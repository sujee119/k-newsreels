"""Narration, with more than one way to produce it.

edge-tts is the best free option -- natural voices, no API key, and it
reports word timings we use for caption highlighting. But it talks to an
undocumented Microsoft endpoint that answers 403 to datacenter IPs without
warning, which stopped the whole pipeline on 13 Oct: discovery, script and
shot list all succeeded and the run died here.

So narration now tries engines in order and uses the first that produces
audio. Quality degrades down the chain; a worse voice beats no reel, and
the caller is told which engine was used so it can say so.

  gemini  free on the key the script step already uses, no second account
  azure   the same voices edge uses, but keyed and documented, so no 403
  edge    natural, word timings, breaks when Microsoft feels like it
  gtts    Google Translate's voice, no key, needs network, no timings
  espeak  offline, robotic, cannot fail -- the floor
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import config


@dataclass
class Word:
    text: str
    start: float   # seconds
    end: float


class NarrationFailed(RuntimeError):
    """Every engine failed. Carries what each one said."""


# Which engine produced the last narration, for the caller to report.
last_engine = ""

# Voices to try before giving up on edge-tts. A 403 is usually about the
# endpoint rather than the voice, but a bad or retired voice name looks
# identical from here, so vary it.
_EDGE_FALLBACK_VOICES = [
    "en-US-AndrewMultilingualNeural",
    "en-US-ChristopherNeural",
    "en-GB-RyanNeural",
    "en-US-GuyNeural",
]


# ----------------------------------------------------------------- helpers

def _audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _usable(path: Path) -> bool:
    return path.exists() and path.stat().st_size >= 1000


def _estimate(text: str, total: float) -> list[Word]:
    """Spread words across a known duration, weighted by length.

    Used for engines that return audio but no word boundaries. Captions
    drift slightly but stay readable, which beats no captions at all.
    """
    tokens = text.split()
    if not tokens or total <= 0:
        return []

    weights = []
    for tok in tokens:
        w = len(tok) + 2
        if tok.endswith((".", "!", "?")):
            w += 6
        elif tok.endswith((",", ";", ":")):
            w += 3
        weights.append(w)

    scale = total / sum(weights)
    words, t = [], 0.0
    for tok, w in zip(tokens, weights):
        d = w * scale
        words.append(Word(tok, t, t + d))
        t += d
    return words


def _rate_to_wpm(rate: str) -> int:
    """edge-tts rate strings look like '+8%'. espeak wants words/minute."""
    base = 165
    try:
        pct = int(str(rate).strip().replace("%", "").replace("+", "") or 0)
    except ValueError:
        pct = 0
    return max(80, min(300, int(base * (1 + pct / 100))))


# ----------------------------------------------------------------- engines

def _engine_gemini(text: str, mp3_path: Path) -> list[Word] | None:
    """Gemini TTS, on the key the script step already uses.

    No new account and no card: Google lists these TTS models as free of
    charge on the free tier. Audio comes back as base64 WAV (24kHz mono
    16-bit PCM) inside the Interactions response, which ffmpeg turns into
    the mp3 the rest of the pipeline expects.

    Note this is the Interactions API (/v1beta/interactions), not the
    generateContent shape -- speech moved there, and the old path returns
    nothing useful for audio.

    No word timings and no rate parameter, so timings are estimated and
    _fit_pace does the pacing, exactly as for gtts.
    """
    import base64
    import json as _json
    import urllib.error
    import urllib.request

    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")

    body = _json.dumps({
        "model": config.GEMINI_TTS_MODEL,
        "input": [{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": text,
                "annotations": [{
                    "type": "speech_metadata",
                    "style": config.GEMINI_TTS_STYLE,
                }],
            }],
        }],
        "response_format": {"type": "audio"},
        "generation_config": {
            "speech_config": [{"voice": config.GEMINI_TTS_VOICE}],
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        data=body, headers={
            "x-goog-api-key": config.GEMINI_API_KEY,
            "Content-Type": "application/json",
        })
    # The free tier allows 3 requests a minute. One reel needs exactly one
    # call, so a 429 means something overlapped -- another repo's build, or
    # a voice audition. Waiting is much better than degrading to gtts's
    # female voice for that reel, which is the thing this engine exists to
    # avoid. Any other status fails straight through to the next engine.
    payload = None
    last = ""
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                payload = _json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:220].decode("utf-8", "ignore")
            last = f"returned {exc.code}: {detail}"
            if exc.code == 429 and attempt < 3:
                print(f"  ! gemini rate-limited (3/min on the free tier), "
                      f"waiting 25s -- attempt {attempt} of 3")
                time.sleep(25)
                continue
            raise RuntimeError(last) from exc
    if payload is None:
        raise RuntimeError(last or "gemini returned nothing")

    data = _find_audio(payload)
    if not data:
        raise RuntimeError(
            f"no audio in the response: {str(payload)[:200]}")

    wav = mp3_path.with_suffix(".gemini.wav")
    wav.write_bytes(base64.b64decode(data))
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
             "-codec:a", "libmp3lame", "-q:a", "4", str(mp3_path)],
            check=True, capture_output=True)
    finally:
        wav.unlink(missing_ok=True)

    if not _usable(mp3_path):
        raise RuntimeError("gemini audio did not convert to usable mp3")
    return None                        # timings get estimated


def _find_audio(payload: dict) -> str:
    """Pull the base64 audio out of an Interactions response.

    Walked rather than indexed: the documented path is
    steps[] -> model_output -> content[] -> audio -> data, but the shape
    has already moved once (generateContent -> interactions), so a search
    survives the next rearrangement instead of raising KeyError.
    """
    found = ""

    def walk(node):
        nonlocal found
        if found:
            return
        if isinstance(node, dict):
            if node.get("type") == "audio" and isinstance(node.get("data"), str):
                found = node["data"]
                return
            # output_audio.data is what the SDK exposes
            oa = node.get("output_audio")
            if isinstance(oa, dict) and isinstance(oa.get("data"), str):
                found = oa["data"]
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return found


def _ssml(text: str) -> bytes:
    """SSML for one voice at the configured rate.

    The text is XML-escaped: an unescaped '&' in a headline would make the
    whole request a 400, which would look like an outage rather than a
    quoting bug.
    """
    body = (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))
    voice = config.TTS_VOICE
    lang = "-".join(voice.split("-")[:2]) or "en-US"
    rate = str(config.TTS_RATE or "+0%")
    return (f"<speak version='1.0' xml:lang='{lang}'>"
            f"<voice name='{voice}'>"
            f"<prosody rate='{rate}'>{body}</prosody>"
            f"</voice></speak>").encode("utf-8")


def _azure_token() -> str:
    """Trade the subscription key for a 10-minute token.

    The synthesis endpoint also accepts the raw key, but the token path is
    the documented one and gives a clearer 401 when the key or region is
    wrong -- which is the failure worth diagnosing quickly.
    """
    import urllib.request

    region = config.AZURE_SPEECH_REGION
    url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    req = urllib.request.Request(url, data=b"", headers={
        "Ocp-Apim-Subscription-Key": config.AZURE_SPEECH_KEY,
        "Content-Length": "0",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _engine_azure(text: str, mp3_path: Path) -> list[Word] | None:
    """Azure Speech, the keyed version of the voices edge-tts borrows.

    Same neural models, documented endpoint, no 403 from datacenter IPs.
    Returns no word timings -- the REST endpoint does not emit boundary
    events, so they are estimated from the audio like gTTS. The rate is
    set in SSML, so _fit_pace has almost nothing left to correct.
    """
    import urllib.error
    import urllib.request

    if not config.AZURE_SPEECH_KEY:
        raise RuntimeError("AZURE_SPEECH_KEY is not set")

    region = config.AZURE_SPEECH_REGION
    if not region:
        raise RuntimeError("AZURE_SPEECH_REGION is not set")

    try:
        token = _azure_token()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"token request returned {exc.code} -- "
            f"{'key rejected' if exc.code == 401 else 'check the region slug'} "
            f"(region={region})") from exc

    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    req = urllib.request.Request(url, data=_ssml(text), headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
        "User-Agent": "news-reels",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", "ignore")
        raise RuntimeError(f"synthesis returned {exc.code}: {detail}") from exc

    mp3_path.write_bytes(audio)
    if not _usable(mp3_path):
        raise RuntimeError("azure returned no usable audio")
    return None                        # timings get estimated


async def _edge_once(text: str, mp3_path: Path, voice: str) -> list[Word]:
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=config.TTS_RATE)
    words: list[Word] = []
    with open(mp3_path, "wb") as fh:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # edge-tts reports in 100-nanosecond ticks
                start = chunk["offset"] / 1e7
                dur = chunk["duration"] / 1e7
                words.append(Word(chunk["text"], start, start + dur))
    return words


def _engine_edge(text: str, mp3_path: Path) -> list[Word] | None:
    voices = [config.TTS_VOICE] + [
        v for v in _EDGE_FALLBACK_VOICES if v != config.TTS_VOICE]
    last = None
    for i, voice in enumerate(voices):
        for attempt in (1, 2):
            try:
                words = asyncio.run(_edge_once(text, mp3_path, voice))
            except Exception as exc:
                last = exc
                print(f"  ! edge-tts {voice} attempt {attempt}: "
                      f"{str(exc)[:120]}")
                time.sleep(2.0 * attempt)
                continue
            if _usable(mp3_path):
                if i:
                    print(f"  (fell back to voice {voice})")
                return words
            last = RuntimeError("no audio written")
            break                      # a silent success won't fix on retry
    if last:
        raise last
    raise RuntimeError("edge-tts produced no audio")


def _engine_gtts(text: str, mp3_path: Path) -> list[Word] | None:
    """Google Translate's voice. No key, no word timings."""
    from gtts import gTTS

    gTTS(text=text, lang="en", tld="com").save(str(mp3_path))
    if not _usable(mp3_path):
        raise RuntimeError("gTTS wrote no audio")
    return None                        # timings get estimated


