#!/usr/bin/env python3
"""Render the same line in several Gemini voices so you can pick one.

Google's voice list gives personality labels but no genders, so the only
way to know which sounds like a male news presenter is to listen.

    set -a && source .env && set +a
    python3 tools/voice_audition.py
    python3 tools/voice_audition.py Charon Fenrir Gacrux     # specific ones

Writes out/voices/<Voice>.mp3 and prints the paths. Pick one, then:

    gh variable set GEMINI_TTS_VOICE --body Orus -R sujee119/news-reels
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config                                      # noqa: E402
from core import tts                               # noqa: E402

# Candidates, not conclusions -- these are the ones whose documented
# labels ("Firm", "Mature", "Upbeat") suggest they might carry a news
# read. Listen before believing any of it.
CANDIDATES = ["Orus", "Charon", "Fenrir", "Alnilam", "Gacrux", "Puck"]

LINE = ("Astronomers just found a planet where it rains glass sideways. "
        "And the way they worked it out is stranger than the planet.")


def main() -> None:
    if not config.GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY is not set. Run:\n"
                 "    set -a && source .env && set +a")

    voices = sys.argv[1:] or CANDIDATES
    out_dir = config.OUT_DIR / "voices"
    out_dir.mkdir(parents=True, exist_ok=True)

    original = config.GEMINI_TTS_VOICE
    ok, failed = [], []
    try:
        for i, voice in enumerate(voices):
            # 3 requests a minute on the free tier, so pace them rather
            # than burning two thirds of the list on 429s.
            if i:
                time.sleep(21)
            config.GEMINI_TTS_VOICE = voice
            path = out_dir / f"{voice}.mp3"
            try:
                tts._engine_gemini(LINE, path)
            except Exception as exc:
                failed.append((voice, str(exc)[:120]))
                print(f"  x {voice}: {str(exc)[:120]}")
                continue
            ok.append(path)
            print(f"  ok {voice}  ->  {path}")
    finally:
        config.GEMINI_TTS_VOICE = original

    print()
    if ok:
        print(f"{len(ok)} written to {out_dir}")
        print("Listen, then set the winner:")
        print("    gh variable set GEMINI_TTS_VOICE --body <Voice> "
              "-R sujee119/news-reels")
    if failed and not ok:
        sys.exit("every voice failed -- the key or the model name is the "
                 "problem, not the voices")


if __name__ == "__main__":
    main()
