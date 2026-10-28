#!/usr/bin/env python3
"""Add Coverr as a video source, and put it first.

Pexels paused new API keys, so Pixabay was carrying the whole video
pipeline alone -- and its video library is small enough that specific
queries get whatever is popular instead of nothing. That is how a reel
about an AI screwdriver ended up showing social-media scrolling and a
horse's eye.

Coverr fixes the two things that were wrong:

  * A SEPARATE, CURATED LIBRARY, so the pool for a given query is no
    longer one small index.
  * MUCH BETTER METADATA. Pixabay gives a tags string. Coverr gives
    title, description AND a tags array, so the relevance gate from
    fix_clipmatch has three fields to match on instead of one. Coverr
    goes FIRST in the chain for that reason -- a source whose relevance
    can be judged properly is worth asking before one whose cannot.

Checked against the docs rather than assumed:

  * GET https://api.coverr.co/videos?query=…&urls=true
  * Auth: "Authorization: Bearer <key>"  (or ?api_key=)
  * hits[].urls.mp4, .title, .description, .tags[], .duration,
    .is_vertical, .max_width/.max_height
  * ATTRIBUTION IS NOT REQUIRED. A third-party blog claimed Coverr
    demands its logo on screen; the licence page says credit "is not
    required… appreciated when possible". Commercial use is permitted,
    worldwide and irrevocable. Credits are recorded anyway, as for
    every other source.

ONE REAL CONSTRAINT: a demo application gets 50 REQUESTS PER HOUR.
Production is 2,000/hour but needs a paid Pro or Ultimate plan, which
is against the everything-free rule. One reel asks roughly one query
per shot, so ~11 a reel; two reels built back to back will approach the
limit. A 429 is caught like any other error and the build falls through
to Pixabay and then photos, so the ceiling costs variety, never a
build. Vertical clips are preferred where offered, since a 9:16 crop
out of portrait footage loses less.

Needs fix_stockvideo.py and fix_clipmatch.py applied first.

    gh secret set COVERR_API_KEY -R sujee119/<repo>
    python3 tools/fix_coverr.py
"""
import ast
import pathlib
import sys

CFG_EDITS = [(
    'PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")\n',
    '''PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
# Coverr: a curated video library with title, description and tags, so
# the relevance gate can actually judge a clip. Asked before Pixabay for
# that reason. A demo app allows 50 requests/hour; exceeding it returns
# 429, which degrades to Pixabay and then to photos.
COVERR_API_KEY = os.getenv("COVERR_API_KEY", "")
''',
    "COVERR_API_KEY", 1)]

VISUALS_EDITS = [
    ('''VIDEO_SOURCES = [
    ("pexels-v", _src_pexels_video),
    ("pixabay-v", _src_pixabay_video),
]
''',
     '''def _src_coverr_video(query: str) -> list[dict]:
    """Coverr footage. Richest metadata of the three, so asked first.

    title + description + tags all feed the relevance gate, which is
    the difference between knowing a clip matches and hoping it does.
    """
    if not config.COVERR_API_KEY:
        return []
    url = ("https://api.coverr.co/videos?"
           + urllib.parse.urlencode({
               "query": query, "page_size": 12,
               "sort": "popular", "urls": "true"}))
    try:
        data = _get_json(
            url, {"Authorization": f"Bearer {config.COVERR_API_KEY}"})
    except Exception:
        # Includes 429 once the demo app's 50/hour is spent. Falling
        # through to the next source is the right answer; a thinner pool
        # beats a failed build.
        return []

    out = []
    for h in data.get("hits", []):
        link = (h.get("urls") or {}).get("mp4")
        if not link:
            continue
        if (h.get("max_height") or 0) < config.STOCK_VIDEO_MIN_PX:
            continue
        shared = _clip_matches(query, h.get("title", ""),
                               h.get("description", ""),
                               " ".join(h.get("tags") or []))
        if not _clip_ok(query, shared):
            continue
        out.append({
            "match": sorted(shared),
            # Portrait footage crops to 9:16 with far less loss, so
            # offer it ahead of landscape rather than discarding either.
            "_vert": bool(h.get("is_vertical")),
            "url": link,
            "credit": {
                "title": (h.get("title") or query)[:60],
                "creator": "Coverr",
                "licence": "Coverr Licence",
                "source": f"https://coverr.co/videos/{h.get('id', '')}",
                "via": "Coverr",
            },
        })
    out.sort(key=lambda c: not c.get("_vert"))
    return out


VIDEO_SOURCES = [
    ("coverr-v", _src_coverr_video),      # best metadata, judged first
    ("pexels-v", _src_pexels_video),
    ("pixabay-v", _src_pixabay_video),
]
''',
     "def _src_coverr_video", 1),
]