def _engine_espeak(text: str, mp3_path: Path) -> list[Word] | None:
    """Offline fallback. Robotic, but it cannot be rate-limited or blocked."""
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        raise RuntimeError("espeak-ng is not installed")

    txt = mp3_path.with_suffix(".txt")
    wav = mp3_path.with_suffix(".espeak.wav")
    txt.write_text(text, encoding="utf-8")
    subprocess.run(
        [exe, "-v", "en-us+m3", "-s", str(_rate_to_wpm(config.TTS_RATE)),
         "-f", str(txt), "-w", str(wav)],
        check=True, capture_output=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
         "-codec:a", "libmp3lame", "-q:a", "4", str(mp3_path)],
        check=True, capture_output=True)
    for tmp in (txt, wav):
        tmp.unlink(missing_ok=True)
    if not _usable(mp3_path):
        raise RuntimeError("espeak produced no audio")
    return None


_ENGINES = {
    "gemini": _engine_gemini,
    "azure": _engine_azure,
    "edge": _engine_edge,
    "gtts": _engine_gtts,
    "espeak": _engine_espeak,
}

# Engines whose voice is female and unchangeable. gTTS exposes one English
# voice through Google Translate; there is no male option at any tld. For a
# male delivery its output is pitched down after the fact.
_FEMALE_ONLY = {"gtts"}

