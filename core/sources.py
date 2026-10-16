"""Keyword-driven story discovery across many publishers.

Two kinds of source:

  1. Direct RSS feeds listed per topic in keywords.json. These are the good
     ones -- ScienceDaily, phys.org and EurekAlert publish full press
     releases, so there is real material to build a script from.

  2. Google News RSS search, which reaches essentially every publisher for
     any keyword. The catch is that it returns a headline and a sentence,
     then links to a site that may paywall or block us. Stories whose full
     text cannot be recovered are skipped rather than padded out by the
     model -- see MIN_SOURCE_WORDS.

Topic tiers control what may be posted unattended:

  safe       science, tech        -- normal pipeline
  caution    health               -- approval required, caveats enforced
  sensitive  conflict, markets,   -- approval required, never auto-post,
             crime                   extra uncertainty language required
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import config
from core.feed import _clean, _fetch, mark_seen, _load_seen, UA

KEYWORDS_PATH = config.ROOT / "keywords.json"

# Why the last fetch_article() call came back empty. Set by fetch_article,
# read by pick_story so the log says "HTTP 403" instead of "0w".
_LAST_FETCH_NOTE = ""

# A story needs this much recoverable text or the script will be padding.
MIN_SOURCE_WORDS = 120

TIER_ORDER = {"safe": 0, "caution": 1, "sensitive": 2}

# Minimum age before a story is eligible, in hours, per tier.
#
# Deliberately waiting is the cheapest accuracy fix available. Breaking
# news is wrong most often in its first hours -- casualty counts, attribution,
# "sources say" claims that get retracted. By the next morning the
# corrections have landed and the follow-up coverage is longer, which also
# gives the script real material instead of a headline.
MIN_AGE_HOURS = {
    "safe": 0,        # a press release does not get more true overnight
    "caution": 12,
    "sensitive": 24,  # yesterday's news, on purpose
}


@dataclass
class Story:
    uid: str
    title: str
    summary: str
    link: str
    published: str
    source_feed: str
    topic: str = "general"
    tier: str = "safe"
    matched: list = field(default_factory=list)
    score: float = 0.0
    article: str = ""
    publisher: str = ""

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["article"] = ""        # don't persist the whole article body
        return d


# ------------------------------------------------------------- keyword file

def load_keywords() -> dict:
    if not KEYWORDS_PATH.exists():
        return {"topics": {}, "enabled_topics": [], "blocked_terms": []}
    return json.loads(KEYWORDS_PATH.read_text())


def save_keywords(data: dict) -> None:
    KEYWORDS_PATH.write_text(json.dumps(data, indent=2))


def active_topics(cfg: dict) -> dict:
    enabled = cfg.get("enabled_topics") or list(cfg.get("topics", {}))
    return {k: v for k, v in cfg.get("topics", {}).items() if k in enabled}


# ----------------------------------------------------------------- fetching

def domain_of(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _parse_rss(xml_bytes: bytes, feed_url: str, topic: str,
               tier: str) -> list[Story]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    out = []
    for item in root.iter("item"):
        title = _clean(item.findtext("title", ""))
        summary = _clean(item.findtext("description", ""))
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if not (title and link):
            continue

        # Google News wraps links through its own domain, but names the real
        # publisher in <source url="...">. That is what we filter on.
        src = item.find("source")
        publisher = ""
        if src is not None:
            publisher = domain_of(src.get("url", "")) or (src.text or "").lower()
        if not publisher:
            publisher = domain_of(link)

        out.append(Story(
            uid=hashlib.sha1(link.encode()).hexdigest()[:16],
            title=title, summary=summary, link=link, published=pub,
            source_feed=feed_url, topic=topic, tier=tier,
            publisher=publisher))
    return out


def google_news(query: str, topic: str, tier: str,
                limit: int = 12, context: str = "", days: int = 7) -> list[Story]:
    """Keyword search across every publisher Google News indexes.

    `context` narrows a generic word to its field -- bare "breakthrough"
    returns horoscopes, "breakthrough research study" does not. `days`
    uses Google's own recency operator so we are not scoring stale items.
    """
    q = f"{query} {context}".strip()
    if days:
        q += f" when:{days}d"
    url = ("https://news.google.com/rss/search?"
           + urllib.parse.urlencode({
               "q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}))
    try:
        stories = _parse_rss(_fetch(url, timeout=15, attempts=2), f"google:{query}", topic, tier)
    except Exception as exc:
        print(f"  ! google news '{query}': {exc}")
        return []

    # Google wraps the publisher name into the title after a dash.
    for s in stories:
        s.title = re.sub(r"\s+-\s+[^-]{2,40}$", "", s.title).strip()
    return stories[:limit]


# ----------------------------------------------------------------- scoring

def _age_hours(pub: str) -> float:
    try:
        dt = parsedate_to_datetime(pub)
    except (TypeError, ValueError):
        return 9999.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


_SUFFIXES = ("ed", "es", "s", "ing", "d")


def _blocked_pattern(term: str) -> str:
    """Match a blocked term and its obvious inflections.

    A list containing "killed" has to catch "kills" and "killing" too --
    otherwise the filter looks like it works while letting the headline
    straight through, which is worse than no filter.
    """
    parts = []
    for word in term.lower().split():
        stem = word
        for suf in _SUFFIXES:
            if len(word) > len(suf) + 2 and word.endswith(suf):
                stem = word[: -len(suf)]
                break
        parts.append(rf"{re.escape(stem)}(?:e?s|e?d|ing)?")
    return r"\b" + r"\s+".join(parts) + r"\b"


def score_story(story: Story, keywords: list[str], blocked: list[str],
                allowed_domains: list[str] | None = None,
                blocked_domains: list[str] | None = None,
                boost_phrases: list[str] | None = None) -> float:
    """Keyword relevance plus a recency bonus. Negative means reject.

    Publisher filtering matters more than keyword tuning here. A keyword
    search across the open web returns astrology columns and sports
    previews for words like "breakthrough" and "first time"; restricting to
    known publishers removes nearly all of it in one step.
    """
    pub = (story.publisher or "").lower()

    for bad in (blocked_domains or []):
        if bad.lower() in pub:
            return -1.0

    if allowed_domains:
        if not any(ok.lower() in pub for ok in allowed_domains):
            return -1.0

    hay = f"{story.title} {story.summary}".lower()

    for term in blocked:
        if re.search(_blocked_pattern(term), hay):
            return -1.0

    title_low = story.title.lower()
    hits = []
    for k in keywords:
        kl = k.lower()
        # A one-word keyword matching only the blurb is usually incidental
        # ("chip" in a sportswriter's name). Require it in the title.
        where = title_low if len(kl.split()) == 1 else hay
        if re.search(rf"\b{re.escape(kl)}\b", where):
            hits.append(k)
    story.matched = hits
    if not hits:
        return 0.0

    score = len(hits) * 2.0
    # Title matches count double -- that's what the story is actually about.
    score += sum(1.5 for k in hits
                 if re.search(rf"\b{re.escape(k.lower())}", story.title.lower()))

    # Viral shape, not viral subject. A reversal or a superlative travels
    # regardless of field, and these are safe to chase in a way that
    # politics and outbreaks are not.
    boosts = [b for b in (boost_phrases or [])
              if b.lower() in story.title.lower()]
    if boosts:
        story.matched = story.matched + [f"*{b}" for b in boosts[:2]]
        score += 2.5 * len(boosts[:2])

    age = _age_hours(story.published)
    min_age = MIN_AGE_HOURS.get(story.tier, 0)
    if age < min_age:
        return -1.0     # too fresh to trust for this tier

    # Score relative to when the story became eligible, so a 25-hour-old
    # conflict story still counts as "fresh" for a tier that waits 24h.
    rel = age - min_age
    if rel <= 12:
        score += 3.0
    elif rel <= 36:
        score += 1.5
    elif age > 24 * config.MAX_STORY_AGE_DAYS:
        return -1.0

    return score


# ------------------------------------------------------------------ picking

def gather(cfg: dict | None = None, use_search: bool = True) -> list[Story]:
    """Collect and score candidate stories across all enabled topics."""
    cfg = cfg or load_keywords()
    blocked = cfg.get("blocked_terms", [])
    seen = _load_seen()
    found: list[Story] = []

    for topic, spec in active_topics(cfg).items():
        tier = spec.get("tier", "safe")
        kws = spec.get("keywords", [])

        for feed_url in spec.get("feeds", []):
            try:
                found += _parse_rss(_fetch(feed_url, timeout=15, attempts=2), feed_url, topic, tier)
            except Exception as exc:
                print(f"  ! feed {feed_url}: {exc}")

        if use_search:
            context = spec.get("search_context", "")
            for kw in kws[:6]:        # keep the request count sane
                found += google_news(kw, topic, tier, context=context)

    scored: list[Story] = []
    by_uid: dict[str, Story] = {}
    for s in found:
        if s.uid in seen or s.uid in by_uid:
            continue
        spec = cfg["topics"].get(s.topic, {})
        s.score = score_story(
            s, spec.get("keywords", []), blocked,
            allowed_domains=(spec.get("allowed_domains")
                             or cfg.get("allowed_domains", [])),
            blocked_domains=cfg.get("blocked_domains", []),
            boost_phrases=cfg.get("boost_phrases", []))
        if s.score <= 0:
            continue
        by_uid[s.uid] = s
        scored.append(s)

    scored.sort(key=lambda x: x.score, reverse=True)
    return scored


def pick_story(cfg: dict | None = None, max_tier: str = "sensitive",
               use_search: bool = True,
               exclude_uids: set | None = None) -> Story | None:
    """Best scoring story whose full text is actually recoverable."""
    cfg = cfg or load_keywords()
    cap = TIER_ORDER.get(max_tier, 2)

    candidates = gather(cfg, use_search=use_search)

    # Direct RSS entries link straight to a publisher that serves the full
    # press release. Google News entries link to an interstitial that may
    # not resolve at all. Try every direct one before any search one, so a
    # change at Google's end cannot starve a run while 40-odd healthy feeds
    # are sitting there.
    direct = [s for s in candidates if "news.google" not in s.link]
    searched = [s for s in candidates if "news.google" in s.link]
    ordered = direct + searched
    print(f"  {len(candidates)} candidate stories after keyword filtering "
          f"({len(direct)} from feeds, {len(searched)} via search)")

    reasons: dict[str, int] = {}

    def _note(why: str):
        reasons[why] = reasons.get(why, 0) + 1

    for story in ordered[:40]:
        if TIER_ORDER.get(story.tier, 0) > cap:
            _note(f"tier above --max-tier {max_tier}")
            continue
        if exclude_uids and story.uid in exclude_uids:
            _note("already built or pending")
            continue

        article = fetch_article(story.link)
        words = len((article or "").split())
        if words < MIN_SOURCE_WORDS:
            why = _LAST_FETCH_NOTE or f"only {words}w of text"
            _note(why)
            print(f"    skip ({why}): {story.title[:54]}")
            continue

        story.article = article
        print(f"    -> [{story.topic}/{story.tier}] score {story.score:.1f}, "
              f"{words}w, matched {story.matched}")
        return story

    # Nothing usable. This used to return None into a caller that treated it
    # as success, so a run could go green having built nothing. The caller
    # now fails; this explains what to actually fix.
    print(f"  ! none of {len(ordered)} candidates had recoverable full text")
    for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"      {n:>3} x {why}")
    if not candidates:
        print("      (no candidates at all -- check feed health with: "
              "python run.py keywords check-feeds)")
    return None


# -------------------------------------------------------- article retrieval

_BLOCK_RE = re.compile(
    r"<(script|style|aside|figure|nav|header|footer|form)[^>]*>.*?</\1>",
    re.S | re.I)


def fetch_article(url: str, max_chars: int = 12000) -> str:
    """Best-effort full text from an arbitrary news page.

    Many publishers paywall, require JS, or block us outright. That is
    expected and is why callers check the word count: a story we cannot
    read properly is skipped, never guessed at.
    """
    global _LAST_FETCH_NOTE
    _LAST_FETCH_NOTE = ""
    try:
        html = _fetch(url).decode("utf-8", "ignore")
    except Exception as exc:
        _LAST_FETCH_NOTE = getattr(exc, "reason", None) or type(exc).__name__
        return ""

    # Google News links redirect; follow via the embedded publisher URL.
    if "news.google.com" in url:
        m = re.search(r'<a[^>]+href="(https?://(?!news\.google)[^"]+)"', html)
        if not m:
            # Google changed its interstitial markup before and will again.
            # Say so plainly rather than returning an empty string that
            # looks identical to a paywall.
            _LAST_FETCH_NOTE = "google-news link unresolvable"
            return ""
        try:
            html = _fetch(m.group(1)).decode("utf-8", "ignore")
        except Exception as exc:
            _LAST_FETCH_NOTE = getattr(exc, "reason", None) or type(exc).__name__
            return ""

    html = _BLOCK_RE.sub(" ", html)

    for pattern in (
        r'<div[^>]+id="story_text".*?>(.*?)</div>\s*<div[^>]+id="story_source"',
        r'<article[^>]*>(.*?)</article>',
        r'<div[^>]+(?:class|id)="[^"]*(?:article-body|story-body|entry-content|post-content)[^"]*"[^>]*>(.*?)</div>\s*</div>',
    ):
        m = re.search(pattern, html, re.S | re.I)
        if m and len(_clean(m.group(1)).split()) >= MIN_SOURCE_WORDS:
            return _clean(m.group(1))[:max_chars]

    paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.S)
    body = " ".join(p for p in paras if len(_clean(p).split()) > 8)
    return _clean(body)[:max_chars]
