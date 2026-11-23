#!/usr/bin/env python3
"""Make the Korea channel read Korean-language sources and run scandal,
crime, corruption and celebrity-controversy stories.

Run once from the repo root of k-newsreels:

    python3 tools/fix_kscandal.py

Idempotent: safe to run twice. If any single edit cannot find its anchor
the script writes nothing at all and says so, so a half-applied state is
not possible.

WHAT THIS CHANGES, AND WHY EACH PART IS NEEDED
----------------------------------------------

1. core/sources.py -- the keyword matcher. This is the part that makes the
   rest work. The matcher used `re.search(rf"\\b{kw}\\b", title)`. In Python
   `\\w` is Unicode-aware and Hangul syllables are word characters, so a
   Korean keyword only matches when it happens to stand alone:

       "\\b비리\\b"  in "회장 비리 의혹에 검찰"  -> True   (space after)
       "\\b의혹\\b"  in "회장 비리 의혹에 검찰"  -> False  (particle 에 attached)

   Korean is agglutinative: 의혹 appears as 의혹이 / 의혹에 / 의혹을 far more
   often than bare. So adding Korean keywords without this fix gives a
   filter that matches *sometimes* and looks like it works -- the exact
   failure the repo's own _blocked_pattern docstring warns about. For a
   term containing Hangul the anchors are dropped and it matches as a
   substring, which is correct for Korean: 의혹 inside 의혹이 is the same
   word, and there is no English-style prefix ambiguity to guard against.

2. core/sources.py -- the same fix for blocked_terms, plus a Korean block
   list. The English blocked terms cannot fire on a Korean headline at
   all, so without Korean entries the minors / sexual / self-harm / graphic
   guards would be switched off for exactly the new sources. The Korean
   list is added in keywords.json below and only works because of this fix.

3. core/sources.py -- MIN_SOURCE_WORDS. 120 counts whitespace tokens.
   Korean attaches particles to the noun (학교에서 is one token where
   English has three), so an equivalent article runs roughly 60% of the
   English count and good Korean articles were being rejected as too
   short. Korean-language text now gets its own floor of 70.

4. core/sources.py -- google_news() hardcoded hl=en-US&gl=US&ceid=US:en.
   A Korean query against the English edition returns almost nothing. A
   topic can now set "search_locale": "ko".

5. .github/workflows/build.yml -- `run.py build` never passed --max-tier,
   and argparse defaults it to "safe". The new topic is tier "caution", so
   without this the story is gathered, scored, and then rejected at
   pick time with "tier above --max-tier safe". Nothing would ever build.

6. core/script.py -- tells the script step the source may be Korean and
   that narration stays English. No new API cost: the prompt already says
   "Re-explain from scratch in spoken English", so translation is the same
   call, and translating satisfies the never-copy-three-consecutive-words
   rule automatically.

7. keywords.json -- the data. Unblocks 46 terms that were vetoing exactly
   this content (accused of, alleged, arrested, charged with, scandal
   vocabulary), keeps the other 81, adds Korean blocked terms, adds the
   kscandal topic with Korean society feeds, re-tiers and enables kwave
   with Korean entertainment feeds, and adds the Korean outlet hosts to
   allowed_domains.

WHAT IS DELIBERATELY STILL BLOCKED
----------------------------------
Scandal that is journalism travels and monetises: corruption, fraud,
corporate wrongdoing, licensing fronts, celebrity controversy, official
apologies. Scandal that is sexual, involves minors, is graphic, or shames
a person's body does not get a channel banned -- it gets it quietly
throttled, because YouTube's advertiser-friendly guidelines restrict
suggestive content and Instagram holds it out of Reels *recommendations*,
which is where the views come from. The symptom is unexplained declining
reach with no strike and no notice. So minors, sexual content, sexual-image
abuse, suicide and self-harm, graphic violence and death, and appearance
shaming stay blocked in both languages, and the Korean list closes the
hole the English-only list left open.

The massage story that did well is the first kind: a licensing and
enforcement story about businesses fronting illegal activity.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
SOURCES = ROOT / "core" / "sources.py"
SCRIPT = ROOT / "core" / "script.py"
KEYWORDS = ROOT / "keywords.json"
BUILD_YML = ROOT / ".github" / "workflows" / "build.yml"

# ---------------------------------------------------------------- repo check

def check_repo() -> None:
    try:
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        remote = ""
    if "k-newsreels" not in remote:
        sys.exit(f"! this patch is for the k-newsreels repo; origin is '{remote}'\n"
                 f"  run it from ~/singlescape/k-newsreels")
    for p in (SOURCES, SCRIPT, KEYWORDS, BUILD_YML):
        if not p.exists():
            sys.exit(f"! {p.relative_to(ROOT)} not found -- wrong directory?")


# ------------------------------------------------------------ edit machinery

class Patcher:
    """Collects edits in memory and writes only if every edit succeeded."""

    def __init__(self) -> None:
        self.texts: dict[Path, str] = {}
        self.notes: list[str] = []
        self.failed: list[str] = []
        self.skipped: list[str] = []

    def load(self, path: Path) -> str:
        if path not in self.texts:
            self.texts[path] = path.read_text(encoding="utf-8")
        return self.texts[path]

    def edit(self, path: Path, label: str, anchor: str, new: str,
             *, done_marker: str, before: bool = False) -> None:
        """Insert `new` after (or before) the unique `anchor`.

        done_marker must be text that exists only once the edit has been
        applied and is NOT a substring of the anchor -- otherwise a second
        run reports success having changed nothing, or an edit silently
        skips. (That collision cost a whole feature once: config.STOCK_VIDEO
        is a substring of STOCK_VIDEO_MIN_PX.)
        """
        text = self.load(path)
        assert done_marker not in anchor, f"{label}: marker inside anchor"
        if done_marker in text:
            self.skipped.append(f"{label} (already applied)")
            return
        n = text.count(anchor)
        if n != 1:
            self.failed.append(f"{label}: anchor found {n} times, expected 1")
            return
        self.texts[path] = (text.replace(anchor, new + anchor, 1) if before
                            else text.replace(anchor, anchor + new, 1))
        self.notes.append(label)

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
            print("\n  The file has probably moved on since this patch was written.")
            print("  Send me the output above and I will re-anchor it.")
            return False
        for path, text in self.texts.items():
            backup = path.with_suffix(path.suffix + ".bak")
            if not backup.exists():
                shutil.copy2(path, backup)
            path.write_text(text, encoding="utf-8")
        return True


# -------------------------------------------------------- 1-4: core/sources.py

MATCHER_HELPERS = '''

# Korean-language sources need their own floor. MIN_SOURCE_WORDS counts
# whitespace tokens, and Korean attaches particles to the noun -- 학교에서 is
# one token where English has "at the school", three. An equivalent article
# runs around 60% of the English count, so 120 rejected good Korean copy.
MIN_SOURCE_WORDS_KO = 70

# Hangul syllables, Jamo, and the compatibility block.
_HANGUL = re.compile(r"[\\uac00-\\ud7a3\\u1100-\\u11ff\\u3130-\\u318f]")


def _has_hangul(s: str) -> bool:
    return bool(_HANGUL.search(s or ""))


def _enough_text(text: str) -> tuple[bool, int]:
    """(long enough, word count), with a lower bar for Korean."""
    words = len((text or "").split())
    floor = MIN_SOURCE_WORDS_KO if _has_hangul(text[:4000]) else MIN_SOURCE_WORDS
    return words >= floor, words


def _term_matches(term: str, haystack: str) -> bool:
    r"""Keyword match that works in both languages.

    English keeps \\b anchors: without them "chip" matches "chipotle". Korean
    cannot use them. In Python \\w is Unicode-aware and Hangul counts as a
    word character, so \\b의혹\\b fails against 의혹에 -- the particle is
    attached, there is no boundary. Since 의혹 inside 의혹이/의혹에/의혹을 is
    the same word and Korean has no English-style prefix ambiguity, a
    Hangul term matches as a plain substring.
    """
    if _has_hangul(term):
        return term in haystack
    return bool(re.search(rf"\\b{re.escape(term)}\\b", haystack))
'''

BLOCKED_HANGUL = '''    if _has_hangul(term):
        # No \\b (see _term_matches) and no English suffix stemming: Korean
        # inflects by attaching particles, which the substring already covers.
        return re.escape(term.strip())
'''

SCRIPT_RULE = '''
13. THE SOURCE MAY BE KOREAN. Some articles arrive in Korean. Narrate in
    English regardless -- the audience is English-speaking. Translate the
    meaning, do not transliterate: render 의혹 as "allegations", 압수수색 as
    "a police raid", 갑질 as "abuse of position by a superior", 재벌 as
    "family-controlled conglomerate". Give a person's name in standard
    romanisation with the family name first (Kim Min-jun), an organisation
    by its usual English name if it has one (Hankyoreh, Samsung, the
    National Assembly), and a place as it appears on an English map
    (Gangnam, Busan, Jeju). Translating also satisfies rule 1 by itself:
    a translation cannot copy three consecutive words of the original.

14. SCANDAL IS A STORY, NOT A VERDICT. Say who alleges what, and who denies
    it. "Prosecutors say" and "the company denies" are not hedging, they
    are the facts. An allegation is not a finding; an indictment is not a
    conviction; a raid is not proof. Never name or identify a private
    individual who is not a public figure, and never describe a minor.
    If the story only works by implying something the source does not
    state, drop the story -- say so instead of writing it.
'''


def patch_sources(p: Patcher) -> None:
    # 1 + 3: helpers (also carries _has_hangul, the idempotency marker)
    p.edit(SOURCES, "sources.py: Korean word floor + matcher helpers",
           "MIN_SOURCE_WORDS = 120\n",
           MATCHER_HELPERS,
           done_marker="def _has_hangul")

    # 2: blocked terms
    p.swap(SOURCES, "sources.py: blocked_terms match Korean",
           '''    parts = []
    for word in term.lower().split():''',
           BLOCKED_HANGUL + '''    parts = []
    for word in term.lower().split():''',
           done_marker="no English suffix stemming")

    # 2b: keyword loop
    p.swap(SOURCES, "sources.py: keywords match Korean",
           '''        where = title_low if len(kl.split()) == 1 else hay
        if re.search(rf"\\b{re.escape(kl)}\\b", where):
            hits.append(k)''',
           '''        where = title_low if len(kl.split()) == 1 else hay
        if _term_matches(kl, where):
            hits.append(k)''',
           done_marker="if _term_matches(kl, where)")

    # 2c: the title-match score bonus uses the same regex
    p.swap(SOURCES, "sources.py: title-match bonus matches Korean",
           '''    score += sum(1.5 for k in hits
                 if re.search(rf"\\b{re.escape(k.lower())}", story.title.lower()))''',
           '''    score += sum(1.5 for k in hits
                 if _term_matches(k.lower(), story.title.lower()))''',
           done_marker="_term_matches(k.lower(), story.title.lower())")

    # 3b: the length gate
    p.swap(SOURCES, "sources.py: length gate uses the Korean floor",
           '''        article = fetch_article(story.link)
        words = len((article or "").split())
        if words < MIN_SOURCE_WORDS:''',
           '''        article = fetch_article(story.link)
        long_enough, words = _enough_text(article or "")
        if not long_enough:''',
           done_marker="long_enough, words = _enough_text")

    # 4: Google News locale
    p.swap(SOURCES, "sources.py: google_news accepts a locale",
           '''def google_news(query: str, topic: str, tier: str,
                limit: int = 12, context: str = "", days: int = 7) -> list[Story]:''',
           '''def google_news(query: str, topic: str, tier: str,
                limit: int = 12, context: str = "", days: int = 7,
                locale: str = "") -> list[Story]:''',
           done_marker='locale: str = ""')

    p.swap(SOURCES, "sources.py: google_news uses the Korean edition",
           '''    url = ("https://news.google.com/rss/search?"
           + urllib.parse.urlencode({
               "q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}))''',
           '''    # A Korean query against the English edition returns almost nothing,
    # so a topic reading Korean sources sets "search_locale": "ko".
    if locale == "ko":
        loc = {"hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    else:
        loc = {"hl": "en-US", "gl": "US", "ceid": "US:en"}
    url = ("https://news.google.com/rss/search?"
           + urllib.parse.urlencode({"q": q, **loc}))''',
           done_marker='if locale == "ko":')

    p.swap(SOURCES, "sources.py: gather() passes the topic locale",
           '''            context = spec.get("search_context", "")
            for kw in kws[:6]:        # keep the request count sane
                found += google_news(kw, topic, tier, context=context)''',
           '''            context = spec.get("search_context", "")
            locale = spec.get("search_locale", "")
            for kw in kws[:6]:        # keep the request count sane
                found += google_news(kw, topic, tier, context=context,
                                     locale=locale)''',
           done_marker='locale = spec.get("search_locale"')


def patch_script(p: Patcher) -> None:
    # Anchor on the end of the HARD RULES block. A regex over "N. UPPERCASE"
    # would also hit the vendored MoneyPrinterTurbo term prompt further down,
    # which has its own rules 1-6.
    p.swap(SCRIPT, "script.py: Korean-source + allegation rules (10, 11)",
           '''9. No call to action. No "follow for more". End on meaning.''',
           '''9. No call to action. No "follow for more". End on meaning.
'''
           + SCRIPT_RULE.replace("\n13.", "\n10.").replace("\n14.", "\n11.").rstrip("\n"),
           done_marker="THE SOURCE MAY BE KOREAN")


def patch_build_yml(p: Patcher) -> None:
    p.swap(BUILD_YML, "build.yml: pass --max-tier (argparse defaults to 'safe')",
           '''            python -u run.py build --count "${{ inputs.count || vars.REELS_PER_RUN || 1 }}"''',
           '''            # Without --max-tier, argparse defaults to "safe" and every
            # caution-tier story is rejected at pick time. Set MAX_TIER=safe
            # as a repo variable to switch the scandal topics off in one move.
            python -u run.py build \\
              --count "${{ inputs.count || vars.REELS_PER_RUN || 1 }}" \\
              --max-tier "${{ vars.MAX_TIER || 'caution' }}"''',
           done_marker="--max-tier \"${{ vars.MAX_TIER")

    p.swap(BUILD_YML, "build.yml: --max-tier for a single-story dispatch",
           '''            python -u run.py build --story "${{ inputs.story_url }}"''',
           '''            python -u run.py build --story "${{ inputs.story_url }}" \\
              --max-tier "${{ vars.MAX_TIER || 'caution' }}"''',
           done_marker="story_url }}\" \\\n              --max-tier")


# ------------------------------------------------------------- 7: keywords.json

# Terms that were blocking exactly the content now wanted. These are the
# vocabulary of accusation, legal process, corporate wrongdoing and
# celebrity controversy -- i.e. of the story, not of the harm.
UNBLOCK = [
    "accused of", "alleged", "alleged affair", "apologises", "apologizes",
    "apologizes after backlash", "arrested", "backlash over", "blasts",
    "box office", "breaks silence", "calls out", "canceled over",
    "cancelled over", "celebrity", "charged with", "cheating allegations",
    "clap back", "criticised for", "criticized for", "dating", "dating rumor",
    "dating rumour", "dating scandal", "denies claims", "divorce",
    "divorce filing", "draft dodging", "enlistment controversy", "fans slam",
    "feud", "fires back", "hits back", "lawsuit against", "netizens react",
    "netizens slam", "responds to", "secretly married", "shuts down",
    "slammed for", "slams", "sparks outrage", "split from", "sues",
    "under fire", "under fire for",
]

# The English list cannot fire on a Korean headline, so without these the
# minors / sexual / self-harm / graphic guards would be off for exactly the
# new sources. Works only because of the matcher fix above.
BLOCK_KO = [
    # minors
    "아동", "미성년", "미성년자", "초등학생", "중학생", "고등학생", "여중생",
    "여고생", "남중생", "남고생", "청소년", "유아", "영아",
    # sexual / image abuse
    "성폭행", "성폭력", "성추행", "성희롱", "강간", "준강간", "성매매",
    "성착취", "몰카", "불법촬영", "디지털 성범죄", "리벤지 포르노",
    "딥페이크", "유출 사진", "음란",
    # self-harm
    "자살", "극단적 선택", "자해", "유서", "투신",
    # graphic violence and death
    "살인", "살해", "시신", "사체", "흉기", "칼부림", "총격",
    "학대", "폭행", "납치", "인질", "참사", "사망자", "사상자", "부상자",
    "유족", "빈소", "분향소",
    # appearance and body shaming
    "다이어트", "성형", "몸매", "체중 감량",
    # fan-stalking and private-life intrusion
    "사생팬", "임신설", "낙태",
    # ads and SEO noise on Korean pages
    "협찬", "제휴", "수강생 모집", "무료 체험", "할인 코드",
    "구독하기", "경품", "오늘의 운세", "별자리 운세", "로또",
]
# Deliberately NOT blocked, after testing each one as a substring:
#   노출  -- "exposed to" in ordinary use ("미세먼지에 노출"), blocks clean stories
#   광고  -- blocks legitimate ad-market business coverage
# A blocked term that is misspelled matches nothing and looks like it works,
# so this list holds only terms whose spelling and usage I am sure of.

KSCANDAL = {
    "tier": "caution",
    "search_context": "한국",
    "search_locale": "ko",
    "keywords": [
        # Korean -- single tokens, matched in the title (substring, after
        # the matcher fix). A headline carrying one of these IS the story.
        "논란", "의혹", "비리", "횡령", "뇌물", "수사", "검찰", "구속",
        "기소", "압수수색", "적발", "단속", "불법", "탈세", "갑질",
        "폭로", "내부고발", "특혜", "유착", "사기", "담합", "과징금",
        "무허가", "위장", "해명", "고발", "재벌", "국정감사",
        # English -- for the English-language outlets still in the mix
        "corruption", "embezzlement", "bribery", "prosecutors", "indicted",
        "scandal", "crackdown", "tax evasion", "whistleblower", "fraud",
        "price-fixing", "chaebol", "raided", "probe into",
        "under investigation", "front for", "unlicensed", "counterfeit",
        "collusion", "no-show job", "abuse of power",
    ],
    "feeds": [
        # Korean-language society desks. Sourced from the feed spec in
        # akngs/knews-rss, which tracks these for breakage.
        "https://www.khan.co.kr/rss/rssdata/society_news.xml",
        "https://rss.donga.com/national.xml",
        "https://www.hani.co.kr/rss/society/",
        "https://www.chosun.com/arc/outboundfeeds/rss/category/national/?outputType=xml",
        "https://www.seoul.co.kr/xml/rss/rss_society.xml",
        "https://newsis.com/RSS/society.xml",
        "http://www.segye.com/Articles/RSSList/segye_society.xml",
        "http://www.sisajournal.com/rss/S1N47.xml",
        "https://www.sisain.co.kr/rss/S1N8.xml",
        "https://www.mediatoday.co.kr/rss/S1N4.xml",
    ],
    "hashtags": ["#koreanews", "#korea", "#southkorea", "#seoul"],
}

KWAVE_FEEDS = [
    "https://www.khan.co.kr/rss/rssdata/kh_entertainment.xml",
    "https://www.chosun.com/arc/outboundfeeds/rss/category/entertainments/?outputType=xml",
    "https://newsis.com/RSS/entertain.xml",
    "https://www.seoul.co.kr/xml/rss/rss_entertainment.xml",
    "http://www.segye.com/Articles/RSSList/segye_entertainment.xml",
]

KWAVE_KO_KEYWORDS = [
    "아이돌", "소속사", "연예인", "컴백", "활동 중단", "계약 분쟁",
    "전속계약", "열애", "결별", "입장문", "입장 밝혀", "팬덤", "시상식",
]

# Hosts of every feed above, plus the publishers those stories link to.
ADD_DOMAINS = [
    "khan.co.kr", "seoul.co.kr", "segye.com", "kmib.co.kr", "newsis.com",
    "sisajournal.com", "sisain.co.kr", "pressian.com", "mediatoday.co.kr",
    "ohmynews.com", "hankyung.com", "news1.kr", "nocutnews.co.kr",
    "ytn.co.kr", "sbs.co.kr", "jtbc.co.kr", "imbc.com", "labortoday.co.kr",
    "womennews.co.kr", "hankookilbo.com", "joongang.co.kr", "edaily.co.kr",
    "akngs.github.io",
]

# The existing boosts are all science-flavoured ("thought extinct",
# "100 million years") and never fire on Korean news.
ADD_BOOSTS = [
    # 결국 and 처음 are left out on purpose: they appear in a large share of
    # Korean headlines, so boosting them boosts everything and the boost
    # stops meaning anything.
    "논란", "의혹", "충격", "알고 보니", "밝혀졌다", "드러났다",
    "사상 최대", "사상 최초", "무더기", "꼼수", "뒷돈",
    "for years", "turns out", "quietly", "no one noticed", "first time",
    "largest ever", "record", "behind closed doors", "it was not",
]


def patch_keywords() -> tuple[bool, list[str]]:
    """keywords.json is data, so it is rewritten rather than text-patched."""
    notes: list[str] = []
    cfg = json.loads(KEYWORDS.read_text(encoding="utf-8"))

    if "kscandal" in cfg.get("topics", {}):
        return True, ["keywords.json (already applied)"]

    before = len(cfg.get("blocked_terms", []))
    unblock = {t.lower() for t in UNBLOCK}
    cfg["blocked_terms"] = [t for t in cfg["blocked_terms"]
                            if t.lower() not in unblock]
    removed = before - len(cfg["blocked_terms"])
    notes.append(f"keywords.json: unblocked {removed} scandal terms "
                 f"({len(cfg['blocked_terms'])} still blocked)")

    added_ko = [t for t in BLOCK_KO if t not in cfg["blocked_terms"]]
    cfg["blocked_terms"] = cfg["blocked_terms"] + added_ko
    notes.append(f"keywords.json: added {len(added_ko)} Korean blocked terms "
                 f"(minors, sexual, self-harm, graphic, shaming, ads)")

    doms = cfg.setdefault("allowed_domains", [])
    new_doms = [d for d in ADD_DOMAINS if d not in doms]
    doms.extend(new_doms)
    notes.append(f"keywords.json: allowed {len(new_doms)} Korean publishers")

    cfg["topics"]["kscandal"] = KSCANDAL
    notes.append(f"keywords.json: new topic kscandal "
                 f"(tier caution, {len(KSCANDAL['keywords'])} keywords, "
                 f"{len(KSCANDAL['feeds'])} Korean feeds)")

    kw = cfg["topics"].get("kwave")
    if kw is not None:
        kw["tier"] = "caution"
        kw["search_locale"] = "ko"
        kw["feeds"] = KWAVE_FEEDS
        kw["keywords"] = kw.get("keywords", []) + [
            k for k in KWAVE_KO_KEYWORDS if k not in kw.get("keywords", [])]
        notes.append("keywords.json: kwave re-tiered safe-for-auto (caution), "
                     f"{len(KWAVE_FEEDS)} Korean entertainment feeds, "
                     f"+{len(KWAVE_KO_KEYWORDS)} Korean keywords")

    enabled = cfg.setdefault("enabled_topics", [])
    for t in ("kscandal", "kwave"):
        if t not in enabled:
            enabled.append(t)
    notes.append(f"keywords.json: enabled_topics = {', '.join(enabled)}")

    boosts = cfg.setdefault("boost_phrases", [])
    new_boosts = [b for b in ADD_BOOSTS if b not in boosts]
    boosts.extend(new_boosts)
    notes.append(f"keywords.json: +{len(new_boosts)} Korean/news boost phrases")

    backup = KEYWORDS.with_suffix(".json.bak")
    if not backup.exists():
        shutil.copy2(KEYWORDS, backup)
    KEYWORDS.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True, notes


# -------------------------------------------------------------------- driver

def main() -> int:
    check_repo()
    p = Patcher()
    patch_sources(p)
    patch_script(p)
    patch_build_yml(p)

    if p.failed:
        p.commit()
        return 1

    if not p.commit():
        return 1

    ok, kw_notes = patch_keywords()

    print("patched:")
    for n in p.notes + kw_notes:
        print(f"  + {n}")
    if p.skipped:
        for n in p.skipped:
            print(f"  = {n}")
    print("\n  (.bak written beside each changed file on the first run)")

    print("""
NEXT, AND IN THIS ORDER -- the feeds are unverified from here:

  1. python run.py keywords check-feeds --prune
     Ten of the new URLs are Korean publishers I could not reach from
     this session, so some will be dead or moved. --prune drops whatever
     does not answer. Run it before you push, not after.

  2. python run.py keywords preview
     Lists what matched, with score, topic and which keywords hit --
     no build, no render, no upload. This is the one that tells you
     whether the scandal topics are finding the right stories. If the
     top of the list is stuff you would not publish, send me the output
     and I will retune the keywords rather than you pushing it.

  3. git add -A && git commit -m "korean-language scandal sources" && git push

  4. Optional switches, as repo variables -- no code change:
       gh variable set MAX_TIER --body safe   # scandal topics off again
       gh variable set MAX_TIER --body caution
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
