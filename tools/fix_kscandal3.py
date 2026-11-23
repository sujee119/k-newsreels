#!/usr/bin/env python3
"""Foreigner-in-Korea stories first, plus a block CLI so you stop waiting
on me, plus the three clusters that survived round two.

Run from the repo root of k-newsreels, after fix_kscandal2.py:

    python3 tools/fix_kscandal3.py

Idempotent. Writes nothing at all if any edit cannot find its anchor.


A. THE FOREIGNER TOPIC, AND THE ONE DECISION I MADE FOR YOU
-----------------------------------------------------------
"Foreigner-related scandals in Korea" splits into two genres that look
similar in a feed and could not be more different downstream.

  Wrongdoing AGAINST foreigners, and by institutions: wage theft from
  E-9 workers, visa and recruitment brokers, confiscated passports,
  dormitory conditions, fake-marriage brokering, universities running
  international students as a revenue line, landlords and bars refusing
  foreigners outright. This is real investigative reporting, it is carried
  by 매일노동뉴스 and 프레시안 and 시사인 week after week, almost none of
  it reaches English, and your channel narrates in English to an audience
  that includes foreigners in Korea. It is advertiser-safe and it travels.

  Crime BY foreigners -- 외국인 범죄 as a Korean news genre. This performs
  well, which is the problem. It is the one thing in this whole project
  that risks actual demonetisation rather than quiet throttling: YouTube's
  advertiser-friendly guidelines restrict content that promotes hatred
  against a group by nationality, ethnicity or immigration status, and a
  channel that runs this beat becomes a xenophobia feed within a month
  whether or not that was the intent.

So kforeign chases the first and not the second. If that is not the call
you wanted, tell me and I will change it -- but I am not going to build
the second one quietly while calling it the first.

Priority: "always prioritise it" is implemented two ways.

  weight 1.8, the highest in the file (kscandal is 1.4), and

  kforeign is inserted FIRST in the topics object. Topics are iterated in
  insertion order and dedup is first-claim by uid, so when a Korea Herald
  story matches both kforeign and korea, kforeign takes it at 1.8 instead
  of korea taking it at 0.8. This works cleanly because of where the
  `if s.score <= 0: continue` sits in gather(): a story that does NOT
  match kforeign is never added to by_uid, so it falls through to the
  other topics untouched. Nothing is lost, the foreigner story just wins.

  That costs two extra feed fetches per run, because koreaherald and
  en.yna are now listed under both topics. Worth it.

One loss worth naming: 아동 is blocked, so undocumented-migrant-children
stories -- a real beat -- get vetoed. Minors stay out. That stands.


B. THE BLOCK CLI -- the change that stops this loop
---------------------------------------------------
This is my third blocked_terms patch in an hour, and that is the actual
bug. `keywords add/remove` only edits a topic's keywords; nothing could
touch blocked_terms, so every bad story had to come back to me and wait
for a tarball. Now:

    python run.py keywords block   리트 만점 손흥민
    python run.py keywords unblock 기각
    python run.py keywords blocked

You own the next cluster.


C. WHAT SURVIVED ROUND TWO
--------------------------
1. THE SAME DEAD-PERSON THREAD, IN THREE MORE HEADLINES.

       9.8  '리트 만점자' 고발 변호사 "AI 부정행위 단서 찾아달라" 수사 촉구
       8.4  출소 뒤 반년만에 국어 7→2등급... 리트 만점자, 3년 전 수능도 의혹
       8.4  모텔서 사라진 '리트 만점자'…공갈 전과·수감 의혹 터졌다

   Blocking 사망 caught only the headline that said 사망. These three are
   the same person and the same thread and none of them mentions a death.
   No term list was going to catch that: what makes it off-limits is a
   fact about the subject, not a word in the headline. That is exactly
   what the block CLI is for. Blocked here by thread name, plus the
   category it belongs to -- a private individual's criminal history:
   출소, 수감, 공갈, 실종, 모텔.

   Left out on purpose: 전과. Substring of 전과목 ("all subjects"), so it
   would quietly block education stories, and 수능 논란 was your number 3.

2. KOREAN SPORT, WHICH THE ENGLISH LIST NEVER COVERED.

       8.4  '손흥민 리더십 논란'에 '96라인 은퇴설'까지 터졌는데...

   blocked_terms had NBA, NFL and fantasy football and nothing for
   football in Korean. Blocked 대표팀, 리그, 은퇴설, 이적설, 감독 경질,
   프로야구, 구단. 리그 covers K리그 and 프리미어리그 both. 감독 경질
   rather than bare 경질, because an executive dismissed after a scandal
   is a story worth keeping.

3. A COUNCIL MEETING RANKED FOURTH.

       10.5 '경제성 논란' 오색케이블카 또 찬반 논란

   A cable-car feasibility argument. Not scandal, not harmful, just dull.
   논란 matched twice and the boost fired on top. Blocked 찬반 논란,
   재심의, 주민 설명회. The weakest of these three -- if it costs you
   stories you wanted, `keywords unblock 찬반 논란`.


D. WHAT IS ALREADY WORKING, LEFT ALONE
--------------------------------------
      16.8  [단독] 상이군경회 서울지부장 3억8천만원 횡령…기부 쌀 빼돌린 의혹
      13.3  '83억 횡령 의혹' 김포FC, 현금성 자산 198만 원뿐
       7.0  Samsung, SK hynix redraw playbooks for AI boom

   Your two best stories first and second, share prices down from 10.0 to
   7.0, and the 단독 boost correctly putting the exclusive on top. Wire
   dedup also held: one "(2nd LD) Seoul shares plunge" where there were
   four identical copies. The remaining Seoul-shares items are genuinely
   different headlines from the same market day and sit at 5.2, so they
   will never win a slot.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
RUN = ROOT / "run.py"
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
    if not RUN.exists() or not KEYWORDS.exists():
        sys.exit("! run.py or keywords.json not found -- wrong directory?")
    # Parse rather than substring-search the raw text: save_keywords used to
    # ASCII-escape, so a file written by the CLI holds 사망 and a
    # text search for 사망 finds nothing even though the term is there.
    try:
        blocked = json.loads(KEYWORDS.read_text(encoding="utf-8")
                             ).get("blocked_terms", [])
    except Exception as exc:
        sys.exit(f"! keywords.json will not parse: {exc}")
    if "사망" not in blocked:
        sys.exit("! run tools/fix_kscandal2.py first -- this builds on it")


# ------------------------------------------------------------- run.py: CLI

BLOCK_CMD = '''    if args.action in ("block", "unblock"):
        if not args.words:
            print("need one or more terms, e.g. "
                  "python run.py keywords block 리트 만점")
            return 1
        blocked = cfg.setdefault("blocked_terms", [])
        for w in args.words:
            if args.action == "block" and w not in blocked:
                blocked.append(w)
                print(f"  blocked: {w}")
            elif args.action == "unblock" and w in blocked:
                blocked.remove(w)
                print(f"  unblocked: {w}")
            else:
                print(f"  (no change: {w})")
        sources.save_keywords(cfg)
        print(f"\\n  {len(blocked)} terms blocked. "
              f"Run 'keywords preview' to see the effect.")
        return 0

    if args.action == "blocked":
        blocked = cfg.get("blocked_terms", [])
        ko = [t for t in blocked
              if any("\\uac00" <= c <= "\\ud7a3" for c in t)]
        en = [t for t in blocked if t not in ko]
        print(f"\\n  {len(en)} English:\\n    " + ", ".join(sorted(en)))
        print(f"\\n  {len(ko)} Korean:\\n    " + ", ".join(ko))
        print("\\n  python run.py keywords unblock <term>   to drop one")
        return 0

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
            backup = path.with_suffix(path.suffix + ".bak3")
            if not backup.exists():
                shutil.copy2(path, backup)
            path.write_text(text, encoding="utf-8")
        return True


def patch_sources(p: Patcher) -> None:
    """save_keywords ASCII-escaped everything it wrote.

    `json.dumps(data, indent=2)` defaults to ensure_ascii=True, so the first
    time any `keywords` command writes the file, all 242 Korean terms become
    \\uc0ac\\ub9dd escapes. Python reads that back fine, so nothing breaks --
    it just makes keywords.json unmaintainable by hand, turns every later
    git diff into a whole-file rewrite, and defeats any text search for a
    Korean term. Invisible until this week, because the file was all ASCII.
    Caught when the block CLI rewrote the file during testing.
    """
    p.swap(SOURCES, "sources.py: save_keywords stops ASCII-escaping Korean",
           '''    KEYWORDS_PATH.write_text(json.dumps(data, indent=2))''',
           '''    KEYWORDS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\\n",
        encoding="utf-8")''',
           done_marker="ensure_ascii=False")


def patch_run(p: Patcher) -> None:
    # argparse FIRST. BLOCK_CMD contains the text `("block", "unblock")`, so
    # a marker like '"block", "unblock"' would be satisfied by the handler
    # body and this edit would silently skip -- leaving the new actions
    # rejected by argparse and the whole CLI dead on arrival. It did exactly
    # that on the first test run. The marker below includes "check-feeds",
    # which only ever appears in the choices list.
    p.swap(RUN, "run.py: register the new actions with argparse",
           '''                   choices=["list", "add", "remove", "enable", "disable",
                            "preview", "check-feeds"])''',
           '''                   choices=["list", "add", "remove", "enable", "disable",
                            "preview", "check-feeds", "block", "unblock",
                            "blocked"])''',
           done_marker='"check-feeds", "block"')

    p.swap(RUN, "run.py: keywords block / unblock / blocked",
           '''    if args.action == "check-feeds":''',
           BLOCK_CMD + '''    if args.action == "check-feeds":''',
           done_marker='if args.action in ("block", "unblock")')

    # Belt and braces: both halves or neither. A handler argparse rejects is
    # worse than no handler, because it looks installed and fails at the
    # prompt.
    text = p.load(RUN)
    if '"check-feeds", "block"' not in text:
        p.failed.append("run.py: argparse choices did not get the new actions")
    if 'if args.action in ("block", "unblock")' not in text:
        p.failed.append("run.py: the block/unblock handler was not inserted")


# ----------------------------------------------------- keywords.json: kforeign

KFOREIGN = {
    "tier": "caution",
    "search_context": "한국 이주노동자 외국인",
    "search_locale": "ko",
    # Highest in the file. kscandal is 1.4.
    "weight": 1.8,
    "keywords": [
        # The first six are also the google_news queries (gather uses
        # kws[:6]), so they are the six that must earn their place.
        "이주노동자", "임금 체불", "비자 브로커", "인종차별",
        "외국인 노동자", "유학생",
        # Korean, matched in feed titles
        "고용허가제", "여권 압류", "송출 비용", "미등록", "결혼이주",
        "위장결혼", "다문화", "이주민", "난민", "체류자격", "출입국",
        "노동착취", "강제노동", "중개업체", "알선", "어학연수",
        "외국인 혐오", "외국인 전용", "출입 금지", "차별", "혐오 표현",
        "산업재해", "산재", "기숙사", "사업주", "브로커",
        # English, for the English-language feeds below
        "migrant worker", "migrant workers", "foreign workers",
        "wage theft", "unpaid wages", "visa broker", "work permit",
        "labour exploitation", "labor exploitation", "passport confiscated",
        "racial discrimination", "international students", "english teacher",
        "foreign residents", "employment permit", "undocumented",
        "marriage migrant", "no foreigners", "discrimination against",
        "exploited", "foreign students", "migrants",
    ],
    "feeds": [
        # Korean-language. 매일노동뉴스 is a labour daily and is the single
        # best source in the country for this beat; 프레시안, 시사인 and
        # 일다 run the long-form investigations; 여성신문 covers marriage
        # migrants, which nobody else does consistently.
        "https://www.labortoday.co.kr/rss/allArticle.xml",
        "https://www.pressian.com/api/v3/site/rss/section/68",
        "https://www.sisain.co.kr/rss/allArticle.xml",
        "https://www.ildaro.com/rss/rss_news.php",
        "http://www.womennews.co.kr/rss/S1N2.xml",
        # English-language, listed here as well as under `korea` so that a
        # foreigner story is claimed at 1.8 rather than 0.8. A story that
        # does not match kforeign scores 0 and falls through untouched.
        "https://www.koreaherald.com/rss/newsAll",
        "https://en.yna.co.kr/RSS/news.xml",
    ],
    "hashtags": ["#koreanews", "#foreignersinkorea", "#migrantworkers",
                 "#koreavisa", "#expatkorea"],
}

ADD_DOMAINS = ["ildaro.com", "tongilnews.com", "ablenews.co.kr"]

# Exploitation reporting has its own vocabulary of revelation.
ADD_BOOSTS = ["실태", "방치", "묵인", "수년간", "버젓이", "속수무책"]


# --------------------------------------------------- keywords.json: cleanup

BLOCK_THREAD = ["리트 만점", "리트 만점자"]
BLOCK_CRIMINAL_HISTORY = ["출소", "수감", "공갈", "실종", "모텔", "전과자"]
BLOCK_SPORTS = ["대표팀", "리그", "은퇴설", "이적설", "감독 경질",
                "프로야구", "구단", "감독 사퇴"]
BLOCK_LOCAL_DISPUTE = ["찬반 논란", "재심의", "주민 설명회"]


def patch_keywords() -> list[str]:
    notes: list[str] = []
    cfg = json.loads(KEYWORDS.read_text(encoding="utf-8"))
    blocked = cfg.setdefault("blocked_terms", [])

    if "kforeign" in cfg.get("topics", {}):
        return ["keywords.json (already applied)"]

    for group, label in ((BLOCK_THREAD, "the 리트 만점 thread by name"),
                         (BLOCK_CRIMINAL_HISTORY,
                          "private-individual criminal history"),
                         (BLOCK_SPORTS, "Korean sport"),
                         (BLOCK_LOCAL_DISPUTE, "local planning disputes")):
        added = [t for t in group if t not in blocked]
        blocked.extend(added)
        notes.append(f"keywords.json: blocked {len(added)} terms -- {label}")

    # kforeign goes FIRST so it claims a shared story before `korea` does.
    # Dicts keep insertion order and json.loads preserves it, so rebuild.
    topics = cfg["topics"]
    cfg["topics"] = {"kforeign": KFOREIGN,
                     **{k: v for k, v in topics.items() if k != "kforeign"}}
    notes.append(f"keywords.json: kforeign FIRST in topic order "
                 f"(claims a shared story before korea), weight 1.8, "
                 f"{len(KFOREIGN['keywords'])} keywords, "
                 f"{len(KFOREIGN['feeds'])} feeds")

    enabled = cfg.setdefault("enabled_topics", [])
    if "kforeign" not in enabled:
        enabled.insert(0, "kforeign")
    notes.append(f"keywords.json: enabled_topics = {', '.join(enabled)}")

    doms = cfg.setdefault("allowed_domains", [])
    added = [d for d in ADD_DOMAINS if d not in doms]
    doms.extend(added)
    notes.append(f"keywords.json: allowed {len(added)} more publishers")

    boosts = cfg.setdefault("boost_phrases", [])
    added = [b for b in ADD_BOOSTS if b not in boosts]
    boosts.extend(added)
    notes.append(f"keywords.json: +{len(added)} exploitation-beat boosts")

    notes.append(f"keywords.json: {len(blocked)} terms blocked in total")

    backup = KEYWORDS.with_suffix(".json.bak3")
    if not backup.exists():
        shutil.copy2(KEYWORDS, backup)
    KEYWORDS.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return notes


def main() -> int:
    check_repo()
    p = Patcher()
    patch_sources(p)
    patch_run(p)
    if not p.commit():
        return 1
    kw_notes = patch_keywords()

    print("patched:")
    for note in p.notes + kw_notes:
        print(f"  + {note}")
    for note in p.skipped:
        print(f"  = {note}")
    print("\n  (.bak3 written beside each changed file on the first run)")
    print("""
YOU NOW OWN THE BLOCK LIST -- no more waiting on me:

    python run.py keywords block   <terms>    kill a story or a category
    python run.py keywords unblock <terms>    undo one
    python run.py keywords blocked            see the whole list

NEXT, IN THIS ORDER:

  1. python run.py keywords check-feeds --prune
     Five new Korean feeds I could not reach from my end. 일다 and
     여성신문 are the likeliest to have moved.

  2. python run.py keywords preview
     Expect: kforeign at the top when it has anything at all, the three
     리트 만점 headlines gone, 손흥민 gone, the cable car gone.
     kforeign may be empty on a given day -- it is a narrower beat than
     kscandal, which is why kscandal stays enabled underneath it.

  3. git add -A && git commit -m "foreigner-in-korea topic first; block \\
     CLI; drop dead thread, sport, local disputes" && git push
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
