"""Turn a resolved shot list into the finished vertical video.

Each shot becomes a still image with a slow Ken Burns move (alternating
push-in and pull-out so consecutive shots don't feel identical), the shots
are crossfaded together, the whole thing is dimmed slightly so captions
stay legible, then captions are burned in and the narration muxed on.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import config
from core.visuals import Shot


def _run(cmd: list[str], label: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-18:])
        raise RuntimeError(f"{label} failed:\n{tail}")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


# --------------------------------------------------------------- per shot

def _render_clip(shot: Shot, dur: float, index: int,
                 out_path: Path) -> Path:
    """Stock footage -> one clip of the right length.

    Deliberately no zoompan. The footage already moves, and a Ken Burns
    push laid over real camera motion reads as a mistake rather than as
    more motion. -stream_loop covers a clip shorter than its shot, and
    -an drops the stock audio, which would otherwise be mixed under the
    narration.
    """
    W, H = config.WIDTH, config.HEIGHT
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
          f"crop={W}:{H},"
          f"eq=brightness=-{config.SHOT_DIM}:saturation=1.08,"
          f"format=yuv420p")
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-stream_loop", "-1", "-i", str(shot.path),
        "-t", f"{dur:.3f}", "-vf", vf, "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(config.FPS),
        str(out_path),
    ], f"shot {index} clip")
    return out_path


def render_shot(shot: Shot, index: int, out_path: Path) -> Path:
    """One still image -> one moving clip of the right length."""
    dur = shot.duration + config.SHOT_TRANSITION
    if getattr(shot, "is_video", False):
        return _render_clip(shot, dur, index, out_path)
    frames = max(2, int(dur * config.FPS))
    W, H = config.WIDTH, config.HEIGHT

    # Oversample so the zoom never shows interpolation mush.
    sw, sh = W * 2, H * 2

    # Zoom per frame, sized so the full move completes over this shot's
    # own length -- short shots move as far as long ones, which is what
    # keeps fast cuts from looking frozen.
    Z = config.KENBURNS_ZOOM
    rate = Z / max(1, frames)

    # Rotate through four moves so consecutive shots never match.
    mode = index % 4
    if mode == 0:      # push in, centre
        zexpr, xf, yf = f"min(1.0+{rate:.6f}*on,{1 + Z:.3f})", 0.5, 0.5
    elif mode == 1:    # pull out, drift right
        zexpr, xf, yf = f"max({1 + Z:.3f}-{rate:.6f}*on,1.0)", 0.58, 0.5
    elif mode == 2:    # push in, drift down-left
        zexpr, xf, yf = f"min(1.0+{rate:.6f}*on,{1 + Z:.3f})", 0.42, 0.58
    else:              # pull out, drift up
        zexpr, xf, yf = f"max({1 + Z:.3f}-{rate:.6f}*on,1.0)", 0.5, 0.42

    vf = (
        f"scale={sw}:{sh}:force_original_aspect_ratio=increase,"
        f"crop={sw}:{sh},"
        f"zoompan=z='{zexpr}'"
        f":x='iw*{xf:.2f}-(iw/zoom/2)'"
        f":y='ih*{yf:.2f}-(ih/zoom/2)'"
        f":d={frames}:s={W}x{H}:fps={config.FPS},"
        f"eq=brightness=-{config.SHOT_DIM}:saturation=1.08,"
        f"format=yuv420p"
    )

    _run([
        "ffmpeg", "-y", "-v", "error",
        "-loop", "1", "-i", str(shot.path),
        "-t", f"{dur:.3f}", "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(config.FPS),
        str(out_path),
    ], f"shot {index} render")
    return out_path


def _placeholder(index: int, dur: float, out_path: Path) -> Path:
    """Used when a shot's image could not be found. Plain, not decorative."""
    W, H = config.WIDTH, config.HEIGHT
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=0x101418:s={W}x{H}:r={config.FPS}",
        "-t", f"{dur + config.SHOT_TRANSITION:.3f}",
        "-vf", "vignette=PI/5,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
        str(out_path),
    ], f"placeholder {index}")
    return out_path


# ---------------------------------------------------------------- sequence

