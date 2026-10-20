#!/usr/bin/env python3
"""Make the pipeline stop failing quietly.

Every real outage this week shared one shape: something broke, the run
reported success, and nobody found out until a channel had been silent
for most of a day. This closes the three remaining instances.

1. A FAILED RELEASE UPLOAD WENT GREEN. The upload loop ends with

       [ "$FAILED" = "0" ] || echo "::warning::some reels did not publish"

   A ::warning:: neither fails the step nor tells anyone. When the upload
   fails, `continue` skips set-video-url, the manifest keeps
   video_url="", and autopublish prints "skip (never uploaded)" on every
   run from then on, records no error, and the run stays green. That is
   how two reels sat parked from 19 Oct. The step still must not fail --
   one bad reel must not stop the others -- so instead it now retires the
   affected manifests and sends an alert.

2. reconcile AND poll FAILURES WERE DISCARDED by `|| true`. reconcile is
   the repair path for published-but-pending; poll is what reads an
   Approve or Reject tap. Both could fail on every run invisibly. They
   still must not abort the step, so they now warn loudly instead.

3. THE SHORTS CHANNEL HAD NO WATCHDOG AT ALL. The two reels repos check
   every four hours; isnotwhatyouthink had nothing -- and it is the one
   most likely to go quiet unnoticed, because "nothing to build today"
   exits 0. It gets the same watchdog, plus the two commands that
   watchdog needs (hours-since-build, alert), which it did not have.

Run it in each repo; it applies whichever fixes fit. Safe to re-run.

    python3 tools/fix_silent.py
"""
import ast
import pathlib
import sys

RUN_URL = ("${{ github.server_url }}/${{ github.repository }}"
           "/actions/runs/${{ github.run_id }}")

# ----------------------------------------------------------- reels build.yml

BUILD_EDITS = [(
    '          [ "$FAILED" = "0" ] || echo "::warning::some reels did not publish"\n',
    '''          if [ "$FAILED" != "0" ]; then
            echo "::warning::some reels did not publish"
            # A failed release upload leaves video_url empty, and
            # autopublish then prints "skip (never uploaded)" on every
            # run for ever, records no error, and this run still goes
            # green. That is how two reels sat parked from 19 Oct with
            # nothing to show it. Retire them and say so out loud.
            python3 tools/fix_parked.py --write || true
            python -u run.py alert "Release upload failed for one or more reels. They are now marked failed so they stop being skipped silently. Log: ''' + RUN_URL + '''" || true
          fi
''',
    'if [ "$FAILED" != "0" ]; then'),
(
    "          python -u run.py reconcile || true\n",
    '''          python -u run.py reconcile \\
            || echo "::warning::reconcile failed -- published reels may still read as pending"
''',
    "::warning::reconcile failed"),
(
    "          python -u run.py poll || true\n",
    '''          python -u run.py poll \\
            || echo "::warning::poll failed -- an Approve or Reject tap was not read"
''',
    "::warning::poll failed"),
]

# ------------------------------------------------------------ reels poll.yml

POLL_EDITS = [(
    "          python -u run.py reconcile || true\n",
    '''          python -u run.py reconcile \\
            || echo "::warning::reconcile failed -- published reels may still read as pending"
''',
    "::warning::reconcile failed"),
(
    "            python -u run.py poll || true\n",
    '''            python -u run.py poll \\
              || echo "::warning::poll failed -- an Approve or Reject tap was not read"
''',
    "::warning::poll failed"),
]

# ----------------------------------------------------------- shorts run.py

SHORTS_RUN_EDITS = [(
    """def main() -> int:
""",
    '''def cmd_hours_since_build(args) -> int:
    """Hours since the newest short was built, or 999 if there are none.

    The watchdog's only question. Printed, not returned, so the workflow
    can compare it in shell.
    """
    newest = None
    for mf in PENDING.glob("*.json"):
        try:
            ts = json.loads(mf.read_text()).get("built_at")
            when = dt.datetime.fromisoformat(ts) if ts else None
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            continue
        if when and (newest is None or when > newest):
            newest = when
    if newest is None:
        print("999")
        return 0
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=dt.timezone.utc)
    print(f"{(dt.datetime.now(dt.timezone.utc) - newest).total_seconds() / 3600:.1f}")
    return 0


def cmd_alert(args) -> int:
    """Send a plain Telegram message, so a dead channel is noticed
    without anyone reading Actions logs."""
    from core import telegram
    if not telegram.enabled():
        print("  telegram not configured, skipping alert")
        return 0
    telegram.send_text(args.text)
    print("  alert sent")
    return 0


def main() -> int:
''',
    "def cmd_hours_since_build("),
(
    """                     ("build", cmd_build), ("doctor", cmd_doctor),
                     ("list", cmd_list), ("ab", cmd_ab)):
""",
    """                     ("build", cmd_build), ("doctor", cmd_doctor),
                     ("list", cmd_list), ("ab", cmd_ab),
                     ("hours-since-build", cmd_hours_since_build)):
""",
    '("hours-since-build", cmd_hours_since_build)'),
(
    """    args = ap.parse_args()
    if not getattr(args, "fn", None):
""",
    """    a = sub.add_parser("alert")
    a.add_argument("text")
    a.set_defaults(fn=cmd_alert)

    args = ap.parse_args()
    if not getattr(args, "fn", None):
""",
    'a = sub.add_parser("alert")'),
]

