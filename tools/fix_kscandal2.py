#!/usr/bin/env python3
"""Fix what the first `keywords preview` exposed.

Run from the repo root of k-newsreels, after fix_kscandal.py:

    python3 tools/fix_kscandal2.py

Idempotent. Writes nothing at all if any edit cannot find its anchor.

THE PREVIEW WORKED -- it caught four things before anything was published.

1. A DEAD PERSON RANKED FOURTH.  (the one that mattered)

       9.5  '리트 만점' 이진우, 사망으로도 끝나지 않는 의혹… 경찰 수사 계속
       7.0  '리트 만점자' 고발 변호사 "AI 부정행위 단서 찾아달라" 수사 촉구

   Allegations against someone who has died, plus a second story from the
   same thread. My Korean block list had 사망자 (fatalities, death toll)
   but not 사망 (death) on its own, so 사망으로도 went straight through.
   That is the gap the English list has never had, because "dies" and
   "death toll" are separate entries there.

   Fixed by blocking the ways a Korean headline says someone died:
   사망, 숨진, 숨져, 숨졌, 별세, 조문, 장례. 빈소 and 유족 were already in.
   고인 is deliberately left out -- it is a substring of 고인돌 (dolmen).

2. THE TOP-RANKED STORY WAS AN ANTI-SCANDAL.

       10.5  위례 비리 등 3차례 조사에도 수사·기소 위법성 발견 안돼

   Read the end: 발견 안돼 -- nothing was found, after three probes. It
   ranked first because it contains 비리 + 수사 + 기소, so pure keyword
   density scored the *negation* of a scandal above every real one.

   A reel built from this has a hook and no payoff, which breaks the
   script's own hard rule 2 (a loop you open and never close is the one
   unforgivable error). Blocked the "nothing found" shapes: 무혐의,
   혐의 없음, 발견 안돼, 발견되지 않, 사실무근, 불기소, 기각, 무죄.

3. SEVEN COPIES OF ONE WIRE STORY.

       6.5  (2nd LD) Seoul shares plunge over 3 pct on AI jitters
       6.5  (LEAD)   Seoul shares plunge over 3 pct on AI jitters
       6.5           Seoul shares plunge over 3 pct on AI jitters
       6.5  (URGENT) Seoul shares plunge over 3 pct on AI jitters
       ...

   uid is sha1(link) and each wire update has its own URL, so dedup never
   saw them as the same story. They flooded the candidate pool. Now the
   title is normalised -- (LEAD), (URGENT), (2nd LD), [속보], [단독] and
   punctuation stripped -- and duplicates collapse to the highest-scoring
   one, preferring the longest title on a tie since a later wire update
   carries more detail.

4. STOCK TICKERS OUTRANKED EVERY SCANDAL.

       10.0 [ktech] Samsung, SK hynix redraw playbooks for AI boom
       10.0 [ktech] Samsung Electronics, SK hynix fall as U.S. tech sell-off

   Two keywords matching plus title bonuses puts a share-price headline
   above an 8.3-billion-won embezzlement. These are the stories you
   already said nobody watches. Topics now take an optional "weight" that
   multiplies the final score, and the weights below push scandal up and
   share prices down. Set every weight to 1 in keywords.json to undo it.

WHAT SURVIVED THE PREVIEW AND IS WORTH KEEPING
----------------------------------------------
       9.5  '83억 횡령 의혹' 김포FC, 지난해 말 현금성 자산 198만 원뿻
            8.3bn won embezzlement; the club had 1.98m won in cash
       9.5  상이군경회 서울지부장 3억8천만원 횡령…기부 쌀 빼돌린 의혹
            veterans' association branch head, donated rice siphoned off
       7.5  최교진 교육부 장관 사의 표명…"수능 논란에 책임"
            education minister resigns over the CSAT controversy
       7.0  '음주운전' 고경표, 결국 '핑계고' 시상식 후보 제외
            actor dropped from an awards shortlist over drunk driving

   A public figure, a concrete number, a clear wrong, and nobody's private
   grief in it. That is the shape to aim at.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
SOURCES = ROOT / "core" / "sources.py"
KEYWORDS = ROOT / "keywords.json"


def check_repo() -> None:
    try:
        remote = subprocess.run(["git", "remote", "get-url", "origin"],
                                capture_output=True, text=True,
                                timeout=10).stdout.strip()
    except Exception:
        remote = ""
    if "k-newsreels" not in remote:
        sys.exit(f"! this patch is for k-newsreels; origin is '{remote}'\n"
                 f"  run it from ~/singlescape/k-newsreels")
    if not SOURCES.exists() or not KEYWORDS.exists():
        sys.exit("! core/sources.py or keywords.json not found -- wrong directory?")
    if "_term_matches" not in SOURCES.read_text(encoding="utf-8"):
        sys.exit("! run tools/fix_kscandal.py first -- this builds on it")


# ----------------------------------------------------------- sources.py edits

DEDUP_HELPERS = '''

# Wire services reissue one story under several URLs: "(URGENT) Seoul shares
# plunge", then "(LEAD) Seoul shares plunge", then "(2nd LD) Seoul shares
# plunge". uid is sha1(link), so dedup never saw them as the same story and
# seven copies of one headline crowded out everything else. Normalising the
# title collapses them.
_WIRE_PREFIX = re.compile(
    r"^\\s*(?:\\((?:URGENT|LEAD|\\d+(?:st|nd|rd|th)\\s+LD|Yonhap[^)]*)\\)"
    r"|\\[(?:속보|단독|종합|르포|인터뷰|기고|사설)\\])\\s*", re.I)
_TITLE_NOISE = re.compile(r"[^0-9a-z\\uac00-\\ud7a3]+")


def _title_key(title: str) -> str:
    """A stable key for 'the same story', whatever the wire tag."""
    t = title or ""
    for _ in range(4):                     # "(LEAD) (2nd LD) ..." stacks
        t2 = _WIRE_PREFIX.sub("", t)
        if t2 == t:
            break
        t = t2
    t = _TITLE_NOISE.sub(" ", t.lower()).strip()
    return " ".join(t.split()[:12])        # a later update appends, so compare heads
'''

WEIGHT_AND_DEDUP = '''        if s.score <= 0:
            continue
        # Topic weight. Two keywords matching puts a share-price headline
        # above a corruption indictment on raw density; this is the dial
        # that fixes it without touching score_story. Set every weight to 1
        # in keywords.json to switch it off.
        s.score *= float(spec.get("weight", 1.0))
        by_uid[s.uid] = s

        # Collapse wire reissues of one story, keeping the best-scoring and,
        # on a tie, the longest title -- a later update carries more detail.
        key = _title_key(s.title)
        prev = by_title.get(key)
        if prev is not None:
            if (s.score, len(s.title)) <= (prev.score, len(prev.title)):
                continue
            scored.remove(prev)
        by_title[key] = s
        scored.append(s)
'''


class Patcher:
    def __init__(self) -> None:
        self.texts: dict[Path, str] = {}
        self.notes: list[str] = []
        self.failed: list[str] = []
        self.skipped: list[str] = []

    def load(self, path: Path) -> str:
        if path not in self.texts:
            self.texts[path] = path.read_text(encoding="utf-8")
        return self.texts[path]

    def swap(self, path: Path, label: str, old: str, new: str,
             *, done_marker: str) -> None:
        text = self.load(path)
        assert done_marker not in old, f"{label}: marker inside old text"
        if done_marker in text:
            self.skipped.append(f"{label} (already applied)")
            return
        n = text.count(old)
        if n != 1:
            self.failed.append(f"{label}: old text found {n} times, expected 1")
            return
        self.texts[path] = text.replace(old, new, 1)
        self.notes.append(label)

    def commit(self) -> bool:
        if self.failed:
            print("\n! NOTHING WAS WRITTEN. These edits could not be applied:")
            for f in self.failed:
                print(f"    - {f}")
            print("\n  Send me this output and I will re-anchor the patch.")
            return False
        for path, text in self.texts.items():
            backup = path.with_suffix(path.suffix + ".bak2")
            if not backup.exists():
                shutil.copy2(path, backup)
            path.write_text(text, encoding="utf-8")
        return True


def patch_sources(p: Patcher) -> None:
    p.swap(SOURCES, "sources.py: wire-reissue title key",
           '''def _has_hangul(s: str) -> bool:''',
           DEDUP_HELPERS.lstrip("\n") + '''

def _has_hangul(s: str) -> bool:''',
           done_marker="def _title_key")

    p.swap(SOURCES, "sources.py: by_title map in gather()",
           '''    scored: list[Story] = []
    by_uid: dict[str, Story] = {}''',
           '''    scored: list[Story] = []
    by_uid: dict[str, Story] = {}
    by_title: dict[str, Story] = {}''',
           done_marker="by_title: dict[str, Story] = {}")

    p.swap(SOURCES, "sources.py: topic weight + wire dedup",
           '''        if s.score <= 0:
            continue
        by_uid[s.uid] = s
        scored.append(s)
''',
           WEIGHT_AND_DEDUP,
           done_marker='s.score *= float(spec.get("weight"')


# --------------------------------------------------------- keywords.json data

# The ways a Korean headline says someone died. 사망자 was already blocked;
# 사망 on its own was the gap that let a dead person's story rank fourth.
# 고인 is left out on purpose: substring of 고인돌 (dolmen).
BLOCK_DEATH = [
    "사망", "숨진", "숨져", "숨졌", "별세", "조문", "장례", "영결",
    "추모", "사망설",
]

# "Nothing was found" headlines. Not harmful -- just not a story. A reel
# built from one has a hook and no payoff, which is the script's own
# unforgivable error.
BLOCK_NOTHING_FOUND = [
    "무혐의", "혐의 없음", "발견 안돼", "발견되지 않", "사실무근",
    "불기소", "기각", "무죄", "근거 없", "해당 없",
]

# Multiplies the final score. The preview had share-price headlines at 10.0
# above an 8.3bn-won embezzlement at 9.5.
WEIGHTS = {
    "kscandal": 1.4,
    "kwave": 1.15,
    "koffbeat": 1.1,
    "kculture": 1.0,
    "kscience": 1.0,
    "kpolitics": 1.0,
    "northkorea": 1.0,
    "korea": 0.8,        # the Yonhap KOSPI/wire filler lives here
    "ktech": 0.7,        # "Samsung, SK hynix fall as ..."
    "kbusiness": 0.7,
}

# An exclusive is genuinely better material than a wire follow.
ADD_BOOSTS = ["단독", "처음 확인", "확인됐다", "입수"]


def patch_keywords() -> list[str]:
    notes: list[str] = []
    cfg = json.loads(KEYWORDS.read_text(encoding="utf-8"))

    if "사망" in cfg.get("blocked_terms", []):
        return ["keywords.json (already applied)"]

    blocked = cfg.setdefault("blocked_terms", [])
    for group, label in ((BLOCK_DEATH, "death-of-a-person"),
                         (BLOCK_NOTHING_FOUND, "nothing-was-found")):
        added = [t for t in group if t not in blocked]
        blocked.extend(added)
        notes.append(f"keywords.json: blocked {len(added)} {label} terms")

    n = 0
    for name, w in WEIGHTS.items():
        if name in cfg.get("topics", {}):
            cfg["topics"][name]["weight"] = w
            n += 1
    notes.append(f"keywords.json: topic weights on {n} topics "
                 f"(kscandal 1.4, ktech/kbusiness 0.7, korea 0.8)")

    boosts = cfg.setdefault("boost_phrases", [])
    added = [b for b in ADD_BOOSTS if b not in boosts]
    boosts.extend(added)
    notes.append(f"keywords.json: +{len(added)} exclusive-reporting boosts")

    backup = KEYWORDS.with_suffix(".json.bak2")
    if not backup.exists():
        shutil.copy2(KEYWORDS, backup)
    KEYWORDS.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return notes


def main() -> int:
    check_repo()
    p = Patcher()
    patch_sources(p)
    if not p.commit():
        return 1
    kw_notes = patch_keywords()

    print("patched:")
    for note in p.notes + kw_notes:
        print(f"  + {note}")
    for note in p.skipped:
        print(f"  = {note}")
    print("\n  (.bak2 written beside each changed file on the first run)")
    print("""
NEXT:

  1. python run.py keywords preview
     The two 리트 만점 stories and the 발견 안돼 one should be gone, and
     kscandal should sit above the Samsung share-price headlines. Send me
     the top of the list again if anything there still looks wrong.

  2. git add -A && git commit -m "korean scandal sources: deaths, \\
     anti-scandals, wire dedup, topic weights" && git push
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
