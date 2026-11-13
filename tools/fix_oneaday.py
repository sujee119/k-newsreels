#!/usr/bin/env python3
"""One reel a day, without losing the retries.

Each reels repo has five cron slots a day and the gate only stands any
of them down for the two catch-up slots, so a good day produces up to
five reels.

The obvious fix -- delete four crons -- is the wrong one. A slot that
finds no story builds nothing, and with a single slot a dud day is a
day with no video at all. The feeds have been thin enough for that to
matter: science went 19 hours with nothing on 25 Oct, and a run on 28
Oct died with every script provider down.

So keep all five slots as ATTEMPTS and cap the OUTPUT instead. The
first slot that finds the last reel older than MIN_HOURS_BETWEEN
builds; the rest stand down. A slot that builds nothing writes no
manifest, so the gap keeps growing and the next slot tries again. One
reel a day, five chances to get it.

20 hours rather than 24 so the daily slot does not drift later and
later; the cap is measured from the last reel, not from the clock.

A manual `gh workflow run` always builds, so you can still force one.

Shorts needs no patch: its build.yml already has DAILY_MIN/DAILY_MAX,
seeded from the date. Setting both to 1 gives exactly one a day.

    python3 tools/fix_oneaday.py
"""
import pathlib
import sys

OLD = '''          SCHED="${{ github.event.schedule }}"
          case "$SCHED" in
            "17  1 * * *"|"17 1 * * *"|"17 13 * * *")
              AGE=$(python -u run.py hours-since-build)
              echo "last reel was ${AGE}h ago"
              if python -c "import sys; sys.exit(0 if float('$AGE') >= 7 else 1)"; then
                echo "catch-up: nothing recent, building"
                echo "go=yes" >> "$GITHUB_OUTPUT"
              else
                echo "catch-up: a build landed ${AGE}h ago, standing down"
                echo "go=no" >> "$GITHUB_OUTPUT"
              fi ;;
            *)
              echo "go=yes" >> "$GITHUB_OUTPUT" ;;
          esac
'''

NEW = '''          # ONE REEL A DAY, five chances to get it.
          #
          # Every cron slot is an attempt, not a build. The first slot
          # that finds the last reel older than the cap builds; the rest
          # stand down. A slot that finds no story writes no manifest,
          # so the gap keeps growing and the next slot tries again --
          # which is why the cap is measured from the last REEL and not
          # from the clock. Cutting the crons to one instead would make
          # a storyless slot cost the whole day.
          #
          # 20h, not 24h, so the daily slot does not drift later each
          # day. Tune without touching code:
          #   gh variable set MIN_HOURS_BETWEEN --body 20
          MIN_H="${{ vars.MIN_HOURS_BETWEEN || '20' }}"
          if [ -z "${{ github.event.schedule }}" ]; then
            echo "manual run -- building regardless of the daily cap"
            echo "go=yes" >> "$GITHUB_OUTPUT"
          else
            AGE=$(python -u run.py hours-since-build)
            echo "last reel was ${AGE}h ago, cap is ${MIN_H}h"
            if python -c "import sys; sys.exit(0 if float('$AGE') >= float('$MIN_H') else 1)"; then
              echo "past the cap -- building"
              echo "go=yes" >> "$GITHUB_OUTPUT"
            else
              echo "already have one inside the cap -- standing down"
              echo "go=no" >> "$GITHUB_OUTPUT"
            fi
          fi
'''


def main() -> None:
    root = pathlib.Path(".")
    p = root / ".github/workflows/build.yml"
    if not p.exists():
        sys.exit("run from a channel repo")
    if not (root / "core/publish.py").exists():
        sys.exit("reels repos only -- shorts uses DAILY_MIN/DAILY_MAX, "
                 "which are variables, not code")
    src = p.read_text()
    if "ONE REEL A DAY" in src:
        print("  build.yml: already patched")
        return
    if src.count(OLD) != 1:
        sys.exit(f"  build.yml: gate not in the expected shape "
                 f"({src.count(OLD)} matches) -- NOT TOUCHED")
    new = src.replace(OLD, NEW)
    try:
        import yaml
        doc = yaml.safe_load(new)
        steps = [s for j in doc["jobs"].values() for s in j["steps"]]
        gate = [s for s in steps if s.get("id") == "gate"]
        if len(gate) != 1:
            sys.exit(f"  gate step count {len(gate)} -- NOT WRITTEN")
        crons = (doc.get(True) or doc.get("on") or {}).get("schedule") or []
        if not crons:
            sys.exit("  crons disappeared -- NOT WRITTEN")
        print(f"  {len(crons)} cron slot(s) kept, all as attempts")
    except ImportError:
        pass
    p.write_text(new)
    print("  build.yml: patched")
    print("""
  Also cap reels per RUN (currently 2 on science):

      gh variable set REELS_PER_RUN --body 1 -R sujee119/<repo>

  Without that, the one slot that builds still builds two.
""")


if __name__ == "__main__":
    main()
