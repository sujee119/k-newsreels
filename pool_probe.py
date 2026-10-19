#!/usr/bin/env python3
"""Build today's topic pool for @isnotwhatyouthink from every source, and
show what survives.

The trend probe showed why this exists: Google Trends daily searches are
names and tragedies, not concepts. "LAUNCH IS NOT WHAT YOU THINK" is not a
video. So the pool is assembled from sources that are concept-shaped, and
Trends is demoted to a ranking signal -- a topic that is ALSO being
searched today ranks higher, but Trends no longer decides what gets made.

Sources, each independently failure-tolerant:

  wikipedia  yesterday's most-viewed articles. What people actually looked
             up, but shaped as subjects rather than events, and each one
             carries a full article -- deep enough for three variations
             without depending on a widening step.
  reddit     r/todayilearned and r/explainlikeimfive top of the day.
             Literally this format, pre-selected for surprise by millions.
  hn         Hacker News front page, for the technical end.
  trends     Google Trends, as a boost only.

Nothing is written and nothing is posted.

    python3 pool_probe.py
    python3 pool_probe.py --no-enrich      # skip the Wikipedia summaries
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

TRENDS_GEOS = ["US", "GB", "CA", "AU"]
TRENDS_ENDPOINTS = [
    "https://trends.google.com/trending/rss?geo={geo}",
    "https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}",
]
HT = {"ht": "https://trends.google.com/trending/rss"}

WIKI_TOP = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
            "en.wikipedia/all-access/{y}/{m}/{d}")
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"

REDDIT_FEEDS = [
    ("til", "https://www.reddit.com/r/todayilearned/top/.rss?t=day"),
    ("eli5", "https://www.reddit.com/r/explainlikeimfive/top/.rss?t=day"),
]
HN_FRONT = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=40"


# --------------------------------------------------------------- the filter

RISKY = {
    "death": ["dies", "died", "dead", "death", "deaths", "killed", "kills",
              "kill", "fatal", "fatally", "obituary", "passes away",
              "passed away", "funeral", "mourns", "tribute", "tue", "tué",
              "mort", "décès"],
    "crime": ["arrest", "arrested", "indicted", "court", "trial", "verdict",
              "guilty", "lawsuit", "sues", "suing", "allegation",
              "allegations", "alleged", "accuser", "assault", "abuse",
              "shooting", "shootings", "stabbed", "murder", "homicide",
              "fraud", "scandal", "prison", "jail", "charged with",
              "criminal charges", "faces charges", "shot dead",
              "shot and killed", "police say", "under investigation",
              "vandalise", "vandalize", "vandalised", "vandalized",
              "terror", "terrorist", "bomb threat", "bomb threats"],
    "politics": ["election", "elections", "referendum", "senate", "congress",
                 "parliament", "president", "presidential", "prime minister",
                 "governor", "candidate", "campaign", "impeach", "tariff",
                 "tariffs", "sanction", "sanctions", "protest", "protests",
                 "riot", "rioting", "riots", "war", "ceasefire", "troops",
                 "missile", "missiles", "strike", "strikes", "airstrike",
                 "immigration", "deportation", "abortion", "republican",
                 "republicans", "democrat", "democrats", "gop", "dems",
                 "separatism", "separation from", "minister", "diplomat",
                 "zelenskyy", "trump", "putin", "netanyahu"],
    "disaster": ["earthquake", "hurricane", "typhoon", "tornado", "wildfire",
                 "flood", "flooding", "tsunami", "crash", "crashes",
                 "collapse", "collapsed", "explosion", "evacuation",
                 "evacuated", "missing", "rescue", "accident"],
    "medical": ["diagnosis", "diagnosed", "cancer", "tumour", "tumor",
                "hospitalized", "hospitalised", "overdose", "suicide",
                "outbreak", "symptoms", "cure", "coma"],
    "personal": ["divorce", "divorced", "split", "splits", "separation",
                 "dating", "pregnant", "pregnancy", "engaged", "wedding",
                 "breakup", "cheating", "feud", "slams", "clapback",
                 "apologizes", "apologises", "ouster", "under fire"],
}

LOW_VALUE = [
    "powerball", "mega millions", "lottery", "lotto", "jackpot",
    "vs", "score", "scores", "final score", "box score", "standings",
    "where to watch", "live stream", "streaming", "what time", "tv channel",
    "stock", "stock price", "share price", "near me", "login", "sign in",
    "outage", "weather", "forecast", "results today", "winning numbers",
    "horoscope", "zodiac", "connections hints", "wordle", "crossword",
    "mail", "live", "news", "today", "exam", "fixtures", "lineup",
    "highlights", "full match", "episode", "season", "trailer", "renders",
]

# Wikipedia namespaces and list pages that are never a topic.
WIKI_SKIP = re.compile(
    r"^(Main_Page|Special:|Portal:|Wikipedia:|Help:|Category:|Template:|"
    r"File:|Talk:|Draft:)|^List_of|^Lists_of|^Deaths_in|^\d{4}$|"
    r"\(disambiguation\)|^20\d\d_|_filmography$", re.I)

# Wikipedia's most-viewed list is dominated by whatever released this
# week: films, series, games, PPV events. The traffic is real but there is
# nothing to explain -- "Street Fighter (2026 film) is not what you think"
# is a movie page, not a video. Caught on the description, which states the
# type plainly.
MEDIA_DESC = re.compile(
    r"\b(\d{4}\s+)?(american|indian|british|canadian|japanese|korean|"
    r"french|australian|chinese|spanish|italian|german)?\s*"
    r"(film|films|movie|miniseries|television series|tv series|web series|"
    r"sitcom|anime|video game|mobile game|album|studio album|ep|single|"
    r"song|soundtrack|pay-per-view|wrestling event|season|episode|"
    r"streaming television series|documentary film|short film|novel)\b",
    re.I)

# A dead person's description carries both years: "(1902-1984)". Those are
# the good ones -- Tenzing Norgay and Ansel Adams both have a surprising
# story and no living-person risk at all.
DECEASED = re.compile(r"\((?:c\.\s*)?\d{3,4}\s*[-–—]\s*\d{3,4}\)")

# A Wikipedia one-line description that means "this is a person".
PERSON_DESC = re.compile(
    r"\b(actor|actress|singer|rapper|musician|footballer|player|athlete|"
    r"politician|president|senator|minister|businessman|businesswoman|"
    r"entrepreneur|journalist|author|writer|director|producer|model|"
    r"comedian|boxer|wrestler|cricketer|driver|coach|manager|"
    r"youtuber|streamer|influencer|personality|born \d{4}|"
    r"\(\d{4}[-–]\d{0,4}\))\b", re.I)


def risk_of(text: str) -> list[str]:
    blob = " " + text.lower() + " "
    return [name for name, words in RISKY.items()
            if any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", blob)
                   for w in words)]


def low_value(text: str) -> str:
    blob = text.lower()
    for w in LOW_VALUE:
        if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", blob):
            return w
    return ""


# Wikipedia gives a subject ("Daylight saving time"). Reddit and Hacker
# News give a CLAIM ("honey never spoils and 3000-year-old honey is still
# edible"), and pasting that into the template produces "HONEY NEVER
# SPOILS AND ... IS NOT WHAT YOU THINK", which is not a sentence. So the
# hook subject is derived, never copied. The real pipeline hands this to
# the LLM; this heuristic exists so the probe can show whether a source's
# topics are even capable of yielding a subject.
_LEAD = re.compile(
    r"^(til\s+|eli5[:\s]+|why\s+|how\s+|what\s+|when\s+|where\s+|"
    r"does\s+|do\s+|did\s+|is\s+|are\s+|was\s+|were\s+|can\s+|could\s+|"
    r"the\s+|a\s+|an\s+|that\s+|show\s+hn[:\s]+|ask\s+hn[:\s]+)+", re.I)
_STOP_AT = {
    "never", "always", "is", "are", "was", "were", "can", "could", "will",
    "would", "has", "have", "had", "does", "do", "did", "makes", "made",
    "look", "looks", "seem", "seems", "actually", "really", "still",
    "because", "when", "which", "that", "who", "and", "but", "of", "in",
    "on", "for", "to", "from", "with", "why", "how",
}


def hook_subject(topic: str, source: str) -> str:
    """A 1-3 word subject the template can carry."""
    if source == "wikipedia":
        # "Street Fighter (2026 film)" -> "Street Fighter". Wikipedia's
        # disambiguators are for Wikipedia, not for a hook.
        return re.sub(r"\s*\([^)]*\)\s*$", "", topic).strip() or topic
    s = _LEAD.sub("", topic.strip()).strip(" ,.:;?!\"'")
    words = re.findall(r"[\w'\-]+", s)
    picked: list[str] = []
    for w in words:
        if w.lower() in _STOP_AT and picked:
            break
        picked.append(w)
        if len(picked) >= 3:
            break
    return " ".join(picked) or topic.split()[0] if topic.split() else topic


def dedup_key(topic: str) -> str:
    words = sorted(w for w in re.findall(r"[a-z0-9]+", topic.lower())
                   if len(w) > 2)
    return " ".join(words[:8])


# ------------------------------------------------------------------ fetching

def get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def get_json(url: str, timeout: int = 25) -> dict:
    return json.loads(get(url, timeout))


def src_wikipedia(limit: int = 80) -> tuple[list[dict], str]:
    """Yesterday's most-viewed articles. Yesterday because today's counts
    are still being filled in and rank badly."""
    day = dt.date.today() - dt.timedelta(days=1)
    url = WIKI_TOP.format(y=day.year, m=f"{day.month:02d}", d=f"{day.day:02d}")
    data = get_json(url)
    arts = (data.get("items") or [{}])[0].get("articles") or []
    out = []
    for a in arts:
        title = a.get("article", "")
        if not title or WIKI_SKIP.search(title):
            continue
        out.append({
            "source": "wikipedia",
            "topic": title.replace("_", " "),
            "key": title,
            "views": a.get("views", 0),
            "detail": "",
            "url": f"https://en.wikipedia.org/wiki/{title}",
        })
        if len(out) >= limit:
            break
    return out, url


def enrich_wikipedia(items: list[dict], pause: float = 0.35) -> None:
    """Pull each candidate's one-line description. This is what makes the
    person filter work: 'American actor' is unambiguous in a way a title
    never is.

    24 of 80 came back empty at 0.15s between calls, and every miss
    becomes a CHECK the LLM then has to spend a call on, so the pause is
    longer and a 429 is retried once.
    """
    for it in items:
        if it["source"] != "wikipedia":
            continue
        d = None
        for attempt in (1, 2):
            try:
                q = urllib.parse.quote(it["key"], safe="")
                d = get_json(WIKI_SUMMARY.format(title=q), timeout=15)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503) and attempt == 1:
                    time.sleep(2.0)
                    continue
                it["enrich_error"] = f"HTTP {exc.code}"
                break
            except Exception as exc:
                it["enrich_error"] = type(exc).__name__
                break
        if d is None:
            it["detail"] = ""
            continue
        it["detail"] = (d.get("description") or "").strip()
        it["extract"] = (d.get("extract") or "").strip()
        it["picture"] = ((d.get("thumbnail") or {}).get("source") or "")
        time.sleep(pause)


def src_reddit() -> tuple[list[dict], list[str]]:
    out, notes = [], []
    for i, (tag, url) in enumerate(REDDIT_FEEDS):
        # Reddit 429'd the second feed when both were fetched back to back.
        if i:
            time.sleep(3.0)
        raw = None
        for attempt in (1, 2):
            try:
                raw = get(url)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt == 1:
                    time.sleep(6.0)
                    continue
                notes.append(f"{tag}: HTTP {exc.code}")
                break
            except Exception as exc:
                notes.append(f"{tag}: {type(exc).__name__}")
                break
        if raw is None:
            continue
        try:
            root = ET.fromstring(raw)
        except Exception as exc:
            notes.append(f"{tag}: not XML ({str(exc)[:40]})")
            continue
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns)[:30]:
            t = entry.find("a:title", ns)
            link = entry.find("a:link", ns)
            title = (t.text or "").strip() if t is not None else ""
            if not title:
                continue
            # "TIL that ..." / "ELI5: ..." -> the claim itself
            claim = re.sub(r"^(TIL\s+(that\s+)?|ELI5[:\s]+)", "", title,
                           flags=re.I).strip()
            out.append({
                "source": f"reddit/{tag}",
                "topic": claim[:110],
                "key": claim[:110],
                "views": 0,
                "detail": "",
                "url": (link.get("href") if link is not None else ""),
            })
        notes.append(f"{tag}: ok")
    return out, notes


def src_hn() -> tuple[list[dict], str]:
    data = get_json(HN_FRONT)
    out = []
    for h in data.get("hits", []):
        title = (h.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "source": "hn",
            "topic": title[:110],
            "key": title[:110],
            "views": h.get("points", 0),
            "detail": "",
            "url": h.get("url") or
                   f"https://news.ycombinator.com/item?id={h.get('objectID')}",
        })
    return out, HN_FRONT


# Common words that must never carry a boost on their own. "pascal" put a
# Pascal compiler at the top of the pool because a Quebec politician named
# Pascal Paradis was trending, and "space" boosted "latent space".
BOOST_STOPWORDS = {
    "news", "live", "today", "now", "new", "the", "and", "for", "with",
    "film", "movie", "game", "season", "show", "time", "day", "world",
    "space", "launch", "update", "best", "top", "free", "full", "open",
    "city", "state", "united", "south", "north", "east", "west", "league",
    "cup", "final", "score", "stock", "price", "weather", "mail", "login",
}


def src_trend_phrases() -> tuple[set[str], list[str]]:
    """Trends as whole queries, not a word bag.

    A boost now needs either the complete trend phrase to appear in the
    topic, or a single distinctive word -- long and not a common one.
    A bare common word matching is how a Quebec politician's first name
    promoted a Pascal compiler to the top of the pool.
    """
    phrases, notes = set(), []
    for geo in TRENDS_GEOS:
        got = False
        for tmpl in TRENDS_ENDPOINTS:
            try:
                raw = get(tmpl.format(geo=geo))
                root = ET.fromstring(raw)
            except Exception:
                continue
            for item in root.findall(".//item"):
                el = item.find("title")
                q = (el.text or "").strip().lower() if el is not None else ""
                q = re.sub(r"\s+", " ", q)
                if not q:
                    continue
                if " " in q:
                    phrases.add(q)
                elif len(q) >= 7 and q not in BOOST_STOPWORDS:
                    phrases.add(q)
                got = True
            if got:
                break
        notes.append(f"{geo}: {'ok' if got else 'unreachable'}")
    return phrases, notes


# ------------------------------------------------------------------ scoring

WEIGHT = {"wikipedia": 3.0, "reddit/til": 3.5, "reddit/eli5": 3.0, "hn": 2.0}


def classify(it: dict) -> tuple[str, str]:
    """Return (verdict, why). KEEP / CHECK / DROP / JUNK."""
    text = f"{it['topic']} {it.get('detail','')} {it.get('extract','')[:300]}"
    risks = risk_of(text)
    if risks:
        return "DROP", ", ".join(risks)
    junk = low_value(it["topic"])
    if junk:
        return "JUNK", f"nothing to explain ({junk})"
    detail = it.get("detail", "")
    if detail and MEDIA_DESC.search(detail):
        return "JUNK", f"media release ({detail[:40]})"
    if detail and PERSON_DESC.search(detail):
        if DECEASED.search(detail):
            # Historical figure, non-risky angle: the best material on the
            # list. Kept, and flagged so the LLM still sees it is a person.
            it["historical"] = True
            return "KEEP", ""
        return "CHECK", f"living person: {detail[:40]}"
    if it["source"] == "wikipedia" and not detail:
        # No description fetched, so the person check never ran.
        return "CHECK", "no description -- person check did not run"
    return "KEEP", ""


def score(it: dict, trend_phrases: set[str]) -> float:
    s = WEIGHT.get(it["source"], 1.0)
    blob = " " + re.sub(r"[^a-z0-9 ]+", " ", it["topic"].lower()) + " "
    blob = re.sub(r"\s+", " ", blob)
    hits = [p for p in trend_phrases
            if f" {p} " in blob or (" " not in p and f" {p} " in blob)]
    if hits:
        s += 2.5 + 0.3 * min(4, len(hits))
        it["trending_on"] = sorted(hits)[:3]
    if it.get("historical"):
        s += 1.2
    # A multi-word subject carries the hook better than a bare noun:
    # "DAYLIGHT SAVING TIME IS NOT WHAT YOU THINK" works, "LAUNCH" does not.
    n = len(it["topic"].split())
    if n == 1:
        s -= 1.5
    elif 2 <= n <= 7:
        s += 0.8
    if it.get("picture"):
        s += 0.4
    if it["source"] == "wikipedia" and it["views"]:
        s += min(1.5, it["views"] / 400_000)
    return s


# --------------------------------------------------------------------- main

def main() -> None:
    enrich = "--no-enrich" not in sys.argv
    print(__doc__.splitlines()[0].rstrip(","))
    print()

    items: list[dict] = []

    for label, fn in (("wikipedia", src_wikipedia), ("hn", src_hn)):
        try:
            got, note = fn()
            items += got
            print(f"  {label:10} {len(got):3} candidates   {note[:62]}")
        except Exception as exc:
            print(f"  {label:10}   0 candidates   FAILED: "
                  f"{type(exc).__name__} {str(exc)[:50]}")

    try:
        got, notes = src_reddit()
        items += got
        print(f"  {'reddit':10} {len(got):3} candidates   {', '.join(notes)}")
    except Exception as exc:
        print(f"  {'reddit':10}   0 candidates   FAILED: {str(exc)[:50]}")

    try:
        trend_words, notes = src_trend_words()
        print(f"  {'trends':10} {len(trend_words):3} words (boost only) "
              f"  {', '.join(notes)}")
    except Exception as exc:
        trend_words = set()
        print(f"  {'trends':10}   0 words        FAILED: {str(exc)[:50]}")

    if not items:
        sys.exit("\nEvery source failed. Nothing to judge.")

    if enrich:
        wiki = [i for i in items if i["source"] == "wikipedia"]
        print(f"\n  enriching {len(wiki)} Wikipedia candidates with their "
              f"one-line descriptions...")
        enrich_wikipedia(wiki)
        failed = sum(1 for i in wiki if i.get("enrich_error"))
        if failed:
            print(f"  ({failed} descriptions could not be fetched)")

    # Same story often lands in Wikipedia, HN and Reddit at once. Keep the
    # heaviest-weighted copy and record where else it showed up, because
    # appearing in several sources is itself a signal.
    best: dict[str, dict] = {}
    for it in items:
        k = dedup_key(it["topic"])
        if not k:
            continue
        prev = best.get(k)
        if prev is None:
            it["also_in"] = []
            best[k] = it
        else:
            keep, drop = ((it, prev)
                          if WEIGHT.get(it["source"], 1) >
                             WEIGHT.get(prev["source"], 1)
                          else (prev, it))
            keep["also_in"] = sorted(set(prev.get("also_in", [])
                                         + it.get("also_in", [])
                                         + [drop["source"]]))
            best[k] = keep
    merged = len(items) - len(best)
    items = list(best.values())
    if merged:
        print(f"\n  merged {merged} duplicate(s) across sources")

    counts = {"KEEP": 0, "CHECK": 0, "DROP": 0, "JUNK": 0}
    for it in items:
        it["verdict"], it["why"] = classify(it)
        it["subject"] = hook_subject(it["topic"], it["source"])
        it["score"] = score(it, trend_words)
        if it.get("also_in"):
            it["score"] += 0.6 * len(it["also_in"])
        counts[it["verdict"]] += 1

    print()
    print("=" * 74)
    print(f"{len(items)} candidates   "
          + "   ".join(f"{k.lower()} {v}" for k, v in counts.items()))
    print("=" * 74)

    keeps = sorted([i for i in items if i["verdict"] == "KEEP"],
                   key=lambda i: -i["score"])
    print(f"\nTOP OF THE POOL -- what tomorrow would be made from:\n")
    for i, it in enumerate(keeps[:12], 1):
        trend = (" [trending: " + ", ".join(it["trending_on"]) + "]"
                 if it.get("trending_on") else "")
        also = (" also in " + ", ".join(it["also_in"])
                if it.get("also_in") else "")
        print(f"{i:2}. {it['topic'][:66]}")
        print(f"    {it['source']:12} score {it['score']:.1f}{trend}{also}")
        if it.get("detail"):
            print(f"    {it['detail'][:66]}")
        print(f'    HOOK: "{it["subject"].upper()} IS NOT WHAT YOU THINK"')
        print()

    checks = [i for i in items if i["verdict"] == "CHECK"][:6]
    if checks:
        print("SENT TO THE LLM GATE (person-shaped or undescribed):")
        for it in checks:
            print(f"  {it['topic'][:46]:48} {it['why'][:40]}")
        print()

    print(f"Usable today: {counts['KEEP']}. The plan needs 1 topic a day "
          f"with 3 variations.")
    if counts["KEEP"] < 5:
        print("That is thinner than it should be -- worth loosening the "
              "low-value list rather than the risk list.")


if __name__ == "__main__":
    main()
