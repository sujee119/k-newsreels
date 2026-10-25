#!/usr/bin/env python3
"""Make the narration voice visible, and stop over-long scripts shipping.

WHAT THE REELS THEMSELVES SAY. Every published reel since 18 Oct was
downloaded and measured -- pitch by autocorrelation, bandwidth by
spectral energy. The break is exact:

    built              secs   F0 Hz   band    engine implied
    18-20 Oct (all)    31-86  102-120 4850-5564  one wideband neural voice
    21 Oct 07:46       34.3   152.4   2693       <-- first bad one
    21 Oct 13:49       36.6   155.3   4590
    22 Oct 01:13       58.1   122.1   2488
    22 Oct 16:24       36.9   117.6   2348
    24 Oct 14:35       47.6   117.6   3267

Before 21 Oct the voice is one thing. After it, bandwidth collapses to
telephone grade and pitch wanders up to 155 Hz. A high F0 over a narrow
band is a female source pitched down -- gtts under VOICE_DEEPEN=0.88 --
which is exactly the robotic female delivery viewers complained about.
Bandwidth also varies reel to reel, so the chain is falling through to a
DIFFERENT depth each time: the leader is unreliable, not merely wrong.

21 Oct is the day TTS_PROVIDERS was deleted from the repo, which moved
science off a pinned "edge,gtts" onto the default chain
"gemini,azure,edge,gtts,espeak".

WHY FOUR DAYS PASSED WITH NOBODY NOTICING. run.py already warns, but it
was written while edge led the chain:

    if engine and engine != "edge":
        print(f"  ! this reel uses the '{engine}' voice because edge-tts
              was unavailable -- quality is lower than usual")

With gemini leading, that fires on EVERY build and blames edge for an
outage that never happened. An alarm that always sounds is an alarm
nobody reads, so a real fall-through to gtts looked exactly like a
normal day. Worse, the engine is printed to a log and never written to
the manifest, so after the run there is no record of which voice spoke.
That is why this had to be diagnosed from the audio.

THE LENGTH LEAK IS SEPARATE. The retry loop flags "Too long at N words"
correctly, but when no attempt comes back clean the best-draft path
below it ships whatever it has:

    if best is not None:
        count, obj, narration, shots = best
        return Script(... narration=narration ...)

-- no ceiling. At MAX_SECONDS=33 and 3.0 words/s the cap is 99 words;
the 22 Oct 01:13 reel ran 58.1s, about 174 words. Ten of the fourteen
reels built since 21 Oct are over the ceiling. Measured syllable rate is
near-constant across all of them, so the voice is NOT running slow --
these are simply long scripts.

Three changes:

  1. run.py records "tts_engine" in the manifest. One grep then answers
     "which voice shipped" for every reel, for good.
  2. The warning compares against the configured chain's actual leader
     and names it, so it fires only on a real fall-through.
  3. script.py trims an over-ceiling best draft from the MIDDLE, keeping
     the hook and the closing payoff, and drops shots whose cues no
     longer appear. Trimming the tail would cut the line that resolves
     the hook, which is the one line the format cannot lose.

Nothing here changes which engine is chosen. That is a repo variable and
the README line at the bottom says how to set it.

    python3 tools/fix_voice.py
"""
import ast
import pathlib
import sys

