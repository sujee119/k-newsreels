#!/usr/bin/env python3
"""Wire the new narration engines into this repo's build workflow.

The config defaults alone are not enough: build.yml sets TTS_PROVIDERS
explicitly, so the workflow's value wins on the runner. This puts gemini
and azure at the front of that list and passes their settings through.

GEMINI_API_KEY is already passed for the script step, so the gemini voice
needs no new secret -- only the voice/model/style variables, all of which
have working defaults.

Safe on either repo and safe to run twice: it keeps that repo's own cron
times and does nothing if the change is already in place.

    python3 tools/apply_short_reels.py
"""
import pathlib
import re
import sys

NEW_LIST = "'gemini,azure,edge,gtts,espeak'"
# Any earlier state of this line, oldest first.
OLD_LISTS = ["'edge,gtts,espeak'", "'azure,edge,gtts,espeak'"]

EXTRA_ENV = """          # Narration engines, in order. gemini runs on GEMINI_API_KEY above --
          # no second account, and Google lists its TTS models as free on
          # the free tier. azure is the same neural voices edge borrows but
          # on the documented endpoint; it needs AZURE_SPEECH_KEY and is
          # skipped silently when that is unset. Either way the chain still
          # falls through to edge, gtts and espeak exactly as before.
          GEMINI_TTS_MODEL: ${{ vars.GEMINI_TTS_MODEL || 'gemini-3.8-flash-tts' }}
          GEMINI_TTS_VOICE: ${{ vars.GEMINI_TTS_VOICE || 'Orus' }}
          GEMINI_TTS_STYLE: ${{ vars.GEMINI_TTS_STYLE }}
          AZURE_SPEECH_KEY: ${{ secrets.AZURE_SPEECH_KEY }}
          AZURE_SPEECH_REGION: ${{ vars.AZURE_SPEECH_REGION || 'koreacentral' }}
"""

MARKER = "GEMINI_TTS_VOICE"


def main() -> None:
    p = pathlib.Path(".github/workflows/build.yml")
    if not p.exists():
        sys.exit("run this from the repo root -- no .github/workflows/build.yml here")

    text = p.read_text()
    notes = []

    if MARKER in text and NEW_LIST in text:
        print("already wired, nothing to do")
        return

    # 1. the provider chain.
    if NEW_LIST in text:
        notes.append("TTS_PROVIDERS already correct")
    else:
        for old in OLD_LISTS:
            if old in text:
                text = text.replace(old, NEW_LIST)
                notes.append(f"TTS_PROVIDERS: {old} -> {NEW_LIST}")
                break
        else:
            sys.exit("could not find the TTS_PROVIDERS default in build.yml -- "
                     "the workflow has changed shape, so stopping rather than "
                     "guessing. Current line:\n  " + "\n  ".join(
                         l.strip() for l in text.splitlines()
                         if "TTS_PROVIDERS" in l) or "  (none found)")

    # 2. the voice settings, anchored to the line that needs them.
    if MARKER not in text:
        m = re.search(r"^[ \t]*TTS_PROVIDERS:.*\n", text, re.M)
        if not m:
            sys.exit("no TTS_PROVIDERS line to anchor the voice settings to")
        # Drop an older Azure-only block if a previous run added one, so the
        # file does not end up with the same keys twice.
        text = re.sub(
            r"^[ \t]*# Azure Speech:.*?AZURE_SPEECH_REGION:.*\n",
            "", text, flags=re.S | re.M)
        m = re.search(r"^[ \t]*TTS_PROVIDERS:.*\n", text, re.M)
        text = text[:m.end()] + EXTRA_ENV + text[m.end():]
        notes.append("passed the gemini and azure voice settings through")

    try:
        import yaml
        doc = yaml.safe_load(text)
        assert doc, "empty document"
        notes.append("YAML still parses")
    except ImportError:
        notes.append("not validated (pip install pyyaml to check here)")
    except Exception as exc:
        sys.exit(f"the edit broke the YAML, so nothing was written: {exc}")

    # Guard against the duplicate-key case YAML would silently accept.
    for key in ("GEMINI_TTS_VOICE", "AZURE_SPEECH_KEY", "TTS_PROVIDERS"):
        n = len(re.findall(rf"^\s*{key}:", text, re.M))
        if n != 1:
            sys.exit(f"{key} appears {n} times after the edit; nothing written")

    p.write_text(text)
    for n in notes:
        print(f"  {n}")


if __name__ == "__main__":
    main()
