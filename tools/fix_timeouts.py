#!/usr/bin/env python3
"""A network read timeout must never kill a build.

urlopen(..., timeout=N) raises TimeoutError on a read timeout.
TimeoutError and urllib.error.URLError are both subclasses of OSError --
SIBLINGS, not parent and child. So `except URLError` does NOT catch a
read timeout, and the timeout sails straight past the handler.

That is the 19 Oct science outage, exactly:

    [3/7] writing script and shot list
      ! attempt 1 failed: HTTP Error 503: Service Unavailable
      ! attempt 2 failed: HTTP Error 503: Service Unavailable
    ERROR: The read operation timed out

Two 503s were caught and retried as designed. The third call hung for
the full 90s timeout, raised TimeoutError, escaped the retry loop, the
best-draft path AND _fallback(), and killed the build. Every scheduled
science build since has died the same way.

This adds OSError to the handlers that need it. OSError also covers
connection resets, DNS failures and TLS errors -- the same kind of
transient problem, all worth retrying or skipping rather than crashing.

Handlers whose body uses HTTPError-only attributes (.code, .read()) are
deliberately NOT touched: adding OSError there would turn a crash into
an AttributeError, and their timeouts are already caught further up by a
broader handler.

One script for all three repos -- it applies whichever edits fit the
repo it is run in and ignores the rest. Safe to re-run.

    python3 tools/fix_timeouts.py
"""
import ast
import pathlib
import sys

# --- the reels repos (scidaily-reels, k-newsreels): core/script.py -------

SCRIPT_EDITS = [(
    """        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, KeyError) as exc:
""",
    """        # OSError, not URLError. A read timeout raises TimeoutError, and
        # TimeoutError is a SIBLING of URLError under OSError, not a
        # subclass -- so the old clause let it straight through. It then
        # escaped this retry loop, the best-draft path below and
        # _fallback(), and killed the whole build.
        #
        # That is the 19 Oct outage: two 503s from Gemini were caught and
        # retried correctly, then attempt 3 hung for the full 90s and
        # raised "The read operation timed out". The 503s were survivable;
        # the hang was not.
        #
        # OSError also covers connection resets, DNS and TLS failures,
        # which are the same kind of transient problem.
        except (OSError, json.JSONDecodeError, KeyError) as exc:
""")]

# --- the shorts repo (isnotwhatyouthink): run.py -------------------------

RUN_EDITS = [
    ("""    except gate.GateUnavailable as exc:
""",
     """    # OSError too: a network timeout talking to the gate IS the gate
    # being unavailable. Without it a transient blip raised past this
    # handler and ended the run instead of failing closed.
    except (gate.GateUnavailable, OSError) as exc:
"""),
    ("""        except anglemod.NoAngle as exc:
""",
     """        # OSError too: a timeout on one candidate should skip that
        # candidate, not abandon every remaining one.
        except (anglemod.NoAngle, OSError) as exc:
"""),
    ("""        except scriptmod.ScriptRefused as exc:
""",
     """        # OSError too, for the same reason as the angle step above.
        except (scriptmod.ScriptRefused, OSError) as exc:
"""),
]


def apply(path: pathlib.Path, pairs: list) -> str:
    """Returns a one-line report. Writes only if every guard passes."""
    if not path.exists():
        return f"  {path}: not in this repo, skipped"

    src = original = path.read_text()
    applied = already = 0

    for old, new in pairs:
        marker = new.rstrip("\n").splitlines()[-1]
        if marker in src:
            already += 1
            continue
        n = src.count(old)
        if n == 0:
            continue                   # this edit is not for this repo
        if n > 1:
            return (f"  {path}: target appears {n}x, too ambiguous "
                    f"-- NOT TOUCHED")
        src = src.replace(old, new)
        applied += 1

    if applied == 0:
        return (f"  {path}: already patched" if already
                else f"  {path}: nothing here to patch")

    try:
        ast.parse(src)
    except SyntaxError as exc:
        return f"  {path}: edit would not parse ({exc}) -- NOT WRITTEN"

    # Nothing may vanish except the exact clause lines being replaced.
    replaced = {l for old, _ in pairs for l in old.splitlines()}
    lost = {l for l in set(original.splitlines()) - set(src.splitlines())
            if l.strip()} - replaced
    if lost:
        return (f"  {path}: edit would drop {len(lost)} unrelated line(s) "
                f"-- NOT WRITTEN")

    path.write_text(src)
    return f"  {path}: patched {applied} handler(s)"


def main() -> None:
    root = pathlib.Path(".")
    if not (root / "run.py").exists():
        sys.exit("run this from a repo root -- there is no run.py here")

    print("making network timeouts non-fatal:")
    print(apply(root / "core/script.py", SCRIPT_EDITS))
    print(apply(root / "run.py", RUN_EDITS))


if __name__ == "__main__":
    main()