RUN_EDITS = [
    # 1. the engine goes into the manifest, not just the log
    ('''        "shots_found": f"{found}/{len(shots)}",
''',
     '''        "shots_found": f"{found}/{len(shots)}",
        # Which voice actually spoke. Printed to the log before, and
        # only there -- so when the chain quietly fell through to gtts
        # on 21 Oct there was nothing left afterwards to show it, and it
        # took four days and a spectrum analysis of the published mp4s
        # to find. One field here makes it greppable for ever:
        #   grep -h tts_engine state/pending/*.json | sort | uniq -c
        "tts_engine": engine,
''',
     '"tts_engine"', 1),

    # 2. an alarm that only sounds when something is actually wrong
    ('''    # A fallback engine sounds noticeably worse. Say so once, loudly, rather
    # than quietly shipping a robotic reel and leaving you to wonder.
    if engine and engine != "edge":
        print(f"  ! this reel uses the '{engine}' voice because edge-tts was "
              f"unavailable -- quality is lower than usual")
        try:
            from core import telegram
            if telegram.enabled():
                telegram.send_text(
                    f"Heads up: narration fell back to '{engine}' because "
                    f"edge-tts was refused. The reel still posts, but the "
                    f"voice is worse. Reel: {story.title[:70]}")
        except Exception:
            pass
''',
     '''    # A fallback engine sounds noticeably worse. Say so once, loudly,
    # rather than quietly shipping a robotic reel and leaving you to
    # wonder.
    #
    # Both the test and the message used to be hardcoded to "edge",
    # which was right while TTS_PROVIDERS was pinned to "edge,gtts".
    # Once the chain became "gemini,..." this fired on EVERY build and
    # blamed edge for an outage that had not happened -- console line
    # and Telegram alert alike. The one warning that would have caught
    # the 21 Oct fall-through to gtts was already crying wolf, which is
    # why four days of robotic reels went out unnoticed.
    #
    # Two separate things now. Any engine that is not the chain leader
    # is worth a console line. Only a genuinely last-resort engine --
    # the two that actually sound robotic -- is worth interrupting you.
    _lead = next((n.strip() for n in config.TTS_PROVIDERS.split(",")
                  if n.strip()), "")
    if engine and _lead and engine != _lead:
        print(f"  ! this reel uses the '{engine}' voice, not '{_lead}' -- "
              f"'{_lead}' was unavailable and quality is lower than usual")
    if engine in ("gtts", "espeak"):
        _why = ("gtts is female and gets pitched down to sound male"
                if engine == "gtts" else
                "espeak is formant synthesis, not a neural voice")
        print(f"  ! '{engine}' is a last-resort engine -- {_why}, so this "
              f"reel sounds robotic. Everything above it in "
              f"'{config.TTS_PROVIDERS}' refused.")
        try:
            from core import telegram
            if telegram.enabled():
                telegram.send_text(
                    f"Narration fell all the way to '{engine}' -- {_why}, "
                    f"so this one sounds robotic. Everything above it in "
                    f"'{config.TTS_PROVIDERS}' refused. It still posts. "
                    f"Reel: {story.title[:70]}")
        except Exception:
            pass
''',
     "_lead = next(", 1),
]

