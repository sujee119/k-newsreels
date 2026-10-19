#!/usr/bin/env python3
"""Repair build.yml, then wire the voice settings in properly.

My previous patcher deleted from the Azure comment to the end of the file:
the dedup regex ended in `.*\\n` under re.S, where `.` matches newlines, so
the greedy tail swallowed everything after it. The build step lost its
`run:` and the autopublish, commit-state and upload steps went with it.
yaml.safe_load still passed, because a step with no `run` is valid YAML and
only Actions rejects it.

So this does two things:

  1. Checks the workflow is intact -- every step has `run` or `uses`, and
     the known steps are present. If not, it walks git history for the
     newest version that IS intact and restores that file.
  2. Re-applies the voice env, with guards: the edit may only ADD lines,
     never remove any, and the result must pass the step check.

Safe to run twice and safe on either repo.

    python3 tools/fix_workflow.py
"""
import pathlib
import re
import subprocess
import sys

WF = ".github/workflows/build.yml"

NEW_LIST = "'gemini,azure,edge,gtts,espeak'"
OLD_LISTS = ["'edge,gtts,espeak'", "'azure,edge,gtts,espeak'"]

# Two independent blocks, each added only when its key is absent. Nothing
# is ever deleted -- the previous version tried to dedup with a regex and
# took the rest of the file with it.
GEMINI_ENV = """          # gemini runs on GEMINI_API_KEY above: no second account, and Google
          # lists its TTS models as free of charge on the free tier. It is
          # rate-limited to 3 requests a minute, which one reel never hits.
          GEMINI_TTS_MODEL: ${{ vars.GEMINI_TTS_MODEL || 'gemini-3.8-flash-tts' }}
          GEMINI_TTS_VOICE: ${{ vars.GEMINI_TTS_VOICE || 'Orus' }}
          GEMINI_TTS_STYLE: ${{ vars.GEMINI_TTS_STYLE || 'a confident male news presenter, clear and brisk, no sing-song' }}
"""

AZURE_ENV = """          # azure is the same neural voices edge borrows, on the documented
          # endpoint. Unset key means the engine skips itself and the chain
          # falls through to edge, gtts and espeak exactly as before.
          AZURE_SPEECH_KEY: ${{ secrets.AZURE_SPEECH_KEY }}
          AZURE_SPEECH_REGION: ${{ vars.AZURE_SPEECH_REGION || 'koreacentral' }}
"""

LENGTH_ENV = """          # Reel length, so it can be tuned with a repo variable instead of a
          # redeploy. An unset variable arrives as an EMPTY env var rather
          # than an absent one, which config falls back from, so leaving
          # these unset keeps the config defaults.
          TARGET_SECONDS: ${{ vars.TARGET_SECONDS }}
          MAX_SECONDS: ${{ vars.MAX_SECONDS }}
"""

MARKER = "TARGET_SECONDS"
# Steps every version of this workflow has had. Their absence means the
# file was truncated, whatever the YAML parser says.
REQUIRED_STEPS = ["Build the reel", "Commit state"]


def step_problems(text: str) -> list[str]:
    """Every step must carry run or uses. This is the check that matters:
    `yaml.safe_load` accepts a step with neither and Actions does not."""
    bad = []
    try:
        import yaml
    except ImportError:
        return ["pyyaml missing -- cannot verify (pip install pyyaml)"]
    try:
        doc = yaml.safe_load(text)
    except Exception as exc:
        return [f"YAML does not parse: {exc}"]
    if not isinstance(doc, dict) or not doc.get("jobs"):
        return ["no jobs in the workflow"]
    for job_name, job in (doc.get("jobs") or {}).items():
        for i, step in enumerate(job.get("steps") or [], 1):
            if not isinstance(step, dict):
                bad.append(f"{job_name} step {i} is not a mapping")
            elif not ("run" in step or "uses" in step):
                label = step.get("name", f"step {i}")
                bad.append(f"{job_name}: '{label}' has neither run nor uses")
    for name in REQUIRED_STEPS:
        if name not in text:
            bad.append(f"the '{name}' step is missing entirely")
    return bad


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def recover(p: pathlib.Path) -> bool:
    """Restore the newest version of the workflow from history that passes
    the step check. Returns True if a restore happened."""
    shas = [s for s in git("log", "--format=%H", "--", WF).split() if s]
    for sha in shas:
        old = git("show", f"{sha}:{WF}")
        if not old:
            continue
        if not step_problems(old):
            p.write_text(old)
            subject = git("log", "-1", "--format=%s", sha).strip()
            print(f"  restored from {sha[:8]} ({len(old.splitlines())} lines)"
                  f" -- {subject[:48]}")
            return True
    sys.exit("no intact version of build.yml found in history -- stopping. "
             "Do not push; tell me and I will send the file whole.")


def main() -> None:
    p = pathlib.Path(WF)
    if not p.exists():
        sys.exit(f"run this from the repo root -- no {WF} here")

    problems = step_problems(p.read_text())
    if problems:
        print("  workflow is broken:")
        for pr in problems[:6]:
            print(f"    - {pr}")
        recover(p)
    else:
        print("  workflow is intact")

    text = p.read_text()
    lines_before = len(text.splitlines())

    if MARKER in text and NEW_LIST in text:
        print("  voice settings already wired, nothing more to do")
        return

    if NEW_LIST not in text:
        for old in OLD_LISTS:
            if old in text:
                text = text.replace(old, NEW_LIST)
                print(f"  TTS_PROVIDERS: {old} -> {NEW_LIST}")
                break
        else:
            sys.exit("could not find the TTS_PROVIDERS default -- stopping "
                     "rather than guessing")

    expected = 0
    for label, block, key in (("gemini", GEMINI_ENV, "GEMINI_TTS_VOICE"),
                              ("azure", AZURE_ENV, "AZURE_SPEECH_KEY"),
                              ("length", LENGTH_ENV, "TARGET_SECONDS")):
        if key in text:
            print(f"  {label} settings already present, left alone")
            continue
        m = re.search(r"^[ \t]*TTS_PROVIDERS:.*\n", text, re.M)
        if not m:
            sys.exit("no TTS_PROVIDERS line to anchor the voice settings to")
        text = text[:m.end()] + block + text[m.end():]
        expected += len(block.splitlines())
        print(f"  added the {label} settings")

    # The guard that was missing last time: this edit may only ADD lines.
    added = len(text.splitlines()) - lines_before
    if added != expected:
        sys.exit(f"the edit changed the line count by {added}, expected "
                 f"+{expected} -- nothing written")

    problems = step_problems(text)
    if problems:
        sys.exit("the edit broke the workflow, so nothing was written:\n  "
                 + "\n  ".join(problems[:6]))

    for key in ("GEMINI_TTS_VOICE", "AZURE_SPEECH_KEY", "TTS_PROVIDERS",
                "TARGET_SECONDS", "MAX_SECONDS"):
        n = len(re.findall(rf"^\s*{key}:", text, re.M))
        if n != 1:
            sys.exit(f"{key} appears {n} times; nothing written")

    p.write_text(text)
    print(f"  written, {len(text.splitlines())} lines, every step has a run "
          f"or uses")


if __name__ == "__main__":
    main()
