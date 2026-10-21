#!/usr/bin/env python3
"""Upload the reels that never reached YouTube.

69 videos are live on Instagram; 12 are on YouTube. The rest were
built before the channel binding and YT_ENABLED were sorted, so they
were never offered to YouTube at all -- 46 on science, 11 on Korea.
They are finished: narrated, captioned, covered, and still sitting in
their GitHub releases as reel-<slug>/reel.mp4.

cmd_youtube already does the whole job -- picks published manifests
with no youtube_id, uploads, records the id, sets the cover frame as
the thumbnail -- and bails on one line only:

    reel = config.OUT_DIR / man["slug"] / "reel.mp4"
    if not reel.exists():
        print(f"  skip (no local file): {man['slug']}")

out/ is gitignored and the runner that built it is long gone, so the
file is never there. Fetch it back from the release and cmd_youtube
does the rest, thumbnail included.

Adds:
  * run.py backfill-slugs N -- the oldest N published reels with no
    youtube_id, one per line.
  * .github/workflows/backfill.yml -- downloads those assets, then
    runs the existing uploader, then commits state.

Paced by hand rather than scheduled. YouTube allows 100 uploads a day
so the cap is not the limit; what matters is that 46 videos arriving
at once on a channel with 6 is a content dump, and you may not want
all of them.

Its own concurrency group, NOT the builds' one: a shared group is how
poll.yml was evicting scheduled builds on 20 Oct. The state push loop
handles any overlap.

    python3 tools/fix_backfill.py
"""
import ast
import pathlib
import sys

RUN_EDITS = [(
    """def cmd_hours_since_build(args) -> int:
""",
    '''def cmd_backfill_slugs(args) -> int:
    """The oldest published reels that never reached YouTube.

    One slug per line, so the workflow can fetch each one's release
    asset back into out/ -- which is the only thing standing between
    cmd_youtube and uploading them.
    """
    rows = []
    for mf in PENDING.glob("*.json"):
        try:
            man = json.loads(mf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if man.get("status") != "published" or man.get("youtube_id"):
            continue
        rows.append((man.get("built_at", ""), man["slug"]))
    rows.sort()
    try:
        limit = max(1, int(getattr(args, "count", 8) or 8))
    except (TypeError, ValueError):
        limit = 8
    for _, slug in rows[:limit]:
        print(slug)
    return 0


def cmd_hours_since_build(args) -> int:
''',
    "def cmd_backfill_slugs(", 1),
(
    '''    s = sub.add_parser("alert")
    s.add_argument("text"); s.set_defaults(fn=cmd_alert)
''',
    '''    s = sub.add_parser("alert")
    s.add_argument("text"); s.set_defaults(fn=cmd_alert)

    s = sub.add_parser("backfill-slugs")
    s.add_argument("count", nargs="?", type=int, default=8)
    s.set_defaults(fn=cmd_backfill_slugs)
''',
    'sub.add_parser("backfill-slugs")', 1),
]

WORKFLOW = '''name: Backfill YouTube

# Reels that were built before the YouTube binding was sorted and so
# were never offered to YouTube. They are finished and still sitting
# in their releases; cmd_youtube skips them only because out/ is
# gitignored and the file is gone.
#
# Manual on purpose. The daily cap is 100 uploads, so that is not the
# constraint -- the constraint is that dumping fifty videos at once on
# a small channel is a content dump, and old news ages differently
# from old science.
on:
  workflow_dispatch:
    inputs:
      count:
        description: "How many to upload this run"
        required: false
        default: "8"

permissions:
  contents: write

# Its OWN group. Sharing the builds' group is exactly how poll.yml was
# cancelling scheduled builds on 20 Oct: GitHub keeps one pending run
# per group and evicts the rest. The state push loop below handles any
# overlap instead.
concurrency:
  group: reel-backfill
  cancel-in-progress: false

jobs:
  backfill:
    runs-on: ubuntu-latest
    timeout-minutes: 90

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: pip install -q requests

      - name: Fetch the videos back from their releases
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          N="${{ github.event.inputs.count || '8' }}"
          SLUGS=$(python -u run.py backfill-slugs "$N")
          if [ -z "$SLUGS" ]; then
            echo "nothing left to backfill"
            exit 0
          fi
          GOT=0
          for SLUG in $SLUGS; do
            mkdir -p "out/$SLUG"
            if gh release download "reel-$SLUG" \\
                 --repo "${{ github.repository }}" \\
                 --pattern reel.mp4 --dir "out/$SLUG" 2>/dev/null; then
              GOT=$((GOT + 1))
              echo "  fetched $SLUG"
            else
              # The release may have been cleaned up. Not an error --
              # that reel simply cannot be recovered.
              echo "  no release asset for $SLUG, skipping"
            fi
          done
          echo "fetched $GOT video(s)"

      - name: Upload them
        env:
          YT_ENABLED: ${{ vars.YT_ENABLED || '0' }}
          YT_CLIENT_ID: ${{ secrets.YT_CLIENT_ID }}
          YT_CLIENT_SECRET: ${{ secrets.YT_CLIENT_SECRET }}
          YT_REFRESH_TOKEN: ${{ secrets.YT_REFRESH_TOKEN }}
          YT_PRIVACY: ${{ vars.YT_PRIVACY || 'public' }}
          YT_CATEGORY_ID: ${{ vars.YT_CATEGORY_ID || '28' }}
          COVER_ENABLED: ${{ vars.COVER_ENABLED || '1' }}
          COVER_SECONDS: ${{ vars.COVER_SECONDS || '3.2' }}
          COVER_THUMB_MS: ${{ vars.COVER_THUMB_MS || '900' }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python -u run.py youtube

      - name: Commit state
        if: always()
        continue-on-error: true
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          BR="${GITHUB_REF_NAME}"
          git add state/
          git diff --staged --quiet && { echo "no state changes"; exit 0; }
          git commit -q -m "backfill $(date -u +%FT%TZ)"
          for attempt in 1 2 3; do
            if git push; then echo "state pushed"; exit 0; fi
            git fetch -q origin "$BR"
            git rebase -X theirs "origin/$BR" || {
              git rebase --abort || true
              git reset --soft "origin/$BR"
              git add state/
              git diff --staged --quiet || git commit -q -m "backfill $(date -u +%FT%TZ)"
            }
          done
          echo "::warning::could not push state after 3 attempts"
'''


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
    if not (root / "core/publish.py").exists():
        sys.exit("the two reels repos only (scidaily-reels, k-newsreels)")
    print("backfilling YouTube:")
    print(apply(root / "run.py", RUN_EDITS))
    wf = root / ".github/workflows/backfill.yml"
    if wf.exists():
        print(f"  {wf}: already there, left alone")
    else:
        wf.write_text(WORKFLOW)
        print(f"  {wf}: created")
    print("\n  Check what it would do:  python3 run.py backfill-slugs 8")


if __name__ == "__main__":
    main()
