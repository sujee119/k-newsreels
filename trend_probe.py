#!/usr/bin/env python3
"""Fetch today's Google Trends and show what a 'non-risky' filter leaves.

Run this before anything else is built. Three videos a day off ONE trend,
each from a different shuffle of the sources, rests on two unknowns:

  1. after the risky trends are dropped, is anything left at all?
  2. does a surviving trend carry enough DISTINCT sources to write three
     scripts that are not near-duplicates of each other?

The second is the real constraint. This answers both with today's data
instead of a guess.

    python3 trend_probe.py
    python3 trend_probe.py US GB          # specific geos

No API key. Google Trends has no official API; this uses the trending RSS
feed, trying both known endpoints because the path moved once already.
Nothing is written and nothing is posted.
"""
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

GEOS = ["US", "GB", "CA", "AU"]

# The path moved once; try the current one first and fall back.
ENDPOINTS = [
    "https://trends.google.com/trending/rss?geo={geo}",
    "https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}",
]

NS = {"ht": "https://trends.google.com/trending/rss"}

# A browser UA: the bot default gets 403 from Google, the same way it did
# on the publisher feeds in the reels pipeline.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# --- the risk filter, as words. Stage one only: cheap, and deliberately
# over-eager. The real pipeline adds an LLM classifier behind this, because
# "is this a named person" is not a keyword problem.
RISKY = {
    "death": ["dies", "died", "dead", "death", "killed", "kills", "fatal",
              "obituary", "passes away", "passed away", "funeral", "rip"],
    # Ambiguous words are given context. Bare "charges" matched a battery
    # charging in seconds, and bare "shot" matches photography and
    # basketball. A filter that eats the good topics is worse than no
    # filter, because it silently starves the channel.
    "crime": ["arrest", "arrested", "indicted", "court", "trial", "verdict",
              "guilty", "lawsuit", "sues", "suing", "allegation", "alleged",
              "accuser", "assault", "abuse", "shooting", "stabbed", "murder",
              "homicide", "fraud", "scandal", "prison", "jail",
              "charged with", "criminal charges", "faces charges",
              "shot dead", "shot and killed", "police say", "police report",
              "under investigation"],
    "politics": ["election", "elections", "senate", "congress", "parliament",
                 "president", "prime minister", "governor", "candidate",
                 "campaign", "impeach", "tariff", "sanctions", "protest",
                 "riot", "war", "strike", "ceasefire", "immigration",
                 "deportation", "abortion", "republican", "democrat"],
    "disaster": ["earthquake", "hurricane", "tornado", "wildfire", "flood",
                 "tsunami", "crash", "collapse", "explosion", "evacuation",
                 "missing", "rescue"],
    "medical": ["diagnosis", "diagnosed", "cancer", "tumour", "tumor",
                "hospitalized", "hospitalised", "overdose", "suicide",
                "outbreak", "symptoms", "cure"],
    "personal": ["divorce", "split", "dating", "pregnant", "engaged",
                 "wedding", "breakup", "cheating", "feud", "slams",
                 "clapback", "apologizes", "apologises"],
}

# Not risky, just worthless: a trend with nothing to explain. "Powerball
# numbers" passed the risk filter cleanly and would have made a video about
# nothing, so these are dropped on value rather than safety.
LOW_VALUE = [
    "powerball", "mega millions", "lottery", "lotto", "jackpot numbers",
    "vs", "score", "scores", "final score", "box score", "standings",
    "where to watch", "live stream", "streaming", "what time", "tv channel",
    "stock price", "share price", "near me", "login", "down", "outage",
    "weather forecast", "results today", "winning numbers",
]

# Shapes that are usually a person: two or three capitalised words, no
# obvious brand or place marker. Crude on purpose -- it only has to flag
# for review, not decide.
PERSON_SHAPE = re.compile(r"^[A-Z][a-z]+(?:\s[A-Z][a-z'\-]+){1,2}$")
NOT_A_PERSON = {
    "new", "the", "of", "city", "state", "united", "south", "north", "east",
    "west", "day", "world", "cup", "league", "open", "park", "island",
    "university", "airlines", "motors", "store", "bank", "news", "weather",
    "stock", "price", "prices", "today", "live", "score", "results",
}


def fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def feed_for(geo: str):
    """Return (url, items) for the first endpoint that parses."""
    problems = []
    for tmpl in ENDPOINTS:
        url = tmpl.format(geo=geo)
        try:
            raw = fetch(url)
        except urllib.error.HTTPError as exc:
            problems.append(f"{url} -> HTTP {exc.code}")
            continue
        except Exception as exc:
            problems.append(f"{url} -> {type(exc).__name__}: {str(exc)[:70]}")
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            problems.append(f"{url} -> not XML ({str(exc)[:50]})")
            continue
        items = root.findall(".//item")
        if items:
            return url, items, problems
        problems.append(f"{url} -> parsed but zero items")
    return "", [], problems


def text(node, path: str) -> str:
    el = node.find(path, NS)
    return (el.text or "").strip() if el is not None and el.text else ""