SCRIPT_EDITS = [
    # 3. a ceiling the best-draft path cannot walk past
    ('''    # Nothing was perfect. A near-miss draft beats the plain template, so
    # take the best one and say plainly what is still wrong with it.
    if best is not None:
        count, obj, narration, shots = best
        print(f"  ~ no clean draft after {attempts} attempts; using the best "
              f"one ({count} remaining issue(s)) -- review script.txt")
''',
     '''    # Nothing was perfect. A near-miss draft beats the plain template, so
    # take the best one and say plainly what is still wrong with it.
    if best is not None:
        count, obj, narration, shots = best
        print(f"  ~ no clean draft after {attempts} attempts; using the best "
              f"one ({count} remaining issue(s)) -- review script.txt")
        # ...but not at any length. "Too long at N words" was raised as a
        # problem above and then ignored right here, which is how a
        # 174-word script became a 58-second reel on 22 Oct against a
        # 99-word ceiling. Ten of fourteen reels built after 21 Oct were
        # over it.
        narration, shots = _trim_to_ceiling(narration, shots, max_words)
''',
     "_trim_to_ceiling(narration", 1),

    # the helper itself
    ('''def build_caption(script: Script, title: str, link: str,
''',
     '''def _trim_to_ceiling(narration: str, shots: list,
                     max_words: int) -> tuple[str, list]:
    """Bring an over-long draft under the word ceiling.

    Sentences come out of the MIDDLE, never the end. The format is a
    hook that opens a loop and a closing line that pays it off, and
    loop_is_closed() is checked precisely because that payoff is the one
    line the reel cannot ship without. Cutting the tail -- the obvious
    implementation -- would remove it every time.

    Shots carry a "cue" copied verbatim from the narration, so any shot
    whose cue was in a dropped sentence no longer has anything to sync
    to and goes with it.
    """
    words = narration.split()
    if len(words) <= max_words:
        return narration, shots

    parts = re.findall(r"[^.!?]+[.!?]*\\s*", narration)
    parts = [p for p in parts if p.strip()]
    if len(parts) < 3:                     # nothing safe to take out
        print(f"  ! {len(words)} words, over the {max_words} ceiling, but "
              f"it is {len(parts)} sentence(s) -- shipping it whole")
        return narration, shots

    keep = list(parts)
    i = len(keep) - 2                      # last sentence before the payoff
    while sum(len(p.split()) for p in keep) > max_words and len(keep) > 2:
        if i <= 0:                         # never drop the hook
            i = len(keep) - 2
            if i <= 0:
                break
        keep.pop(i)
        i -= 1

    out = "".join(keep).strip()
    n = len(out.split())
    print(f"  ~ trimmed {len(words)} words to {n} ({len(parts) - len(keep)} "
          f"sentence(s) out of the middle); hook and payoff kept")
    if n > max_words:
        # Hook plus payoff alone are over the ceiling. Nothing further can
        # come out without losing one of them, so say so rather than
        # reporting a ceiling that was not actually reached.
        print(f"  ! still {n} words, over the {max_words} ceiling -- the "
              f"hook and the payoff alone exceed it. Shipping at "
              f"~{n / max(0.1, config.WORDS_PER_SECOND):.0f}s.")

    low = out.lower()
    kept_shots = [s for s in shots
                  if str(s.get("cue", "")).strip().lower() in low]
    if len(kept_shots) != len(shots):
        print(f"  ~ dropped {len(shots) - len(kept_shots)} shot(s) whose cue "
              f"was in a trimmed sentence")
    return out, (kept_shots or shots)


def build_caption(script: Script, title: str, link: str,
''',
     "def _trim_to_ceiling", 1),
]


def apply(path: pathlib.Path, edits: list) -> str:
    if not path.exists():
        return f"  {path}: not here, skipped"
    src = original = path.read_text()
    applied = 0
    for old, new, marker, count in edits:
        if marker in src:
            continue
        n = src.count(old)
        if n != count:
            return (f"  {path}: expected {count} occurrence(s), found {n} "
                    f"-- NOT TOUCHED")
        src = src.replace(old, new)
        applied += 1
    if applied == 0:
        return f"  {path}: already patched"
    try:
        ast.parse(src)
    except SyntaxError as exc:
        return f"  {path}: would not parse ({exc}) -- NOT WRITTEN"
    replaced = {l.strip() for old, _, _, _ in edits for l in old.splitlines()}
    lost = {l.strip() for l in set(original.splitlines())
            - set(src.splitlines()) if l.strip()} - replaced
    if lost:
        return f"  {path}: would drop {len(lost)} unrelated line(s) -- NOT WRITTEN"
    path.write_text(src)
    return f"  {path}: patched {applied} place(s)"


def main() -> None:
    root = pathlib.Path(".")
    if not (root / "core/tts.py").exists():
        sys.exit("run this from a reels repo (scidaily-reels or k-newsreels)")

    # run.py reads config.TTS_PROVIDERS for the chain leader; it imports
    # config already in every build, but check rather than assume.
    run = (root / "run.py").read_text()
    if "import config" not in run and "from core import config" not in run:
        print("  ! run.py does not import config -- edit 2 would NameError")
        sys.exit("  NOT TOUCHED")

    print("make the voice visible and cap the script:")
    print(apply(root / "run.py", RUN_EDITS))
    print(apply(root / "core/script.py", SCRIPT_EDITS))
    print("""
  Then pin the voice (this script does NOT change it):

      gh variable set TTS_PROVIDERS --body 'edge,gemini,gtts,espeak' \\
         -R sujee119/news-reels

  Every reel built while the chain was pinned 'edge,gtts' measured
  102-120 Hz over a 4850-5564 Hz band -- a consistent wideband male
  voice. Putting edge back in front restores exactly that, and leaves
  gemini as the backup rather than the leader.

  After the next build:

      grep -h tts_engine state/pending/*.json | sort | uniq -c
""")


if __name__ == "__main__":
    main()