# ------------------------------------------------------- shorts watchdog.yml

WATCHDOG = '''name: Watchdog

# Every other failure mode here reports itself. This one cannot: if the
# channel quietly builds nothing -- a dropped schedule, a rate-limited
# gate, every candidate rejected -- the run still exits 0 and silence
# means both "fine" and "dead for a day".
#
# The reels repos have had this for a while. This channel did not, which
# is why it sat producing nothing on 19 Oct without anyone noticing.
#
# Minute :07 deliberately: the other two repos check at :23 and :53, and
# three repos checking in the same minute would collide.
on:
  schedule:
    - cron: "7 0 * * *"        # 09:07 Seoul
    - cron: "7 4 * * *"        # 13:07 Seoul
    - cron: "7 8 * * *"        # 17:07 Seoul
    - cron: "7 12 * * *"       # 21:07 Seoul
    - cron: "7 16 * * *"       # 01:07 Seoul
    - cron: "7 20 * * *"       # 05:07 Seoul
  workflow_dispatch:

permissions:
  contents: read
  actions: write               # to dispatch a catch-up build

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - run: pip install -q requests

      # 16h, not the reels' 10: only 4-8 of 8 slots post on any given day,
      # so a run of unchosen slots can legitimately leave a 12h gap.
      - name: Has this channel built anything?
        id: check
        env:
          MAX_QUIET_HOURS: ${{ vars.MAX_QUIET_HOURS || '16' }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          AGE=$(python -u run.py hours-since-build)
          echo "last short built ${AGE}h ago (limit ${MAX_QUIET_HOURS}h)"
          if python -c "import os,sys; sys.exit(0 if float('$AGE') > float(os.environ['MAX_QUIET_HOURS']) else 1)"; then
            echo "stale=yes" >> "$GITHUB_OUTPUT"
            python -u run.py alert "No short has been built for ${AGE}h. Starting a catch-up build. If this repeats, check the gate: a rate-limited or over-strict gate approves nothing and the run still exits 0." || true
          else
            echo "stale=no" >> "$GITHUB_OUTPUT"
            echo "healthy, saying nothing"
          fi

      # Noticing is not enough -- close the gap too. A manual dispatch is
      # slot 0, which builds regardless of today's chosen slots.
      - name: Kick a build
        if: steps.check.outputs.stale == 'yes'
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh workflow run build.yml --repo "${{ github.repository }}"

      - name: Tell me the watchdog itself broke
        if: failure() || cancelled()
        continue-on-error: true
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          python -u run.py alert "Shorts watchdog failed. Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}" || true
'''


def apply(path: pathlib.Path, edits: list, check_python: bool = False) -> str:
    if not path.exists():
        return f"  {path}: not here, skipped"
    src = original = path.read_text()
    applied = 0

    for old, new, marker in edits:
        if marker in src:
            continue
        n = src.count(old)
        if n == 0:
            return f"  {path}: expected text missing -- NOT TOUCHED"
        if n > 1:
            return f"  {path}: target appears {n}x -- NOT TOUCHED"
        src = src.replace(old, new)
        applied += 1

    if applied == 0:
        return f"  {path}: already patched"

    if check_python:
        try:
            ast.parse(src)
        except SyntaxError as exc:
            return f"  {path}: would not parse ({exc}) -- NOT WRITTEN"
    else:
        try:
            import yaml
            doc = yaml.safe_load(src)
            for job, body in ((doc or {}).get("jobs") or {}).items():
                for i, step in enumerate((body or {}).get("steps") or [], 1):
                    if not isinstance(step, dict) or not (
                            "run" in step or "uses" in step):
                        return (f"  {path}: {job} step {i} would have neither "
                                f"run nor uses -- NOT WRITTEN")
        except ImportError:
            pass
        except Exception as exc:
            return f"  {path}: would not parse as YAML ({exc}) -- NOT WRITTEN"

    replaced = {l for old, _, _ in edits for l in old.splitlines()}
    lost = {l for l in set(original.splitlines()) - set(src.splitlines())
            if l.strip()} - replaced
    if lost:
        return f"  {path}: would drop {len(lost)} unrelated line(s) -- NOT WRITTEN"

    path.write_text(src)
    return f"  {path}: patched {applied} place(s)"


def main() -> None:
    root = pathlib.Path(".")
    shorts = (root / "core/gate.py").exists()
    reels = (root / "core/publish.py").exists()
    if not (shorts or reels):
        sys.exit("run this from one of the three repo roots")

    print("making silent failures loud:")

    if reels:
        if not (root / "tools/fix_parked.py").exists():
            print("  ! tools/fix_parked.py is missing -- extract the earlier "
                  "fix-parked tarball first, or the new alert path cannot "
                  "retire anything")
        print(apply(root / ".github/workflows/build.yml", BUILD_EDITS))
        print(apply(root / ".github/workflows/poll.yml", POLL_EDITS))

    if shorts:
        print(apply(root / "run.py", SHORTS_RUN_EDITS, check_python=True))
        wd = root / ".github/workflows/watchdog.yml"
        if wd.exists():
            print(f"  {wd}: already there, left alone")
        else:
            wd.write_text(WATCHDOG)
            print(f"  {wd}: created (6 checks a day at :07)")


if __name__ == "__main__":
    main()
