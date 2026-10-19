#!/usr/bin/env python3
"""Stop the poll workflow from cancelling the scheduled builds.

build.yml and poll.yml share `concurrency: group: reel-state` with
cancel-in-progress false. GitHub keeps only ONE pending run per
concurrency group: when a third run arrives, the run already waiting is
cancelled to make room. poll.yml fires into that shared group every 15
minutes.

So any time a poll happened to be running at :47, the scheduled build
went pending -- and the next poll, at most 15 minutes later, evicted it.
The build was cancelled before executing a single step. That is why
"Build reel" was the one workflow going missing while Poll and Watchdog,
which has no group at all, kept firing on time.

The state-push race the shared group was guarding is already handled, by
the push loop in both workflows: it fetches, rebases -X theirs, falls
back to reset --soft, retries three times and alerts if it still fails.
The lock is redundant; its only remaining effect is losing builds.

Each workflow gets its own group, so each still serialises against
itself and neither can evict the other.

    python3 tools/split_concurrency.py
"""
import pathlib
import sys

OLD = """# ONE group across both workflows. state/ is committed by
# whichever run finishes, and two runs pushing it at once is
# what lost a published status this morning. Serialising them
# removes the race rather than retrying through it.
concurrency:
  group: reel-state
  cancel-in-progress: false
"""

NEW_BUILD = """# A group per workflow, NOT one shared with poll.yml. The shared group
# is what was killing the scheduled builds: with cancel-in-progress
# false GitHub keeps only ONE pending run per group, so when a poll was
# running at :47 the build went pending and the next poll -- at most 15
# minutes later -- evicted it. The build was cancelled before it ran a
# single step, which is why this was the only workflow going missing.
#
# The state race that shared group guarded is already handled below, by
# the push loop: fetch, rebase -X theirs, reset --soft, three attempts,
# alert if it still fails. This group only serialises builds with each
# other, which is all it needs to do.
concurrency:
  group: reel-build
  cancel-in-progress: false
"""

NEW_POLL = """# A group per workflow, NOT one shared with build.yml. Sharing one
# meant this workflow -- firing every 15 minutes -- kept evicting the
# build from the pending queue, because GitHub keeps only one pending
# run per group. The state race that shared group guarded is already
# handled below, by the push loop: fetch, rebase -X theirs, reset
# --soft, three attempts, alert if it still fails.
concurrency:
  group: reel-poll
  cancel-in-progress: false
"""

TARGETS = [
    (".github/workflows/build.yml", NEW_BUILD, "reel-build"),
    (".github/workflows/poll.yml", NEW_POLL, "reel-poll"),
]


def step_problems(text: str) -> list:
    """Every step must carry run or uses. yaml.safe_load accepts a step
    with neither and Actions does not, so parsing is not enough."""
    try:
        import yaml
    except ImportError:
        return []
    try:
        doc = yaml.safe_load(text)
    except Exception as exc:
        return [f"YAML does not parse: {exc}"]
    bad = []
    for job_name, job in ((doc or {}).get("jobs") or {}).items():
        for i, step in enumerate((job or {}).get("steps") or [], 1):
            if not isinstance(step, dict):
                bad.append(f"{job_name}: step {i} is not a mapping")
            elif not ("run" in step or "uses" in step):
                bad.append(f"{job_name}: step {i} has neither run nor uses")
    if not bad and not (((doc or {}).get("jobs")) or {}):
        bad.append("no jobs survived the edit")
    return bad


def patch(path: str, new: str, group: str) -> str:
    p = pathlib.Path(path)
    if not p.exists():
        return f"  {path}: not here, skipped"

    text = p.read_text()
    if f"group: {group}" in text:
        return f"  {path}: already on {group}, nothing to do"
    if text.count(OLD) != 1:
        return (f"  {path}: the shared-group block is not there verbatim "
                f"(found {text.count(OLD)}x) -- NOT TOUCHED, patch by hand")

    new_text = text.replace(OLD, new)

    # Exactly one concurrency group before and after: a substitution,
    # never a structural change.
    if new_text.count("concurrency:") != text.count("concurrency:"):
        return f"  {path}: concurrency block count moved -- not written"

    problems = step_problems(new_text)
    if problems:
        return (f"  {path}: edit broke the workflow, NOT written:\n      "
                + "\n      ".join(problems))

    p.write_text(new_text)
    return f"  {path}: reel-state -> {group}"


def main() -> None:
    if not pathlib.Path(".github/workflows").is_dir():
        sys.exit("run this from the repo root")
    print("splitting the shared concurrency group:")
    for path, new, group in TARGETS:
        print(patch(path, new, group))


if __name__ == "__main__":
    main()
