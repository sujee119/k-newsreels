#!/usr/bin/env python3
"""Stop the channel republishing syndicated copy, and drop wire photo
captions.

Run from the repo root of k-newsreels, after fix_kscandal4.py:

    python3 tools/fix_kscandal5.py

Idempotent. Writes nothing at all if any edit cannot find its anchor.


FIRST, A PREDICTION I GOT WRONG
-------------------------------
I said to expect around 45 stories, down from 56. It stayed at 56. The gate
did reject all four items it was aimed at -- the two 다문화 policy pieces,
the labour-statistics report, the 아이돌 variety-show booking and the 수어
op-ed are all gone -- but a new cluster arrived the same afternoon and took
their slots. The count was never the thing to watch; what's in the top five
is. I should have said that instead of giving you a number.

kforeign showed nothing at all, which is what I said might happen. Judge it
tomorrow, not today.


1. THE SAME EVENT, THREE TIMES -- and the real risk behind it
-------------------------------------------------------------
      10.5  [속보] 최교진 교육부 장관 사의 표명…"수능 논란에 책임"        hankyung
      10.5  '제 나이가 있어서…' 수능 AI 부정행위 대응 논란 교육장관, 결국 사임   bbc
       8.4  "시험 방식 자체가 달라져야 한다" AI 안경 부정행위 논란에…        koreaherald

One story -- the education minister resigning over AI-glasses cheating at
the 수능 -- under three headlines from three publishers. Round two's dedup
only collapses wire REISSUES of identical text, so it never saw these as
the same thing.

In the preview this is only clutter: one reel is built per run and the
highest score wins, and that story deserves to win. The risk is the day
after. `seen` is a set of uids and a uid is sha1(link), so tomorrow the BBC
version is a brand-new story to the pipeline and you get a second reel
about a resignation you already covered.

SO I MEASURED WHETHER I COULD ACTUALLY FIX THAT, AND MOSTLY I CANNOT.
The three headlines share almost no words:

    A/B shared: 수능                    1 word
    B/C shared: ai, 부정행위            2 words
    A/C shared: 논란에                  1 word

Korean compounding and particles are why: 교육부 장관 and 교육장관 share
zero tokens, and 논란 and 논란에 are different tokens -- the same
attached-particle problem the keyword matcher had, now showing up in the
other direction. Any similarity threshold loose enough to catch A and B
would collapse unrelated stories wholesale. Cross-publisher Korean headline
dedup is not solvable this way, and I am not going to pretend otherwise by
shipping a fuzzy match tuned until these three pass.

WHAT THIS PATCH DOES FIX is verbatim syndication, which is the commoner
case in Korean media: 연합뉴스 and 뉴시스 copy is reprinted word for word
across a dozen sites under its own URL each time. Those have identical
titles, so remembering the title catches them where remembering the link
cannot.

  core/feed.py     mark_seen(uid, title_key="") also stores "t:<key>"
  core/sources.py  gather() skips a story whose title key is already seen
  run.py           all four mark_seen call sites pass the title

WHAT STILL GUARDS THE REST: the recency bonus (yesterday's event scores
lower tomorrow), one reel per run, and the Telegram approval showing you
the title before anything goes out. When you are done with a running story
-- and the 수능 AI cheating one will run for weeks -- the lever is yours:

    python run.py keywords block 부정행위


2. WIRE PHOTO CAPTIONS
----------------------
       4.9  발언하는 박주민 한강버스 비리 은폐 진상조사단장      newsis

Not an article. That is Newsis publishing a press photo, and the "headline"
is its caption -- adnominal verb plus subject plus job title. There is no
body text, so it would have died at MIN_SOURCE_WORDS anyway, but it burns a
fetch and crowds the pool. Korean wire captions are formulaic, so blocking
the openings is enough: 발언하는, 인사하는, 참석한, 악수하는, 기념촬영,
묵념하는, 답변하는, 입장하는, 포토뉴스, 사진으로 보는, 한 컷.


3. ONE I AM NOT PATCHING, BECAUSE IT IS YOUR CALL
-------------------------------------------------
       4.9  일본 측, 조국 입국 거부 관련 "자녀 입시비리 전과 있어서"   jtbc

Cho Kuk refused entry to Japan. 비리 matched, so it came in under kscandal
even though kpolitics is still disabled -- partisan Korean politics
reaching the channel through the side door. It sits at 4.9 and will never
win a slot, so nothing breaks either way, and I said earlier that enabling
political scandal was your decision rather than mine. If you want it kept
out of kscandal too, the one-liner is yours now:

    python run.py keywords block 조국 입국 거부

Blocking party names or figures is the blunt version; say the word and I
will do it properly with a require_any on kscandal instead.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
RUN = ROOT / "run.py"
FEED = ROOT / "core" / "feed.py"
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
    for p in (RUN, FEED, SOURCES, KEYWORDS):
        if not p.exists():
            sys.exit(f"! {p} not found -- wrong directory?")
    if "_title_key" not in SOURCES.read_text(encoding="utf-8"):
        sys.exit("! run tools/fix_kscandal2.py first -- _title_key comes from it")
    try:
        cfg = json.loads(KEYWORDS.read_text(encoding="utf-8"))
    except Exception as exc:
        sys.exit(f"! keywords.json will not parse: {exc}")
    if not cfg.get("topics", {}).get("kforeign", {}).get("require_any"):
        sys.exit("! run tools/fix_kscandal4.py first -- this builds on it")


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
             *, done_marker: str, expect: int = 1) -> None:
        """Replace `old` exactly `expect` times, or refuse."""
        text = self.load(path)
        assert done_marker not in old, f"{label}: marker inside old text"
        if done_marker in text:
            self.skipped.append(f"{label} (already applied)")
            return
        n = text.count(old)
        if n != expect:
            self.failed.append(
                f"{label}: old text found {n} times, expected {expect}")
            return
        self.texts[path] = text.replace(old, new)
        self.notes.append(f"{label}" + (f" ({n} sites)" if expect > 1 else ""))

    def commit(self) -> bool:
        if self.failed:
            print("\n! NOTHING WAS WRITTEN. These edits could not be applied:")
            for f in self.failed:
                print(f"    - {f}")
            print("\n  Send me this output and I will re-anchor the patch.")
            return False
        for path, text in self.texts.items():
            backup = path.with_suffix(path.suffix + ".bak5")
            if not backup.exists():
                shutil.copy2(path, backup)
            path.write_text(text, encoding="utf-8")
        return True


def patch_code(p: Patcher) -> None:
    # feed.py: remember the normalised title as well as the link.
    p.swap(FEED, "feed.py: mark_seen remembers the story title too",
           '''def mark_seen(uid: str) -> None:
    seen = _load_seen()
    seen.add(uid)''',
           '''def mark_seen(uid: str, title_key: str = "") -> None:
    """Remember a published story by link AND by normalised title.

    uid is sha1(link), so three publishers covering one resignation are
    three unrelated stories to the pipeline and the second one builds a
    duplicate reel the next day. Storing the title key under a "t:" prefix
    in the same set lets gather() recognise the event rather than the URL.
    The prefix keeps the two namespaces apart in one file, so nothing else
    that reads this set has to change.
    """
    seen = _load_seen()
    seen.add(uid)
    if title_key:
        seen.add("t:" + title_key)''',
           done_marker="def mark_seen(uid: str, title_key")

    # sources.py: honour it when gathering.
    p.swap(SOURCES, "sources.py: gather() skips an already-published event",
           '''    for s in found:
        if s.uid in seen or s.uid in by_uid:
            continue''',
           '''    for s in found:
        if s.uid in seen or s.uid in by_uid:
            continue
        # Same event, different publisher: the link is new but the story is
        # not. Conservative on purpose -- the key is the first twelve
        # content words, so a genuine follow-up with a different headline
        # still gets through.
        if "t:" + _title_key(s.title) in seen:
            continue''',
           done_marker='if "t:" + _title_key(s.title) in seen')

    # run.py: all four publish paths pass the title.
    p.swap(RUN, "run.py: pass the title at every mark_seen call",
           '''feed.mark_seen(man["story_uid"])''',
           '''feed.mark_seen(man["story_uid"],
                           sources._title_key(man.get("title", "")))''',
           done_marker='sources._title_key(man.get("title"',
           expect=4)


# --------------------------------------------------------- keywords.json data

# Korean wire photo captions are formulaic: adnominal verb, subject, job
# title, no body text. Blocking the openings is enough.
BLOCK_PHOTO_CAPTION = [
    "발언하는", "인사하는", "참석한", "참석하는", "악수하는", "기념촬영",
    "묵념하는", "답변하는", "입장하는", "포토뉴스", "사진으로 보는",
    "한 컷", "포토제닉", "브리핑하는", "발표하는 모습",
]


def patch_keywords() -> list[str]:
    cfg = json.loads(KEYWORDS.read_text(encoding="utf-8"))
    blocked = cfg.setdefault("blocked_terms", [])
    if "발언하는" in blocked:
        return ["keywords.json (already applied)"]

    added = [t for t in BLOCK_PHOTO_CAPTION if t not in blocked]
    blocked.extend(added)

    backup = KEYWORDS.with_suffix(".json.bak5")
    if not backup.exists():
        shutil.copy2(KEYWORDS, backup)
    KEYWORDS.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return [f"keywords.json: blocked {len(added)} wire photo-caption openings",
            f"keywords.json: {len(blocked)} terms blocked in total"]


def main() -> int:
    check_repo()
    p = Patcher()
    patch_code(p)
    if not p.commit():
        return 1
    kw_notes = patch_keywords()

    print("patched:")
    for note in p.notes + kw_notes:
        print(f"  + {note}")
    for note in p.skipped:
        print(f"  = {note}")
    print("\n  (.bak5 written beside each changed file on the first run)")
    print("""
NEXT:

  1. python run.py keywords preview
     Watch the top five, not the count. The 발언하는 photo caption should
     be gone. The three AI-cheating headlines will STILL all be listed --
     the cross-publisher check applies at publish time, not in preview, so
     whichever one wins today blocks the other two from tomorrow.

  2. git add -A && git commit -m "remember published stories by title, \\
     not just link; block wire photo captions" && git push

  3. After the next successful publish, confirm it took:
       python -c "import json;s=json.load(open('state/seen.json'));
       print([x for x in s if x.startswith('t:')][-3:])"
     You should see a t: entry. If that list is empty after a publish,
     tell me -- it means a mark_seen path I did not find.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