def build_sequence(shots: list[Shot], workdir: Path) -> Path:
    """Render every shot and crossfade them into one continuous clip."""
    clips: list[tuple[Path, float]] = []

    for i, shot in enumerate(shots):
        dst = workdir / f"clip{i:02d}.mp4"
        if shot.path and Path(shot.path).exists():
            try:
                render_shot(shot, i, dst)
            except RuntimeError as exc:
                # One unreadable image should cost one shot, not the video.
                print(f"    ! shot {i} failed ({str(exc).splitlines()[-1][:60]}); "
                      f"using a plain card")
                _placeholder(i, shot.duration, dst)
        else:
            _placeholder(i, shot.duration, dst)
        clips.append((dst, shot.duration))

    if len(clips) == 1:
        return clips[0][0]

    T = config.SHOT_TRANSITION
    inputs: list[str] = []
    for path, _ in clips:
        inputs += ["-i", str(path)]

    # Mostly dissolves, with an occasional directional wipe so the cutting
    # has some texture. Anything flashier reads as a template.
    TRANSITIONS = ["fade", "fade", "dissolve", "fade",
                   "smoothleft", "fade", "dissolve", "slideup"]

    # Chain xfades. After fading A (len a) into B (len b) the result is
    # a + b - T long, so each offset is the running total minus one T.
    chain: list[str] = []
    label = "0:v"
    cum = clips[0][1] + T
    for i in range(1, len(clips)):
        offset = cum - T
        out = f"x{i}"
        trans = TRANSITIONS[i % len(TRANSITIONS)]
        chain.append(
            f"[{label}][{i}:v]xfade=transition={trans}:duration={T}:"
            f"offset={offset:.3f}[{out}]")
        label = out
        cum = cum + clips[i][1] + T - T

    out_path = workdir / "sequence.mp4"
    _run([
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(chain),
        "-map", f"[{label}]",
        "-c:v", "libx264", "-preset", config.VIDEO_PRESET, "-crf", "21",
        "-pix_fmt", "yuv420p", "-r", str(config.FPS),
        str(out_path),
    ], "crossfade sequence")
    return out_path


# ------------------------------------------------------------------ finish

def _esc(p: Path) -> str:
    return str(p.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


AUDIO_EXTS = (".mp3", ".m4a", ".wav", ".ogg", ".aac")


def _tracks_in(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [p for p in sorted(folder.iterdir())
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS]


def pick_music(topic: str = "") -> Path | None:
    """Choose a track, preferring one that suits the topic.

    assets/music/<topic>/ is used when it exists, otherwise the top level.
    A brooding true-crime bed under a story about coffee is worse than no
    music at all, so it is worth keeping a few folders.
    """
    if not config.MUSIC_ENABLED or not config.MUSIC_DIR.exists():
        return None

    import random
    if topic:
        scoped = _tracks_in(config.MUSIC_DIR / topic)
        if scoped:
            return random.choice(scoped)

    tracks = _tracks_in(config.MUSIC_DIR)
    return random.choice(tracks) if tracks else None


def mix_audio(narration: Path, music: Path | None, total: float,
              out_path: Path) -> Path:
    """Mix narration with ducked background music.

    sidechaincompress drives the music level from the narration signal, so
    the bed drops automatically whenever the voice is speaking and comes
    back up in the gaps. That reads far better than a fixed low volume.
    """
    if music is None:
        return narration

    filt = (
        # Narration: pad to full length so the music doesn't outlast it.
        f"[0:a]aresample=44100,apad=whole_dur={total:.2f}[voice];"
        f"[voice]asplit=2[voice_out][voice_key];"
        # Music: loop, trim, fade, and drop to the configured bed level.
        f"[1:a]aresample=44100,aloop=loop=-1:size=2e9,"
        f"atrim=0:{total:.2f},asetpts=N/SR/TB,"
        f"volume={config.MUSIC_GAIN},"
        f"afade=t=in:st=0:d={config.MUSIC_FADE},"
        f"afade=t=out:st={max(0.0, total - config.MUSIC_FADE):.2f}"
        f":d={config.MUSIC_FADE}[bed];"
        # Duck the bed against the voice.
        f"[bed][voice_key]sidechaincompress="
        f"threshold=0.03:ratio={config.MUSIC_DUCK_RATIO}:"
        f"attack=8:release=320[ducked];"
        f"[voice_out][ducked]amix=inputs=2:duration=first:"
        f"dropout_transition=0:normalize=0[out]"
    )

    _run([
        "ffmpeg", "-y", "-v", "error",
        "-i", str(narration), "-i", str(music),
        "-filter_complex", filt, "-map", "[out]",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        str(out_path),
    ], "audio mix")
    return out_path


def end_card(out_path: Path, seconds: float) -> Path:
    """A short follow card to close on."""
    W, H = config.WIDTH, config.HEIGHT
    lines = config.CTA_CARD.split("\\n") if "\\n" in config.CTA_CARD \
        else config.CTA_CARD.split("\n")

    draw = []
    y0 = H // 2 - (len(lines) * 70) // 2
    for i, line in enumerate(lines):
        safe = line.replace("'", "’").replace(":", "\\:").replace("%", "\\%")
        size = 104 if i == 0 else 60
        bold = "-Bold" if i == 0 else ""
        draw.append(
            f"drawtext=text='{safe}'"
            f":fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans{bold}.ttf"
            f":fontsize={size}:fontcolor=white"
            f":x=(w-text_w)/2:y={y0 + i * 140}")

    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=0x0d1117:s={W}x{H}:r={config.FPS}",
        "-t", f"{seconds:.2f}",
        "-vf", ",".join(draw + ["vignette=PI/5", "format=yuv420p"]),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ], "end card")
    return out_path


FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# DejaVu Sans Bold runs about this fraction of the point size per character
# at these sizes. Used only to choose a size that fits -- ffmpeg does the
# real layout, and the margin below leaves room for the estimate being off.
_CHAR_W = 0.62
_COVER_USABLE_W = config.WIDTH - 140


def _escape_drawtext(text: str) -> str:
    """Escape for ffmpeg drawtext. Colons, quotes, percent and backslash all
    change the filter's meaning, and one stray character fails the render."""
    return (text.replace("\\", "")
                .replace("'", "\u2019")
                .replace(":", "\\:")
                .replace("%", "\\%")
                .replace("[", "(").replace("]", ")")
                .replace(",", "\\,"))


def wrap_cover(text: str, size: int,
               max_lines: int = config.COVER_MAX_LINES) -> list[str] | None:
    """Greedy word wrap. None if it cannot fit at this size."""
    per_line = max(1, int(_COVER_USABLE_W / (size * _CHAR_W)))
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(w) > per_line:
            return None                     # a single word overflows
        trial = f"{cur} {w}".strip()
        if len(trial) <= per_line:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) > max_lines:
                return None
    if cur:
        lines.append(cur)
    return lines if 0 < len(lines) <= max_lines else None


def fit_cover(text: str) -> tuple[list[str], int] | None:
    """Biggest size at which the phrase fits. None if there is nothing to draw."""
    text = " ".join((text or "").split()).upper()
    if not text:
        return None
    for size in config.COVER_SIZES:
        lines = wrap_cover(text, size)
        if lines:
            return lines, size
    return None


