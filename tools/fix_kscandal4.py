#!/usr/bin/env python3
"""Make kforeign find scandal instead of policy forums.

Run from the repo root of k-newsreels, after fix_kscandal3.py:

    python3 tools/fix_kscandal4.py

Idempotent. Writes nothing at all if any edit cannot find its anchor.


WHAT WENT WRONG, AND WHY MORE KEYWORDS WOULD NOT FIX IT
-------------------------------------------------------
kforeign produced three hits, all at 6.3, ranked 8th to 10th -- below
Samsung share prices:

    6.3  중소기업 고숙련 직무, 이주노동자 '의존도' 커졌다
         a labour-statistics report
    6.3  "다문화 지원, 기존 제도론 한계…실제수요 반영한 서비스 필요"
         a policy panel saying support needs reform
    6.3  '다문화가족에서 이주배경가족으로'…연합뉴스 다문화포럼 개막
         Yonhap announcing Yonhap's own forum

Each matched exactly ONE keyword -- 이주노동자 or 다문화 -- so the base
score was 2.0 + 1.5 = 3.5, and 3.5 x 1.8 = 6.3. The weight worked. The
matching is what failed.

The reason is that every keyword in kforeign names a SUBJECT, and a subject
word alone describes a forum announcement just as well as it describes wage
theft. 다문화 is the government's own term, so it appears in every
feel-good policy story ever written. Adding more subject words makes this
worse, not better: more ways to match a press release.

So this patch adds a gate instead. A topic can now set "require_any": a
story must match at least one term from that list, on top of the normal
keyword match, or it scores 0 and drops out.

    kforeign  require_any = the vocabulary of WRONGDOING
              체불, 브로커, 착취, 압류, 알선, 적발, 단속, 불법, 의혹,
              수사, 고발, 소송, 피해, 실태, 방치, 묵인, 갑질, 위장,
              무허가, 차별, 혐오, 폭언, 산재, 강제 + the English equivalents

Now 이주노동자 alone is not a story and 이주노동자 + 임금 체불 is. A real
one scores roughly 2 keywords + title bonuses = 7.0, x1.8 = 12.6, which
puts it above everything except a top-tier kscandal story. The three above
are rejected outright.

kforeign will be silent on days when nobody published an exploitation
story. That is the correct behaviour and it is why kscandal stays enabled
underneath it. A quiet topic is better than a topic that reliably finds
forum announcements and ranks them first because you told me to prioritise
it.

kwave gets the same treatment, because of this:

    7.5  '출판계 아이돌' 김민경, 반반차 내고 예능 출격
         a book-world "idol" going on a variety show

It matched 아이돌 and nothing else. kwave require_any is the controversy
vocabulary -- 논란, 의혹, 사과, 해명, 입장문, 하차, 제외, 음주운전,
전속계약, 분쟁, 열애, 결별, 피소 -- so an appearance booking no longer
qualifies and a contract dispute still does.


ALSO IN THIS PATCH
------------------
    8.4  SNS 수어 희화화 논란으로 본 한국수어법의 무력함과 미디어 가이드라인 시급성

An opinion column, from ablenews.co.kr. Two mistakes of mine:

  I added ablenews.co.kr to allowed_domains in round three on the guess
  that disability reporting would cross over with migrant rights. It did
  not earn its place -- it brought an op-ed. Removed. tongilnews.com goes
  with it, added on the same guess and no better justified.

  Nothing blocked opinion pieces. Korean papers label them in the headline
  or the section, so 기고, 칼럼, 사설, 시론, 논평, 되짚어, 으로 본 are now
  blocked. An op-ed has no reportable facts for a 45-second script to
  stand on, whatever its subject.


LEFT ALONE, BECAUSE IT IS WORKING
---------------------------------
    16.8  [단독] 상이군경회 서울지부장 3억8천만원 횡령…기부 쌀 빼돌린 의혹
    14.7  8년 해외 도피한 '재벌가 3세' 구본현 구속송치… 주가조작·횡령 혐의
    10.5  [속보] 최교진 교육부 장관 사의 표명…"수능 논란에 책임"

The 재벌가 3세 story is the shape to aim at: a named public figure, eight
years of flight, two specific charges, and a number. Nothing in the
scandal pipeline needs changing.
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
        sys.exit("! core/sources.py or keywords.json not found -- wrong dir?")
    try:
        cfg = json.loads(KEYWORDS.read_text(encoding="utf-8"))
    except Exception as exc:
        sys.exit(f"! keywords.json will not parse: {exc}")
    if "kforeign" not in cfg.get("topics", {}):
        sys.exit("! run tools/fix_kscandal3.py first -- this builds on it")


# ----------------------------------------------------------- sources.py edit

REQUIRE_GATE = '''    # A topic can demand that a story contain at least one term from
    # require_any, over and above matching a keyword. Without it every
    # keyword in a topic names a SUBJECT, and a subject word describes a
    # policy forum as well as it describes wage theft: kforeign ranked
    # "연합뉴스 다문화포럼 개막" -- a press release about the agency's own
    # event -- eighth, because 다문화 matched and nothing else had to.
    # Adding more subject words makes that worse. This makes the topic
    # quiet instead, which is the right failure.
    if require_any:
        if not any(_term_matches(r.lower(), hay) for r in require_any):
            return 0.0

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
            backup = path.with_suffix(path.suffix + ".bak4")
            if not backup.exists():
                shutil.copy2(path, backup)
            path.write_text(text, encoding="utf-8")
        return True


def patch_sources(p: Patcher) -> None:
    p.swap(SOURCES, "sources.py: score_story takes require_any",
           '''def score_story(story: Story, keywords: list[str], blocked: list[str],
                allowed_domains: list[str] | None = None,
                blocked_domains: list[str] | None = None,
                boost_phrases: list[str] | None = None) -> float:''',
           '''def score_story(story: Story, keywords: list[str], blocked: list[str],
                allowed_domains: list[str] | None = None,
                blocked_domains: list[str] | None = None,
                boost_phrases: list[str] | None = None,
                require_any: list[str] | None = None) -> float:''',
           done_marker="require_any: list[str] | None = None")

    p.swap(SOURCES, "sources.py: the require_any gate",
           '''    title_low = story.title.lower()
    hits = []''',
           REQUIRE_GATE + '''    title_low = story.title.lower()
    hits = []''',
           done_marker="if require_any:")

    p.swap(SOURCES, "sources.py: gather() passes require_any",
           '''            boost_phrases=cfg.get("boost_phrases", []))''',
           '''            boost_phrases=cfg.get("boost_phrases", []),
            require_any=spec.get("require_any", []))''',
           done_marker='require_any=spec.get("require_any"')


# --------------------------------------------------------- keywords.json data

# The vocabulary of wrongdoing. A kforeign story must contain one of these
# as well as a subject keyword. Terms already in blocked_terms (학대, 폭행,
# 성폭력 ...) are deliberately absent: a blocked story never reaches here.
KFOREIGN_REQUIRE = [
    "체불", "브로커", "착취", "압류", "알선", "적발", "단속", "불법",
    "의혹", "수사", "고발", "소송", "고소", "진정", "피해", "실태",
    "방치", "묵인", "갑질", "위장", "무허가", "차별", "혐오", "폭언",
    "산재", "강제", "과징금", "시정명령", "송출", "횡령", "사기",
    "무단", "편법", "거부", "퇴출", "철폐", "농성", "규탄",
    "wage theft", "unpaid", "exploited", "exploitation", "broker",
    "confiscated", "discrimination", "discriminat", "illegal", "raided",
    "crackdown", "lawsuit", "sued", "complaint", "violation", "denied",
    "refused", "protest", "abuse of", "forced labour", "forced labor",
    "trafficked", "withheld", "overcharged", "scam",
]

# Celebrity controversy, not a booking announcement. 아이돌 alone put a
# variety-show appearance at 7.5.
KWAVE_REQUIRE = [
    "논란", "의혹", "사과", "해명", "입장문", "하차", "제외", "음주운전",
    "전속계약", "분쟁", "열애", "결별", "피소", "고소", "고발", "수사",
    "비난", "질타", "해촉", "손해배상", "위반", "취소", "중단", "사퇴",
    "controversy", "apolog", "denied", "lawsuit", "dropped", "pulled",
    "dispute", "accused", "backlash", "investigat",
]

# Korean papers label opinion in the headline or section. An op-ed has no
# reportable facts for a 45-second script to stand on, whatever its subject.
BLOCK_OPINION = ["기고", "칼럼", "사설", "시론", "논평", "으로 본",
                 "되짚어", "기자의 눈", "취재파일", "오피니언"]

# Added in round three on a guess that disability and unification reporting
# would cross over with migrant rights. ablenews brought an op-ed; neither
# earned its place.
DROP_DOMAINS = ["ablenews.co.kr", "tongilnews.com"]

# My round-two "nothing was found" block went too wide, and testing this
# patch against realistic POSITIVES rather than only the bad headlines is
# what surfaced it:
#
#   이주노동자 임금 체불 3억…사업주 무혐의 처분에 농성      scored -1.0
#
# Wage theft of 300 million won, the employer not charged, the workers
# occupying the site. The non-charge IS the story, and 무혐의 vetoed it.
#
# 무혐의, 불기소, 기각 and 무죄 are OUTCOMES, and an outcome is very often
# the scandal itself -- prosecutors declining to charge a chaebol heir is a
# story, not the absence of one. Unblocked.
#
# 발견 안돼, 발견되지 않, 사실무근, 근거 없 and 해당 없 stay blocked: those
# are pure negation with nothing behind them, and the headline this was
# aimed at -- 위례 비리 등 3차례 조사에도 수사·기소 위법성 발견 안돼 --
# carries 발견 안돼, so round two's fix survives intact.
UNBLOCK_OUTCOMES = ["무혐의", "불기소", "기각", "무죄"]


def patch_keywords() -> list[str]:
    notes: list[str] = []
    cfg = json.loads(KEYWORDS.read_text(encoding="utf-8"))
    topics = cfg.get("topics", {})

    if topics.get("kforeign", {}).get("require_any"):
        return ["keywords.json (already applied)"]

    topics["kforeign"]["require_any"] = KFOREIGN_REQUIRE
    notes.append(f"keywords.json: kforeign require_any -- "
                 f"{len(KFOREIGN_REQUIRE)} wrongdoing terms "
                 f"(a subject word alone no longer qualifies)")

    if "kwave" in topics:
        topics["kwave"]["require_any"] = KWAVE_REQUIRE
        notes.append(f"keywords.json: kwave require_any -- "
                     f"{len(KWAVE_REQUIRE)} controversy terms")

    blocked = cfg.setdefault("blocked_terms", [])
    added = [t for t in BLOCK_OPINION if t not in blocked]
    blocked.extend(added)
    notes.append(f"keywords.json: blocked {len(added)} opinion-column markers")

    freed = [t for t in UNBLOCK_OUTCOMES if t in blocked]
    cfg["blocked_terms"] = blocked = [t for t in blocked
                                      if t not in UNBLOCK_OUTCOMES]
    notes.append(f"keywords.json: unblocked {len(freed)} legal OUTCOMES "
                 f"({', '.join(freed) or 'none present'}) -- a non-charge is "
                 f"often the scandal; pure negations stay blocked")

    doms = cfg.setdefault("allowed_domains", [])
    dropped = [d for d in DROP_DOMAINS if d in doms]
    cfg["allowed_domains"] = [d for d in doms if d not in DROP_DOMAINS]
    notes.append(f"keywords.json: dropped {len(dropped)} publishers I added "
                 f"on a guess ({', '.join(dropped) or 'none present'})")

    backup = KEYWORDS.with_suffix(".json.bak4")
    if not backup.exists():
        shutil.copy2(KEYWORDS, backup)
    KEYWORDS.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
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
    print("\n  (.bak4 written beside each changed file on the first run)")
    print("""
NEXT:

  1. python run.py keywords preview
     Expect FEWER stories, around 45. The three kforeign policy items and
     the 아이돌 variety-show one should be gone, along with the 수어 op-ed.
     kforeign may show nothing at all today -- that is the gate working,
     not the topic being broken. Check again tomorrow before judging it.

  2. git add -A && git commit -m "require a wrongdoing term in kforeign \\
     and kwave; block op-eds" && git push

  The gate is per topic, so you can use it anywhere:

     "require_any": ["논란", "의혹"]     in any topic in keywords.json
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