def apply(path: pathlib.Path, edits: list) -> str:
    if not path.exists():
        return f"  {path}: not here, skipped"
    src = original = path.read_text()
    applied = 0
    for old, new, marker, count in edits:
        if marker in src:
            continue
        n = src.count(old)
        if n != count:
            return (f"  {path}: expected {count} occurrence(s), found {n} "
                    f"-- NOT TOUCHED")
        src = src.replace(old, new)
        applied += 1
    if applied == 0:
        return f"  {path}: already patched"
    try:
        ast.parse(src)
    except SyntaxError as exc:
        return f"  {path}: would not parse ({exc}) -- NOT WRITTEN"
    replaced = {l.strip() for old, _, _, _ in edits for l in old.splitlines()}
    lost = {l.strip() for l in set(original.splitlines())
            - set(src.splitlines()) if l.strip()} - replaced
    if lost:
        return f"  {path}: would drop {len(lost)} unrelated line(s) -- NOT WRITTEN"
    path.write_text(src)
    return f"  {path}: patched {applied} place(s)"


def add_yml(path: pathlib.Path) -> str:
    if not path.exists():
        return f"  {path}: not here, skipped"
    src = path.read_text()
    if "COVERR_API_KEY" in src:
        return f"  {path}: already patched"
    anchor = "          PIXABAY_API_KEY: ${{ secrets.PIXABAY_API_KEY }}\n"
    if src.count(anchor) != 1:
        return f"  {path}: anchor appears {src.count(anchor)}x -- NOT TOUCHED"
    new = src.replace(anchor, anchor +
                      "          COVERR_API_KEY: ${{ secrets.COVERR_API_KEY }}\n")
    try:
        import yaml
        doc = yaml.safe_load(new)
        envs = [s.get("env") or {}
                for j in doc["jobs"].values() for s in j["steps"]]
        if not any("COVERR_API_KEY" in e for e in envs):
            return f"  {path}: did not land in a step env -- NOT WRITTEN"
    except ImportError:
        pass
    except Exception as exc:
        return f"  {path}: YAML would not parse ({exc}) -- NOT WRITTEN"
    path.write_text(new)
    return f"  {path}: COVERR_API_KEY passed through"


def main() -> None:
    root = pathlib.Path(".")
    vis = root / "core/visuals.py"
    if not vis.exists():
        sys.exit("run from a channel repo")
    text = vis.read_text()
    if "_src_pixabay_video" not in text:
        sys.exit("run tools/fix_stockvideo.py first")
    if "_clip_matches" not in text:
        sys.exit("run tools/fix_clipmatch.py first -- Coverr relies on the "
                 "relevance gate, and without it this would add a third way "
                 "to fetch an irrelevant clip")
    print("coverr, asked before the others:")
    print(apply(root / "config.py", CFG_EDITS))
    print(apply(vis, VISUALS_EDITS))
    print(add_yml(root / ".github/workflows/build.yml"))
    print("""
  Set the key, then watch the next build log:

      gh secret set COVERR_API_KEY -R sujee119/news-reels

      shot  0  coverr-v   [clip] electric screwdriver  <-screwdriver

  A demo app allows 50 requests/hour. Past that Coverr returns 429, the
  source returns nothing, and the build uses Pixabay and then photos --
  so the ceiling shows up as less footage, never as a failure.
""")


if __name__ == "__main__":
    main()