def cover_filters(text: str, start_label: str, out_label: str) -> list[str]:
    """drawtext filters painting the scroll-stopper over the opening.

    Each line gets its own drawtext with a dark box behind it, so it stays
    readable over any image, and all of them fade out together.
    """
    fitted = fit_cover(text)
    if not fitted:
        return []
    lines, size = fitted
    hold = max(0.6, config.COVER_SECONDS)
    fade_from = max(0.1, hold - config.COVER_FADE)
    alpha = (f"if(lt(t,{fade_from:.2f}),1,"
             f"max(0,1-(t-{fade_from:.2f})/{config.COVER_FADE:.2f}))")

    step = int(size * 1.26)
    # Centre the whole block rather than each line, so two lines and three
    # lines both sit around the same optical middle.
    if str(getattr(config, "COVER_ALIGN", "top")).lower() in (
            "middle", "center", "centre"):
        y0 = max(140, (config.HEIGHT - len(lines) * step) // 2)
    else:
        y0 = config.COVER_TOP
    chain, last = [], start_label
    for i, line in enumerate(lines):
        tag = f"cov{i}"
        nxt = out_label if i == len(lines) - 1 else tag
        chain.append(
            f"[{last}]drawtext=text='{_escape_drawtext(line)}'"
            f":fontfile={FONT_BOLD}"
            f":fontsize={size}:fontcolor=white"
            f":borderw=3:bordercolor=black@0.85"
            f":box=1:boxcolor=black@0.42:boxborderw=18"
            f":alpha='{alpha}'"
            f":enable='lt(t,{hold:.2f})'"
            f":x=(w-text_w)/2:y={y0 + i * step}[{nxt}]")
        last = nxt
    return chain


def finish(sequence: Path, audio: Path, ass_file: Path, out_path: Path,
           kicker: str | None = None, music: Path | None = None,
           workdir: Path | None = None, cover: str = "") -> Path:
    """Burn captions over the shot sequence, add music, append the card."""
    dur = probe_duration(audio)
    if dur <= 0:
        raise RuntimeError(f"narration has no duration: {audio}")
    total = dur + 0.6

    if music is not None and workdir is not None:
        audio = mix_audio(audio, music, total, workdir / "mixed.m4a")

    chain = [f"[0:v]trim=0:{total:.2f},setpts=PTS-STARTPTS[bg]"]
    last = "bg"

    if kicker:
        safe = (kicker.replace("\\", "").replace(":", "\\:")
                      .replace("'", "’").replace("%", "\\%"))
        chain.append(
            f"[{last}]drawtext=text='{safe}'"
            f":fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            f":fontsize=44:fontcolor=white@0.93"
            f":box=1:boxcolor=black@0.5:boxborderw=20"
            f":x=(w-text_w)/2:y=215[kick]")
        last = "kick"

    # Built as a function of one flag, so the no-cover fallback is the
    # identical chain this pipeline used before the feature existed --
    # not a cover chain with pieces unpicked from it.
    base = list(chain)
    base_last = last

    def _chain(with_cover: bool) -> list[str]:
        out, tail = list(base), base_last
        if with_cover:
            cov = cover_filters(cover, tail, "cov")
            if cov:
                out += cov
                tail = "cov"
        out.append(f"[{tail}]ass='{_esc(ass_file)}'[v]")
        return out

    want_cover = bool(cover) and config.COVER_ENABLED and bool(
        cover_filters(cover, base_last, "cov"))

    body_path = (workdir / "body.mp4") if workdir else out_path

    def _assemble(filters: list[str]) -> None:
        _run([
            "ffmpeg", "-y", "-v", "error",
            "-stream_loop", "-1", "-i", str(sequence),
            "-i", str(audio),
            "-filter_complex", ";".join(filters),
            "-map", "[v]", "-map", "1:a",
            "-t", f"{total:.2f}",
            "-c:v", "libx264", "-preset", config.VIDEO_PRESET, "-crf", "23",
            "-maxrate", "6M", "-bufsize", "12M",
            "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
            "-r", str(config.FPS),
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            str(body_path),
        ], "final assembly")

    if want_cover:
        try:
            _assemble(_chain(True))
            print(f"  cover: {' '.join(cover.split()).upper()[:60]}")
        except Exception as exc:
            # The reel matters more than the cover.
            print(f"  ! cover overlay failed ({str(exc)[:140]})")
            print(f"  ! rendering without it")
            _assemble(_chain(False))
    else:
        _assemble(_chain(False))

    if not config.CTA_CARD.strip() or workdir is None:
        if body_path != out_path:
            body_path.replace(out_path)
        return out_path

    # Concat the follow card on. Re-encoding both through the same settings
    # avoids the stream-mismatch errors the concat demuxer throws otherwise.
    card = end_card(workdir / "endcard.mp4", config.CTA_CARD_SECONDS)
    silent = workdir / "endcard_a.m4a"
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
          "-i", "anullsrc=r=44100:cl=stereo",
          "-t", f"{config.CTA_CARD_SECONDS:.2f}",
          "-c:a", "aac", "-b:a", "160k", str(silent)], "end card audio")

    card_av = workdir / "endcard_av.mp4"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(card), "-i", str(silent),
          "-c:v", "libx264", "-preset", config.VIDEO_PRESET, "-crf", "23",
          "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
          "-r", str(config.FPS),
          "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
          "-shortest", str(card_av)], "end card mux")

    listing = workdir / "concat.txt"
    listing.write_text(f"file '{body_path.resolve()}'\nfile '{card_av.resolve()}'\n")
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
          "-i", str(listing), "-c", "copy", "-movflags", "+faststart",
          str(out_path)], "append end card")
    return out_path
