"""Find a relevant free-licence image for each beat of the narration.

The script declares a shot list -- a search query plus the narration phrase
it should appear on -- and this module resolves each one to an actual image.

Sources are tried in order. The first two need no API key at all:

  1. Openverse        CC-licensed images, no key, good general coverage
  2. Wikimedia Commons no key, excellent for species, anatomy, instruments
  3. Pexels           free key, best-looking stock photography
  4. Pixabay          free key, broad fallback

Every image carries a licence, and most CC licences require credit. The
attribution for each image used is recorded and appended to the Instagram
caption automatically -- see `credits_block()`.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import config

UA = "scidaily-reels/1.0 (educational science summaries)"
TIMEOUT = 30

# Licences we will actually use. Anything requiring share-alike on the whole
# derived work, or forbidding commercial use, is skipped -- a monetisable
# Instagram account should not inherit those terms.
OK_LICENCES = {"cc0", "pdm", "by", "by-sa"}


@dataclass
class Shot:
    query: str                  # what to search for
    cue: str                    # narration phrase this shot lands on
    start: float = 0.0          # filled in once word timings exist
    end: float = 0.0
    path: Path | None = None
    credit: dict = field(default_factory=dict)
    is_video: bool = False      # stock footage, not a still -- no zoompan

    @property
    def duration(self) -> float:
        return max(0.8, self.end - self.start)


def _get_json(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


MIN_PIXELS = 500          # anything smaller looks soft once zoomed


def _download(url: str, dest: Path) -> bool:
    """Download and normalise to a real JPEG that ffmpeg can definitely open.

    The image sources happily serve SVG, WebP, GIF and occasionally an HTML
    error page. Saving those under a .jpg name made ffmpeg fail at render
    time with a confusing "Option loop not found", long after the download
    looked successful. So every file is decoded and re-encoded here, and
    anything that won't decode is rejected while there is still time to try
    the next candidate.
    """
    tmp = dest.with_suffix(".tmp")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)

        if tmp.stat().st_size < 8000:          # tracking pixel or stub
            tmp.unlink(missing_ok=True)
            return False

        from PIL import Image, ImageOps
        with Image.open(tmp) as im:
            im.load()                          # forces a real decode
            if min(im.size) < MIN_PIXELS:
                tmp.unlink(missing_ok=True)
                return False
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", im.size, (12, 16, 22))
                im = im.convert("RGBA")
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
            im.save(dest, "JPEG", quality=90)

        tmp.unlink(missing_ok=True)
        return True

    except Exception:
        tmp.unlink(missing_ok=True)
        dest.unlink(missing_ok=True)
        return False


# ------------------------------------------------------------------ sources

def _src_openverse(query: str) -> list[dict]:
    url = ("https://api.openverse.org/v1/images/?"
           + urllib.parse.urlencode({
               "q": query, "page_size": 8, "license_type": "all-cc",
               "aspect_ratio": "tall,square", "size": "medium,large"}))
    try:
        data = _get_json(url)
    except Exception:
        return []

    out = []
    for r in data.get("results", []):
        lic = (r.get("license") or "").lower()
        if lic not in OK_LICENCES:
            continue
        if not r.get("url"):
            continue
        out.append({
            "url": r["url"],
            "credit": {
                "title": r.get("title") or query,
                "creator": r.get("creator") or "unknown",
                "licence": f"CC {lic.upper()}" if lic not in ("cc0", "pdm") else lic.upper(),
                "source": r.get("foreign_landing_url") or r.get("url"),
                "via": "Openverse",
            },
        })
    return out


def _src_wikimedia(query: str) -> list[dict]:
    url = ("https://commons.wikimedia.org/w/api.php?"
           + urllib.parse.urlencode({
               "action": "query", "format": "json", "generator": "search",
               "gsrsearch": f"filetype:bitmap {query}", "gsrlimit": 8,
               "gsrnamespace": 6, "prop": "imageinfo",
               "iiprop": "url|extmetadata", "iiurlwidth": 1400}))
    try:
        data = _get_json(url)
    except Exception:
        return []

    out = []
    for page in (data.get("query", {}).get("pages") or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        src = info.get("thumburl") or info.get("url")
        if not src:
            continue
        meta = info.get("extmetadata", {})
        out.append({
            "url": src,
            "credit": {
                "title": page.get("title", "").replace("File:", ""),
                "creator": re.sub(r"<[^>]+>", "",
                                  meta.get("Artist", {}).get("value", "unknown")).strip(),
                "licence": meta.get("LicenseShortName", {}).get("value", "see source"),
                "source": info.get("descriptionurl", src),
                "via": "Wikimedia Commons",
            },
        })
    return out


def _src_pexels(query: str) -> list[dict]:
    if not config.PEXELS_API_KEY:
        return []
    url = ("https://api.pexels.com/v1/search?"
           + urllib.parse.urlencode({
               "query": query, "per_page": 8, "orientation": "portrait"}))
    try:
        data = _get_json(url, {"Authorization": config.PEXELS_API_KEY})
    except Exception:
        return []

    return [{
        "url": p["src"]["large2x"],
        "credit": {
            "title": p.get("alt") or query,
            "creator": p.get("photographer", "unknown"),
            "licence": "Pexels Licence",
            "source": p.get("url", ""),
            "via": "Pexels",
        },
    } for p in data.get("photos", [])]


def _src_pixabay(query: str) -> list[dict]:
    if not config.PIXABAY_API_KEY:
        return []
    url = ("https://pixabay.com/api/?"
           + urllib.parse.urlencode({
               "key": config.PIXABAY_API_KEY, "q": query, "per_page": 8,
               "image_type": "photo", "orientation": "vertical",
               "safesearch": "true"}))
    try:
        data = _get_json(url)
    except Exception:
        return []

    return [{
        "url": h["largeImageURL"],
        "credit": {
            "title": h.get("tags", query),
            "creator": h.get("user", "unknown"),
            "licence": "Pixabay Licence",
            "source": h.get("pageURL", ""),
            "via": "Pixabay",
        },
    } for h in data.get("hits", [])]


def _download_video(url: str, dest: Path) -> bool:
    """Save a stock clip, and prove ffmpeg can actually read it.

    _download() runs everything through PIL and re-encodes to JPEG,
    which an mp4 cannot survive, so footage needs its own path. ffprobe
    is the check: a clip that will not probe will not render either, and
    learning that here leaves time to try the next candidate instead of
    failing mid-render.
    """
    import subprocess
    tmp = dest.with_suffix(".tmpvid")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as resp, \
                open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        if tmp.stat().st_size < 50_000:        # stub or error page
            tmp.unlink(missing_ok=True)
            return False
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=height", "-of", "csv=p=0", str(tmp)],
            capture_output=True, text=True, timeout=60)
        if probe.returncode != 0 or not probe.stdout.strip():
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(dest)
        return True
    except Exception:
        tmp.unlink(missing_ok=True)
        dest.unlink(missing_ok=True)
        return False


def _src_pexels_video(query: str) -> list[dict]:
    """Pexels footage. Same key as the photo search, /videos/ endpoint."""
    if not config.PEXELS_API_KEY:
        return []
    url = ("https://api.pexels.com/videos/search?"
           + urllib.parse.urlencode({
               "query": query, "per_page": 8,
               "orientation": "portrait", "size": "medium"}))
    try:
        data = _get_json(url, {"Authorization": config.PEXELS_API_KEY})
    except Exception:
        return []

    out = []
    for v in data.get("videos", []):
        files = [f for f in v.get("video_files", [])
                 if f.get("file_type") == "video/mp4"
                 and (f.get("height") or 0) >= config.STOCK_VIDEO_MIN_PX]
        if not files:
            continue
        # Smallest rendition that still clears the floor: a 4K download
        # costs minutes and is cropped to 1080 high anyway.
        files.sort(key=lambda f: f.get("height") or 0)
        slug = (v.get("url", "").rstrip("/").split("/")[-1] or query)
        out.append({
            "url": files[0]["link"],
            "credit": {
                "title": slug.replace("-", " ")[:60] or query,
                "creator": (v.get("user") or {}).get("name", "unknown"),
                "licence": "Pexels Licence",
                "source": v.get("url", ""),
                "via": "Pexels",
            },
        })
    return out


def _src_pixabay_video(query: str) -> list[dict]:
    if not config.PIXABAY_API_KEY:
        return []
    url = ("https://pixabay.com/api/videos/?"
           + urllib.parse.urlencode({
               "key": config.PIXABAY_API_KEY, "q": query,
               "per_page": 8, "safesearch": "true"}))
    try:
        data = _get_json(url)
    except Exception:
        return []

    out = []
    for h in data.get("hits", []):
        vids = h.get("videos") or {}
        pick = None
        for name in ("medium", "small", "large", "tiny"):
            v = vids.get(name) or {}
            if v.get("url") and (v.get("height") or 0) >= config.STOCK_VIDEO_MIN_PX:
                pick = v
                break
        if not pick:
            continue
        out.append({
            "url": pick["url"],
            "credit": {
                "title": h.get("tags", query),
                "creator": h.get("user", "unknown"),
                "licence": "Pixabay Licence",
                "source": h.get("pageURL", ""),
                "via": "Pixabay",
            },
        })
    return out


VIDEO_SOURCES = [
    ("pexels-v", _src_pexels_video),
    ("pixabay-v", _src_pixabay_video),
]

_VIDEO_CACHE: dict[str, list[dict]] = {}


def video_candidates_for(query: str) -> list[dict]:
    """Stock clips for one query, cached like the image candidates.

    Narrower than candidates_for(): no word-dropping variants, because a
    one-word query against a video library returns generic filler, and
    generic filler is worse than the specific photo it would replace.
    """
    if query in _VIDEO_CACHE:
        return _VIDEO_CACHE[query]
    found, seen = [], set()
    for name, fn in VIDEO_SOURCES:
        try:
            cands = fn(query)
        except Exception:
            continue
        for cand in cands:
            if cand["url"] in seen:
                continue
            seen.add(cand["url"])
            cand["via_source"] = name
            found.append(cand)
    _VIDEO_CACHE[query] = found
    return found


SOURCES = [
    ("pexels", _src_pexels),         # best looking, used first when keyed
    ("openverse", _src_openverse),
    ("wikimedia", _src_wikimedia),
    ("pixabay", _src_pixabay),
]


# ----------------------------------------------------------------- resolve

_CANDIDATE_CACHE: dict[str, list[dict]] = {}


def candidates_for(query: str) -> list[dict]:
    """All candidates across all sources for one query, cached.

    Cached because a beat split into sub-shots asks for the same query
    several times and should get a *different* image each time, without
    re-hitting the APIs.
    """
    if query in _CANDIDATE_CACHE:
        return _CANDIDATE_CACHE[query]

    variants = [query]
    words = query.split()
    if len(words) > 2:
        variants.append(" ".join(words[:2]))
    if len(words) > 1:
        variants.append(words[0])

    found: list[dict] = []
    seen_urls: set[str] = set()
    for variant in variants:
        for name, fn in SOURCES:
            for cand in fn(variant):
                if cand["url"] in seen_urls:
                    continue
                seen_urls.add(cand["url"])
                cand["via_source"] = name
                found.append(cand)
        # Keep collecting: a beat split into 5 sub-shots needs 5 distinct
        # images, and stopping early is what produced six identical
        # "nature landscape" shots in a row.
        if len(found) >= 14:
            break

    _CANDIDATE_CACHE[query] = found
    return found


def resolve_shot(shot: Shot, workdir: Path, index: int,
                 used: set[str]) -> bool:
    """Download footage, or failing that an image, for one shot."""
    # Footage first. Anything the video libraries cannot cover falls
    # straight through to the photo pipeline below, so a thin result is
    # today's behaviour rather than a failure.
    if config.STOCK_VIDEO:
        for cand in video_candidates_for(shot.query):
            if cand["url"] in used:
                continue
            dest = workdir / f"shot{index:02d}.mp4"
            if _download_video(cand["url"], dest):
                used.add(cand["url"])
                shot.path = dest
                shot.credit = cand["credit"]
                shot.is_video = True
                print(f"    shot {index:2d}  {cand.get('via_source','video'):10s} "
                      f"[clip] {shot.query[:28]}")
                return True

    for cand in candidates_for(shot.query):
        if cand["url"] in used:
            continue
        dest = workdir / f"shot{index:02d}.jpg"
        if _download(cand["url"], dest):
            used.add(cand["url"])
            shot.path = dest
            shot.credit = cand["credit"]
            print(f"    shot {index:2d}  {cand.get('via_source','?'):10s} "
                  f"{shot.query[:34]}")
            return True

    # Pool exhausted for this query. Rather than repeating the same picture
    # back to back, widen the search with a generic companion term before
    # falling back to reuse.
    for extra in (f"{shot.query} closeup", f"{shot.query} macro",
                  shot.query.split()[-1] if shot.query.split() else shot.query):
        for cand in candidates_for(extra):
            if cand["url"] in used:
                continue
            dest = workdir / f"shot{index:02d}.jpg"
            if _download(cand["url"], dest):
                used.add(cand["url"])
                shot.path = dest
                shot.credit = cand["credit"]
                print(f"    shot {index:2d}  widened    {extra[:34]}")
                return True

    for cand in candidates_for(shot.query):
        dest = workdir / f"shot{index:02d}.jpg"
        if _download(cand["url"], dest):
            shot.path = dest
            shot.credit = cand["credit"]
            print(f"    shot {index:2d}  (reused)   {shot.query[:34]}")
            return True

    print(f"    shot {index:2d}  NOT FOUND  {shot.query[:34]}")
    return False


def split_long_shots(shots: list[Shot]) -> list[Shot]:
    """Break any beat longer than MAX_SHOT_SECONDS into equal sub-shots.

    Nine images across 85 seconds is a slideshow. This is what gets the
    cut rate up to roughly one image every 3-4 seconds without asking the
    model for an unrealistic number of distinct search queries.
    """
    out: list[Shot] = []
    for shot in shots:
        # ceil, not round: a 4.2s beat against a 4.0s cap must split, and
        # round() would leave it as one over-long shot.
        n = max(1, math.ceil(shot.duration / config.MAX_SHOT_SECONDS - 0.01))
        while n > 1 and shot.duration / n < config.MIN_SHOT_SECONDS:
            n -= 1
        if n == 1:
            out.append(shot)
            continue

        step = shot.duration / n
        for k in range(n):
            sub = Shot(query=shot.query, cue=shot.cue)
            sub.start = shot.start + k * step
            sub.end = sub.start + step
            out.append(sub)
    return out


def align_shots(shots: list[Shot], words, total: float) -> list[Shot]:
    """Give each shot a start/end by matching its cue to the word timings.

    Falls back to even spacing for any cue that cannot be located, so a bad
    cue degrades the pacing rather than breaking the render.
    """
    text = [re.sub(r"[^a-z0-9]", "", w.text.lower()) for w in words]

    for shot in shots:
        cue = [re.sub(r"[^a-z0-9]", "", t.lower())
               for t in shot.cue.split() if t.strip()]
        cue = [c for c in cue if c]
        shot.start = -1.0
        if not cue:
            continue
        for i in range(len(text) - len(cue) + 1):
            if text[i:i + len(cue)] == cue:
                shot.start = words[i].start
                break

    # Even spacing for unmatched cues.
    n = len(shots)
    for i, shot in enumerate(shots):
        if shot.start < 0:
            shot.start = total * i / n

    shots.sort(key=lambda s: s.start)
    shots[0].start = 0.0
    for i in range(n - 1):
        shots[i].end = max(shots[i].start + 1.2, shots[i + 1].start)
        shots[i + 1].start = shots[i].end
    shots[-1].end = max(shots[-1].start + 1.2, total)
    return shots


def credits_block(shots: list[Shot]) -> str:
    """Attribution text to append to the Instagram caption."""
    seen, lines = set(), []
    for s in shots:
        c = s.credit
        if not c:
            continue
        key = (c.get("creator"), c.get("title"))
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {c.get('title','untitled')[:48]} by "
                     f"{c.get('creator','unknown')[:30]} ({c.get('licence','')}) "
                     f"via {c.get('via','')}")
    if not lines:
        return ""
    return "Image credits:\n" + "\n".join(lines)
