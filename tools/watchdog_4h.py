#!/usr/bin/env python3
"""Check for a dead pipeline every 4 hours instead of twice a day.

The watchdog already does the right thing when it finds a gap -- it
alerts and dispatches a catch-up build. The problem is how rarely it
looks. Builds run every 8 hours, so a twice-daily check can let two
slots pass unnoticed, which is exactly what happened: GitHub dropped the
scheduled builds, the pipeline sat silent for most of a day, and the
watchdog's last look found everything inside its threshold.

Six checks a day closes that to at most four hours. The job carries no
ffmpeg and no render, so the extra runs cost almost nothing.

This repo's own minute offset is preserved -- the two repos check at
different minutes on purpose, so they never collide.

    python3 tools/watchdog_4h.py
"""
import pathlib
import re
import sys

WF = ".github/workflows/watchdog.yml"
HOURS = [0, 4, 8, 12, 16, 20]


def seoul(hour: int, minute: int) -> str:
    return f"{(hour + 9) % 24:02d}:{minute:02d} Seoul"


def step_problems(text: str) -> list:
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
        for i, step in enumerate(job.get("steps") or [], 1):
            if not isinstance(step, dict):
                bad.append(f"{job_name} step {i} is not a mapping")
            elif not ("run" in step or "uses" in step):
                bad.append(f"{job_name}: step {i} has neither run nor uses")
    return bad


def main() -> None:
    p = pathlib.Path(WF)
    if not p.exists():
        sys.exit(f"run this from the repo root -- no {WF} here")

    text = p.read_text()
    crons = re.findall(r'^\s*- cron: "([^"]+)".*$', text, re.M)
    if not crons:
        sys.exit("no cron lines in the watchdog -- stopping rather than guessing")
    if len(crons) >= len(HOURS):
        print(f"already checking {len(crons)} times a day, nothing to do")
        return

    # Keep this repo's own minute: the two repos are staggered on purpose.
    try:
        minute = int(crons[0].split()[0])
    except (ValueError, IndexError):
        sys.exit(f"could not read the minute from {crons[0]!r}")

    block = "".join(
        f'    - cron: "{minute:02d} {h} * * *"'.ljust(31)
        + f"# {seoul(h, minute)}\n"
        for h in HOURS)

    # Replace the whole contiguous run of cron lines, leaving everything
    # else alone. Anchored on the lines themselves rather than on a
    # character offset -- a greedy span is what truncated build.yml.
    lines = text.split("\n")
    first = next(i for i, l in enumerate(lines) if "- cron:" in l)
    last = first
    while last + 1 < len(lines) and "- cron:" in lines[last + 1]:
        last += 1
    new_text = "\n".join(lines[:first]) + "\n" + block + "\n".join(
        lines[last + 1:])

    added = len(new_text.split("\n")) - len(lines)
    expected = len(HOURS) - len(crons)
    if added != expected:
        sys.exit(f"line count moved by {added}, expected +{expected}; "
                 f"nothing written")

    problems = step_problems(new_text)
    if problems:
        sys.exit("the edit broke the workflow, nothing written:\n  "
                 + "\n  ".join(problems))

    p.write_text(new_text)
    print(f"  {len(crons)} checks a day -> {len(HOURS)}, at :{minute:02d}")
    for h in HOURS:
        print(f"    {h:02d}:{minute:02d} UTC = {seoul(h, minute)}")


if __name__ == "__main__":
    main()
