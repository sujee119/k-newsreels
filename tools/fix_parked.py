#!/usr/bin/env python3
"""Retire manifests that are pending forever.

A reel whose release upload failed keeps status "pending" with an empty
video_url. autopublish then hits

    if not man.get("video_url"):
        print(f"  skip (never uploaded): {man['slug']}")
        continue

every single run, for ever, and records no error -- so the reel is never
posted and nothing ever says why.

It cannot be repaired either: cmd_pending_slugs only lists reels whose
out/<slug>/reel.mp4 still exists, out/ is gitignored, and the runner that
built it is long gone. The video is lost. The honest thing is to mark it
failed so it stops being counted as pending, stops being silently
skipped, and carries a reason a human can read.

Only manifests with status "pending" AND an empty-or-missing video_url
are touched. Anything published, rejected or still legitimately awaiting
approval with a real URL is left exactly as it is.

    python3 tools/fix_parked.py          # show what it would do
    python3 tools/fix_parked.py --write  # do it
"""
import json
import pathlib
import sys

PENDING = pathlib.Path("state/pending")
REASON = ("release upload failed, so there is no public URL for Instagram "
          "to fetch; out/ is not committed so the video cannot be "
          "re-uploaded")


def main() -> None:
    write = "--write" in sys.argv
    if not PENDING.is_dir():
        sys.exit("run this from the repo root -- no state/pending here")

    hits = []
    for mf in sorted(PENDING.glob("*.json")):
        try:
            man = json.loads(mf.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  ! unreadable, left alone: {mf.name} ({exc})")
            continue
        if man.get("status") != "pending":
            continue
        if man.get("video_url"):
            continue                   # real URL: genuinely awaiting approval
        hits.append((mf, man))

    if not hits:
        print("nothing parked -- no pending manifest is missing its video_url")
        return

    for mf, man in hits:
        print(f"  {man.get('built_at', '?')[:16]}  {man.get('slug', mf.stem)}")
        print(f"      {man.get('title', '')[:70]}")
        print(f"      duration {man.get('duration', 0):.1f}s")
        if write:
            man["status"] = "failed"
            man["last_error"] = REASON
            man["attempts"] = man.get("attempts", 1)
            mf.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")

    if write:
        print(f"\nmarked {len(hits)} failed -- autopublish will stop "
              f"scanning them and `run.py list` now says why")
    else:
        print(f"\n{len(hits)} would be marked failed. Re-run with --write "
              f"to do it.")


if __name__ == "__main__":
    main()
