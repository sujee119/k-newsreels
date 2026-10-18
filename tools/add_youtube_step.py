#!/usr/bin/env python3
"""Insert the YouTube upload step into this repo's build.yml.

Safe to run on either repo: it edits in place, keeps that repo's own cron
times, and does nothing if the step is already there.

    python tools/add_youtube_step.py
"""
import pathlib
import sys

STEP = """      # After Instagram, never before. Returns 0 even on failure so a
      # YouTube problem cannot turn a successful post into a red run.
      - name: Upload to YouTube
        if: ${{ !cancelled() && steps.gate.outputs.go == 'yes' }}
        continue-on-error: true
        env:
          YT_ENABLED: ${{ vars.YT_ENABLED || '0' }}
          YT_CLIENT_ID: ${{ secrets.YT_CLIENT_ID }}
          YT_CLIENT_SECRET: ${{ secrets.YT_CLIENT_SECRET }}
          YT_REFRESH_TOKEN: ${{ secrets.YT_REFRESH_TOKEN }}
          YT_PRIVACY: ${{ vars.YT_PRIVACY || 'public' }}
          YT_CATEGORY_ID: ${{ vars.YT_CATEGORY_ID || '28' }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python -u run.py youtube

"""

ANCHOR = "      - name: Commit state"


def main() -> None:
    p = pathlib.Path(".github/workflows/build.yml")
    if not p.exists():
        sys.exit("run this from the repo root -- no .github/workflows/build.yml here")
    s = p.read_text()
    if "Upload to YouTube" in s:
        print("already present, nothing to do")
        return
    if ANCHOR not in s:
        sys.exit("could not find the 'Commit state' step to insert before")
    p.write_text(s.replace(ANCHOR, STEP + ANCHOR, 1))

    try:
        import yaml
        yaml.safe_load(p.read_text())
        print("inserted, and the YAML still parses")
    except ImportError:
        print("inserted (install pyyaml if you want it validated here)")
    except Exception as exc:
        sys.exit(f"inserted but the YAML broke: {exc}")


if __name__ == "__main__":
    main()
