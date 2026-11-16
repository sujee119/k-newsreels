#!/usr/bin/env python3
"""45-60s reels: raise the floor, and tell the model what the room is for.

TARGET_SECONDS and MAX_SECONDS already reach the build through
build.yml, so the CEILING is a repo variable and needs no code. Two
things in the code do need changing.

1. THE FLOOR. core/script.py rejects a draft only below

       target_words * 0.65

   which at TARGET_SECONDS=50 is 97 words -- about 32 seconds. A 45s
   minimum set by variables alone would not hold. 0.90 of target is 135
   words = 45.0s at WORDS_PER_SECOND=3.0, and 0.90 is what the shorts
   repo has always used, so this aligns the three rather than inventing
   a number.

       floor    0.90 x 50 x 3.0 = 135 words = 45s
       ceiling        60 x 3.0  = 180 words = 60s

2. RULE 8 OF THE PROMPT, which was written for a 30-second format and
   argues against the extra time:

       "This is a TIGHT format. ... There is no room for scene-setting,
       restatement or a warm-up line ... Pick the one most interesting
       thing the study found and spend the words there"

   Left alone, the model keeps compressing to 30 seconds of content and
   either pads to reach the floor or gets rejected. So the rule now
   says what the extra twenty seconds is FOR: mechanism, one comparable
   number, what the field expected instead, and one honest limit. It
   still is not for more findings -- a shallow tour of four results is
   worse than one taken apart properly, at any length.

   The section structure needs no change: t_hook, t_ground, t_method,
   t_find and t_sowhat are fractions of the total (0.14, 0.30, 0.53,
   0.80, 0.96), so hook/groundwork/method/findings/sowhat/button all
   stretch on their own.

What this costs: a tighter band rejects more drafts, so more runs reach
the fourth attempt and the best-draft path, which trims from the middle
and keeps the hook and the payoff. The failure mode is a flatter middle,
not a broken reel. If build logs start showing "no clean draft" often,
revert this commit.

Shorts needs no patch: its floor is already 0.90 and its own rule 1
carries no scarcity framing to undo.

    python3 tools/fix_length.py
"""
import ast
import pathlib
import sys

FLOOR_OLD = "            elif words < target_words * 0.65:\n"
FLOOR_NEW = (
    "            # 0.90, not 0.65: at TARGET_SECONDS=50 the old floor\n"
    "            # accepted 97 words -- about 32s -- which made a 45s\n"
    "            # minimum unenforceable from variables alone. 0.90 x 150\n"
    "            # = 135 words = 45s at 3.0 words/sec, and matches the\n"
    "            # floor the shorts repo has always used.\n"
    "            elif words < target_words * 0.90:\n")

RULE_OLD = (
    "8. LENGTH: target {target_words} words. Hard ceiling {max_words}. This is a\n"
    "   TIGHT format. Every sentence must carry information the one before it did\n"
    "   not. There is no room for scene-setting, restatement or a warm-up line --\n"
    "   the first sentence is already the hook. If a sentence does not teach\n"
    "   something or move the story, cut it rather than shorten it. Pick the one\n"
    "   most interesting thing the study found and spend the words there; a\n"
    "   shallow tour of four findings is worse than one explained properly.\n")

RULE_NEW = (
    "8. LENGTH: target {target_words} words. Hard ceiling {max_words}. Every\n"
    "   sentence must carry information the one before it did not. No\n"
    "   scene-setting, no restatement, no warm-up line -- the first sentence is\n"
    "   already the hook. If a sentence does not teach something or move the\n"
    "   story, cut it rather than shorten it.\n"
    "\n"
    "   This length exists so you can EXPLAIN. It is NOT room for more\n"
    "   findings: a shallow tour of four results is worse than one taken\n"
    "   apart properly. Pick the single most interesting thing the source\n"
    "   reports and spend the words on these, in this order:\n"
    "\n"
    "   - THE MECHANISM, in plain words. Not that it works -- HOW: the steps,\n"
    "     what acts on what, in what order. \"The protein clamps the gene\n"
    "     shut, so the cell never reads it\" earns its seconds. \"A regulatory\n"
    "     mechanism was identified\" does not.\n"
    "   - ONE NUMBER THAT MATTERS, with its unit and something to compare it\n"
    "     against. \"Forty times faster than the standard assay\" says more\n"
    "     than \"significantly faster\".\n"
    "   - WHY IT WAS SURPRISING: what the field expected instead, and what\n"
    "     this replaces. A result is only interesting against a prior belief.\n"
    "   - WHAT IT DOES NOT SHOW. One honest limit, where the source states\n"
    "     one, is worth more than another adjective.\n"
    "\n"
    "   Every claim still traces to the source material. If the source does\n"
    "   not say how something was done, do NOT invent a mechanism to fill the\n"
    "   time -- explain what it does say, in more depth.\n")


def main() -> None:
    root = pathlib.Path(".")
    p = root / "core/script.py"
    if not p.exists():
        sys.exit("run from a channel repo")
    if not (root / "core/publish.py").exists():
        sys.exit("reels repos only -- the shorts floor is already 0.90")

    src = p.read_text()
    done = []

    if "0.90, not 0.65" in src:
        done.append("floor already 0.90")
    else:
        if src.count(FLOOR_OLD) != 1:
            sys.exit(f"  floor line found {src.count(FLOOR_OLD)} time(s) "
                     f"-- NOT TOUCHED")
        src = src.replace(FLOOR_OLD, FLOOR_NEW)
        done.append("floor 0.65 -> 0.90")

    if "This length exists so you can EXPLAIN" in src:
        done.append("rule 8 already rewritten")
    else:
        if src.count(RULE_OLD) != 1:
            sys.exit(f"  rule 8 found {src.count(RULE_OLD)} time(s) "
                     f"-- NOT TOUCHED")
        src = src.replace(RULE_OLD, RULE_NEW)
        done.append("rule 8 now asks for explanation")

    if src == p.read_text():
        print("  core/script.py: already patched")
        return
    try:
        ast.parse(src)
    except SyntaxError as exc:
        sys.exit(f"  core/script.py: would not parse ({exc}) -- NOT WRITTEN")
    # The prompt is a .format() template: a stray brace would raise at
    # build time, inside the script step, with no useful message.
    for name in ("{target_words}", "{max_words}"):
        if name not in src:
            sys.exit(f"  {name} vanished from the prompt -- NOT WRITTEN")
    p.write_text(src)
    print("  core/script.py: " + "; ".join(done))
    print("""
  Then the variables, all three repos:

      TARGET_SECONDS=50   MAX_SECONDS=60     -> 135-180 words = 45-60s

  Shot count scales with duration (13-18 now, was 8-12), so the clip
  pool wants more terms:

      MPT_TERMS=9                            (reels only)
""")


if __name__ == "__main__":
    main()