def parse(item) -> dict:
    news = []
    for n in item.findall("ht:news_item", NS):
        news.append({
            "title": text(n, "ht:news_item_title"),
            "url": text(n, "ht:news_item_url"),
            "source": text(n, "ht:news_item_source"),
        })
    return {
        "query": text(item, "title"),
        "traffic": text(item, "ht:approx_traffic"),
        "picture": text(item, "ht:picture"),
        "news": news,
    }


def risk_of(trend: dict) -> list[str]:
    """Which risky categories this trend trips, by word match across the
    query and every related headline."""
    blob = (trend["query"] + " " + " ".join(
        n["title"] for n in trend["news"])).lower()
    hits = []
    for name, words in RISKY.items():
        if any(re.search(rf"\b{re.escape(w)}\b", blob) for w in words):
            hits.append(name)
    return hits


def looks_like_person(query: str) -> bool:
    if not PERSON_SHAPE.match(query.strip()):
        return False
    return not any(w.lower() in NOT_A_PERSON for w in query.split())


def low_value(trend: dict) -> str:
    blob = trend["query"].lower()
    for w in LOW_VALUE:
        if re.search(rf"\b{re.escape(w)}\b", blob):
            return w
    return ""


def main() -> None:
    geos = [g.upper() for g in sys.argv[1:]] or GEOS
    print(__doc__.splitlines()[0])
    print()

    totals = {"seen": 0, "clean": 0, "risky": 0, "person": 0, "no_news": 0}
    clean_examples = []

    for geo in geos:
        url, items, problems = feed_for(geo)
        print(f"=== {geo}")
        if not items:
            print("  could not read the feed:")
            for p in problems:
                print(f"    {p}")
            print()
            continue
        if problems:
            print(f"  (first endpoint failed: {problems[0]})")
        print(f"  {url}")
        print(f"  {len(items)} trends")
        print()

        for item in items:
            t = parse(item)
            totals["seen"] += 1
            risks = risk_of(t)
            person = looks_like_person(t["query"])
            if risks:
                totals["risky"] += 1
            if not t["news"]:
                totals["no_news"] += 1

            junk = low_value(t)
            if risks:
                tag = "DROP"
                why = ", ".join(risks)
            elif junk:
                tag = "JUNK"
                why = f"nothing to explain ({junk})"
            elif not t["news"]:
                tag = "THIN"
                why = "no related news, nothing to build content from"
            elif person:
                # Person-shaped with no risky words at all -- "Marie Curie",
                # whose notebooks are still radioactive, is exactly the kind
                # of video this channel wants. A keyword cannot tell that
                # from a living person in a news cycle, so this is the class
                # the LLM classifier decides; never auto-dropped, never
                # auto-posted.
                tag = "CHECK"
                why = "person-shaped but clean -- LLM decides"
                totals["person"] += 1
            else:
                tag = "KEEP"
                why = ""
                totals["clean"] += 1
                clean_examples.append((geo, t))

            pic = "pic" if t["picture"] else "no-pic"
            print(f"  [{tag:5}] {t['query'][:42]:44} {t['traffic'] or '-':>9}"
                  f"  {len(t['news'])} news  {pic}")
            if why:
                print(f"         why: {why}")
            for n in t["news"][:2]:
                print(f"         - {n['title'][:78]}")
        print()

    print("=" * 72)
    print(f"seen {totals['seen']}   kept {totals['clean']}   "
          f"dropped-risky {totals['risky']}   person-shaped {totals['person']}"
          f"   no-news {totals['no_news']}")
    print()

    if not totals["clean"]:
        print("Nothing survived the filter today. That is the finding: the "
              "pipeline needs a fallback pool of evergreen topics for days "
              "when the trend list is all people and disasters.")
        return

    # Three variations need three distinguishable source sets. Overlapping
    # is fine -- identical is not, and the similarity guard would reject a
    # second script written from the same single article anyway.
    MIN_FOR_THREE = 6
    print(f"Source depth per usable trend (need ~{MIN_FOR_THREE} distinct "
          f"sources to write 3 non-duplicate scripts):")
    deep = 0
    for geo, t in sorted(clean_examples, key=lambda x: -len(x[1]["news"])):
        srcs = {n["source"] for n in t["news"] if n["source"]}
        enough = len(t["news"]) >= MIN_FOR_THREE
        deep += bool(enough)
        mark = "ok " if enough else "thin"
        print(f"  [{mark}] [{geo}] {t['query'][:38]:40} "
              f"{len(t['news'])} articles from {len(srcs)} outlets")

    print()
    if deep:
        print(f"{deep} trend(s) deep enough for three variations off the RSS "
              f"alone.")
    else:
        print("No trend has enough sources from the RSS alone -- expected, "
              "since the feed only carries a few articles each.")
    print("Either way the pipeline will widen each trend with a Google News "
          "search for the term (already decoded in core/sources.py) plus a "
          "Wikipedia lookup, which should take any trend to 10-20 sources. "
          "This number is the floor, not the ceiling.")

    print("\nWhat today's hook would be:")
    for geo, t in clean_examples[:4]:
        print(f'  [{geo}] "{t["query"].upper()} IS NOT WHAT YOU THINK"')
        for n in t["news"][:3]:
            print(f'        v{t["news"].index(n)+1} source: '
                  f'{n["title"][:62]}')


if __name__ == "__main__":
    main()
