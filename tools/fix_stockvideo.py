#!/usr/bin/env python3
"""Real footage instead of still photos, from the stock libraries.

The complaint was "animated shorts, not just moving images", and a day
of local video generation produced one static 2.3-second clip, a RAM
stall, an eight-hour thrash and a WSL crash. MoneyPrinterTurbo solves
the same problem with no GPU at all: it pulls stock VIDEO clips. That is
real motion, free, instant, and commercially licensed.

The gap turned out to be small. visuals.py already talks to Pexels and
Pixabay -- it just asks them for photos. Same APIs, same keys, same
credit handling; a different endpoint.

What changes:

  * Two video sources, tried BEFORE the photo sources. Anything they
    cannot cover still falls through to the existing image pipeline, so
    a thin result degrades to today's behaviour rather than failing.
  * _download_video(), because _download() decodes with PIL and
    re-encodes to JPEG and an mp4 will never survive that. ffprobe is
    the validity check: a clip that will not probe will not render.
  * render.py branches. Stock footage gets NO zoompan -- the footage
    already moves, and a Ken Burns push on top of real camera motion
    reads as a mistake. -stream_loop covers a clip shorter than its
    shot, and -an drops the stock audio, which would otherwise collide
    with the narration.

Licensing is better than what it replaces: the Pexels and Pixabay
licences permit commercial use with no attribution required, where the
current CC BY-SA images carry a share-alike obligation. Credits are
still recorded.

STOCK_VIDEO=0 turns the whole thing off and restores photos.

NEEDS A PEXELS KEY to do anything -- both video sources are no-ops
without one, and the build will quietly keep using photos:

    https://www.pexels.com/api/   (free, instant)
    gh secret set PEXELS_API_KEY -R sujee119/news-reels

    python3 tools/fix_stockvideo.py
"""
import ast
import pathlib
import sys

CONFIG_EDITS = [(
    'PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")\n',
    '''# Stock FOOTAGE rather than stills. The renderer fakes motion on a
# photo with zoompan; a real clip just moves. Needs PEXELS_API_KEY --
# without it the video sources return nothing and the build falls back
# to photos exactly as before.
STOCK_VIDEO = (os.getenv("STOCK_VIDEO") or "1") not in ("0", "false", "False")
# Reject clips shorter than this edge, in pixels. Stock libraries serve
# several renditions per clip and the small ones look soft full-frame.
STOCK_VIDEO_MIN_PX = int(os.getenv("STOCK_VIDEO_MIN_PX") or 540)

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
''',
    "STOCK_VIDEO", 1)]

VISUALS_EDITS = [
    # 1. a shot needs to know which kind of file it holds
    ('''    path: Path | None = None
    credit: dict = field(default_factory=dict)
''',
     '''    path: Path | None = None
    credit: dict = field(default_factory=dict)
    is_video: bool = False      # stock footage, not a still -- no zoompan
''',
     "is_video: bool", 1),

    # 2. video download + the two video sources, before SOURCES
    ('''SOURCES = [
''',
     '''def _download_video(url: str, dest: Path) -> bool:
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
        with urllib.request.urlopen(req, timeout=120) as resp, \\
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
''',
     "def _src_pexels_video", 1),

    # 3. try footage before stills
    ('''    """Download an image for one shot, avoiding any already used."""
    for cand in candidates_for(shot.query):
''',
     '''    """Download footage, or failing that an image, for one shot."""
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
''',
     # NOT "config.STOCK_VIDEO" -- that is a substring of
     # STOCK_VIDEO_MIN_PX, which the edit above inserts, so this edit
     # would look already-applied and be silently skipped, leaving the
     # whole feature dead. Caught in testing; marker must be unique.
     "Footage first", 1),
]

RENDER_EDITS = [(
    '''def render_shot(shot: Shot, index: int, out_path: Path) -> Path:
    """One still image -> one moving clip of the right length."""
    dur = shot.duration + config.SHOT_TRANSITION
''',
    '''def _render_clip(shot: Shot, dur: float, index: int,
                 out_path: Path) -> Path:
    """Stock footage -> one clip of the right length.

    Deliberately no zoompan. The footage already moves, and a Ken Burns
    push laid over real camera motion reads as a mistake rather than as
    more motion. -stream_loop covers a clip shorter than its shot, and
    -an drops the stock audio, which would otherwise be mixed under the
    narration.
    """
    W, H = config.WIDTH, config.HEIGHT
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
          f"crop={W}:{H},"
          f"eq=brightness=-{config.SHOT_DIM}:saturation=1.08,"
          f"format=yuv420p")
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-stream_loop", "-1", "-i", str(shot.path),
        "-t", f"{dur:.3f}", "-vf", vf, "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(config.FPS),
        str(out_path),
    ], f"shot {index} clip")
    return out_path


def render_shot(shot: Shot, index: int, out_path: Path) -> Path:
    """One still image -> one moving clip of the right length."""
    dur = shot.duration + config.SHOT_TRANSITION
    if getattr(shot, "is_video", False):
        return _render_clip(shot, dur, index, out_path)
''',
    "_render_clip", 1)]


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


def main() -> None:
    root = pathlib.Path(".")
    if not (root / "core/visuals.py").exists():
        sys.exit("run this from a channel repo (needs core/visuals.py)")
    print("stock footage instead of stills:")
    print(apply(root / "config.py", CONFIG_EDITS))
    print(apply(root / "core/visuals.py", VISUALS_EDITS))
    print(apply(root / "core/render.py", RENDER_EDITS))
    print("""
  Needs a Pexels key or nothing changes -- the video sources return
  nothing without one and the build silently keeps using photos:

      https://www.pexels.com/api/            free, instant
      gh secret set PEXELS_API_KEY -R sujee119/<repo>

  Also pass it to the build step if it is not there already, and watch
  the next build log for "[clip]" lines -- that is footage being used.

      STOCK_VIDEO=0   turns it off and restores photos
""")


if __name__ == "__main__":
    main()
