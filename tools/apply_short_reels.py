#!/usr/bin/env python3
"""Wire Azure Speech into this repo's build workflow.

The config default alone is not enough: build.yml sets TTS_PROVIDERS
explicitly, so the workflow's value wins on the runner. This adds azure to
the front of that list and passes the key and region through.

Safe on either repo and safe to run twice -- it keeps that repo's own cron
times and does nothing if the change is already in place.

    python3 tools/apply_short_reels.py
"""
import pathlib
import re
import sys

OLD_LIST = "'edge,gtts,espeak'"
NEW_LIST = "'azure,edge,gtts,espeak'"

AZURE_ENV = """          # Azure Speech: the same neural voices edge-tts borrows, but on the
          # documented endpoint, so no 403 from GitHub's runner IPs. Unset
          # key means the engine reports itself unavailable and the chain
          # falls through to edge exactly as before.
          AZURE_SPEECH_KEY: ${{ secrets.AZURE_SPEECH_KEY }}
          AZURE_SPEECH_REGION: ${{ vars.AZURE_SPEECH_REGION || 'koreacentral' }}
"""


def main() -> None:
    p = pathlib.Path(".github/workflows/build.yml")
    if not p.exists():
        sys.exit("run this from the repo root -- no .github/workflows/build.yml here")

    text = p.read_text()
    before = text
    notes = []

    if "AZURE_SPEECH_KEY" in text and NEW_LIST in text:
        print("already wired, nothing to do")
        return

    # 1. azure goes to the front of the provider chain.
    if OLD_LIST in text:
        text = text.replace(OLD_LIST, NEW_LIST)
        notes.append("added azure to TTS_PROVIDERS")
    elif NEW_LIST in text:
        notes.append("TTS_PROVIDERS already lists azure")
    else:
        sys.exit(f"could not find {OLD_LIST} in build.yml -- the workflow has "
                 f"changed shape, so stopping rather than guessing")

    # 2. the key and region, right after the provider line that needs them.
    if "AZURE_SPEECH_KEY" not in text:
        m = re.search(r"^[ \t]*TTS_PROVIDERS:.*\n", text, re.M)
        if not m:
            sys.exit("no TTS_PROVIDERS line to anchor the Azure env to")
        text = text[:m.end()] + AZURE_ENV + text[m.end():]
        notes.append("passed AZURE_SPEECH_KEY and AZURE_SPEECH_REGION through")

    if text == before:
        print("nothing changed")
        return

    try:
        import yaml
        yaml.safe_load(text)
        notes.append("YAML still parses")
    except ImportError:
        notes.append("not validated (pip install pyyaml to check here)")
    except Exception as exc:
        sys.exit(f"the edit broke the YAML, so nothing was written: {exc}")

    p.write_text(text)
    for n in notes:
        print(f"  {n}")


if __name__ == "__main__":
    main()