# Engines that cannot be asked to speak faster. Their pace is corrected
# afterwards instead, which is why WORDS_PER_SECOND is enforced below
# rather than merely requested.
_NO_RATE_CONTROL = {"gtts"}


def _ffmpeg_filter(src: Path, dest: Path, chain: str) -> bool:
    out = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-filter:a", chain, "-codec:a", "libmp3lame", "-q:a", "4",
         str(dest)], capture_output=True, text=True)
    if out.returncode != 0 or not _usable(dest):
        print(f"  ! audio filter failed ({out.stderr.strip()[:120]})")
        return False
    return True


def _atempo_chain(tempo: float) -> str:
    """atempo is only well behaved between 0.5 and 2.0, so chain it."""
    parts, left = [], tempo
    while left > 2.0:
        parts.append("atempo=2.0"); left /= 2.0
    while left < 0.5:
        parts.append("atempo=0.5"); left /= 0.5
    parts.append(f"atempo={left:.4f}")
    return ",".join(parts)


def _sample_rate(path: Path) -> int:
    """The file's real sample rate. asetrate takes an absolute value, so
    assuming one (24000) stretched a 44.1 kHz file to nearly double."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True)
    try:
        return int(out.stdout.strip())
    except ValueError:
        return 44100


def _deepen(mp3_path: Path, factor: float) -> bool:
    """Lower the pitch without changing the pace.

    Resampling drops pitch and slows playback together, so the tempo is
    put back afterwards. Used only for an engine whose voice cannot be
    chosen, to get a male-sounding delivery out of a female-only voice.
    """
    tmp = mp3_path.with_suffix(".deep.mp3")
    sr = _sample_rate(mp3_path)
    chain = (f"asetrate={int(sr * factor)},aresample={sr},"
             f"{_atempo_chain(1.0 / factor)}")
    if not _ffmpeg_filter(mp3_path, tmp, chain):
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(mp3_path)
    return True


def _fit_pace(text: str, mp3_path: Path,
              words: list[Word] | None) -> list[Word] | None:
    """Make the audio actually run at WORDS_PER_SECOND.

    An engine that ignores rate requests produced 119s and 125s reels
    against an 85s target while the config said 3.3 words/sec. Measuring
    the result and correcting it is the only way the setting means
    anything across engines.
    """
    n = len(text.split())
    want = n / max(0.5, config.WORDS_PER_SECOND)
    have = _audio_duration(mp3_path)
    if have <= 0 or want <= 0:
        return words

    tempo = have / want
    if 0.97 <= tempo <= 1.03:
        return words                      # already on pace
    capped = max(0.6, min(2.5, tempo))
    tmp = mp3_path.with_suffix(".paced.mp3")
    if not _ffmpeg_filter(mp3_path, tmp, _atempo_chain(capped)):
        tmp.unlink(missing_ok=True)
        return words
    tmp.replace(mp3_path)
    now = _audio_duration(mp3_path)
    print(f"  paced {have:.1f}s -> {now:.1f}s "
          f"({n / max(0.1, now):.2f} w/s, target {config.WORDS_PER_SECOND})")
    if capped != tempo:
        print(f"  ! wanted {tempo:.2f}x but capped at {capped:.2f}x to keep "
              f"it listenable")
    if words:                             # real timings must be rescaled
        scale = now / have
        for w in words:
            w.start *= scale
            w.end *= scale
    return words


# -------------------------------------------------------------------- api

def speak(text: str, mp3_path: Path) -> list[Word]:
    """Render narration to mp3 and return word-level timings."""
    global last_engine
    last_engine = ""

    text = (text or "").strip()
    if not text:
        raise RuntimeError(
            "Nothing to narrate -- the script came back empty. This usually "
            "means the script provider failed; check the [3/7] step above.")

    order = [n.strip() for n in config.TTS_PROVIDERS.split(",") if n.strip()]
    problems: list[str] = []

    for name in order:
        fn = _ENGINES.get(name)
        if not fn:
            problems.append(f"{name}: unknown engine")
            continue
        try:
            words = fn(text, mp3_path)
        except Exception as exc:
            problems.append(f"{name}: {str(exc)[:160]}")
            print(f"  ! {name} unavailable, trying the next engine")
            continue

        last_engine = name
        if name != order[0]:
            print(f"  ! narration came from '{name}', not '{order[0]}'")

        # A female-only engine is pitched down, because the configured
        # voice is male and a sudden voice change mid-feed is jarring.
        if name in _FEMALE_ONLY and config.VOICE_DEEPEN < 1.0:
            if _deepen(mp3_path, config.VOICE_DEEPEN):
                print(f"  pitched down to {config.VOICE_DEEPEN:.2f} for a "
                      f"male delivery ({name} has no male voice)")

        # Enforce the pace BEFORE deriving timings, so captions match the
        # audio that actually ships.
        words = _fit_pace(text, mp3_path, words)

        if not words:
            total = _audio_duration(mp3_path)
            print(f"  ! no word timings from {name}; estimating from "
                  f"{total:.1f}s of audio")
            words = _estimate(text, total)

        # Close gaps so highlighting never flickers off between words.
        for i in range(len(words) - 1):
            words[i].end = max(words[i].end, words[i + 1].start)
        return words

    raise NarrationFailed(
        "No narration engine worked, so there is no audio to build on:\n  "
        + "\n  ".join(problems)
        + "\nInstall espeak-ng for an offline fallback that cannot be "
          "blocked: sudo apt-get install -y espeak-ng")


def duration(words: list[Word]) -> float:
    return words[-1].end if words else 0.0


if __name__ == "__main__":
    out = config.OUT_DIR / "tts_test.mp3"
    w = speak("Testing one two three. This is a narration check.", out)
    print(f"engine: {last_engine}")
    for x in w:
        print(f"{x.start:6.2f}-{x.end:6.2f}  {x.text}")
    print(f"\n{out} ({out.stat().st_size} bytes), {duration(w):.2f}s")
