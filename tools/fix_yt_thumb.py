#!/usr/bin/env python3
"""Put the catchphrase frame on the YouTube grid for the reels channels.

Hotsciencenews shows videos with no catchphrase on them because the
reels repos never set a YouTube thumbnail at all. Without one, YouTube
picks whatever frame it likes, and that frame is almost never inside the
3.2s cover hold where the text lives.

The feature exists -- it was written for the shorts channel
(core/youtube.py set_thumbnail, run.py _cover_frame) and simply never
got ported here. This ports it verbatim, plus the one thing the shorts
repo does not need: the YouTube workflow step never received the COVER_*
variables, so a custom COVER_THUMB_MS would not have reached it.

A thumbnail needs a PHONE-VERIFIED channel. If Hotsciencenews is not
verified, YouTube answers 403 and the manifest records

    thumbnail_note: 403 -- the channel is not phone-verified, ...

which is the answer to "why is there still no thumbnail", rather than
silence. The video is already live when this runs, so nothing here can
fail a build.

    python3 tools/fix_yt_thumb.py
"""
import ast
import pathlib
import sys

YT_EDITS = [(
    '''def watch_url(video_id: str) -> str:
    return f"https://youtube.com/shorts/{video_id}"
''',
    '''def watch_url(video_id: str) -> str:
    return f"https://youtube.com/shorts/{video_id}"


THUMB_URL = ("https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
             "?videoId={vid}&uploadType=media")


def set_thumbnail(video_id: str, image: Path) -> str:
    """Put the cover frame on the channel grid.

    Returns "" on success or a short reason on failure. Never raises: the
    video is already live by the time this runs, and a missing custom
    thumbnail is cosmetic -- YouTube falls back to a frame it picks
    itself, which is exactly the frame with no catchphrase on it.

    Needs a phone-verified channel. Without verification this is a 403,
    worth saying plainly rather than logging as a mystery.
    """
    if not image.exists():
        return "no thumbnail file"
    size = image.stat().st_size
    if size > 2 * 1024 * 1024:
        return f"thumbnail too large ({size // 1024}KB, limit 2MB)"

    try:
        token = access_token()
    except Exception as exc:
        # Not just YouTubeError: access_token converts HTTP errors but
        # lets a URLError through, and that escaping here would kill the
        # run after the video is already live.
        return f"token: {type(exc).__name__} {str(exc)[:70]}"

    req = urllib.request.Request(
        THUMB_URL.format(vid=video_id), data=image.read_bytes(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "image/jpeg"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
        return ""
    except urllib.error.HTTPError as exc:
        body = exc.read()[:200].decode("utf-8", "ignore")
        if exc.code == 403:
            return ("403 -- the channel is not phone-verified, so custom "
                    "thumbnails are not allowed yet")
        return f"{exc.code}: {body}"
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc)[:80]}"
''',
    "def set_thumbnail(")]

RUN_EDITS = [(
    """def cmd_youtube(args) -> int:
""",
    '''def _cover_frame(video: Path) -> Path | None:
    """Pull one frame from inside the cover hold, so the catchphrase is on
    it. Returns None if ffmpeg cannot, which is not worth failing over."""
    import subprocess

    at = max(0.05, config.COVER_THUMB_MS / 1000.0)
    if at >= config.COVER_SECONDS:
        # Past the hold the overlay has faded and the frame would be bare.
        at = max(0.05, config.COVER_SECONDS * 0.4)
    out = video.with_name("thumbnail.jpg")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}",
             "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out)],
            check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out if out.exists() and out.stat().st_size > 2000 else None


def cmd_youtube(args) -> int:
''',
    "def _cover_frame("),
(
    """        man["youtube_id"] = vid
        man.pop("youtube_error", None)
        _save(man["slug"], man)
        print(f"    -> {youtube.watch_url(vid)}")
        done += 1
""",
    '''        man["youtube_id"] = vid
        man.pop("youtube_error", None)
        print(f"    -> {youtube.watch_url(vid)}")

        # The cover frame as the grid thumbnail. Without this YouTube
        # picks its own frame, which is why Hotsciencenews showed videos
        # with no catchphrase on them. Cosmetic, and it runs after the
        # video is live, so a failure is recorded and nothing more.
        thumb = _cover_frame(reel)
        if thumb:
            why = youtube.set_thumbnail(vid, thumb)
            if why:
                man["thumbnail_note"] = why[:160]
                print(f"    thumbnail not set: {why[:110]}")
            else:
                man["thumbnail"] = "set"
                print(f"    thumbnail: the cover frame at "
                      f"{config.COVER_THUMB_MS}ms")
        _save(man["slug"], man)
        done += 1
''',
    "thumb = _cover_frame(reel)"),
]

BUILD_EDITS = [(
    """          YT_CATEGORY_ID: ${{ vars.YT_CATEGORY_ID || '28' }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python -u run.py youtube
""",
    """          YT_CATEGORY_ID: ${{ vars.YT_CATEGORY_ID || '28' }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          # The thumbnail frame is cut from inside the cover hold, so this
          # step needs the same cover settings the render used. It never
          # received them, so a custom COVER_THUMB_MS was ignored here.
          COVER_ENABLED: ${{ vars.COVER_ENABLED || '1' }}
          COVER_SECONDS: ${{ vars.COVER_SECONDS || '3.2' }}
          COVER_THUMB_MS: ${{ vars.COVER_THUMB_MS || '900' }}
        run: python -u run.py youtube
""",
    "step needs the same cover settings")]


def apply(path: pathlib.Path, edits: list, python: bool) -> str:
    if not path.exists():
        return f"  {path}: not here, skipped"
    src = original = path.read_text()
    applied = 0

    for old, new, marker in edits:
        if marker in src:
            continue
        n = src.count(old)
        if n == 0:
            return f"  {path}: expected text missing -- NOT TOUCHED"
        if n > 1:
            return f"  {path}: target appears {n}x -- NOT TOUCHED"
        src = src.replace(old, new)
        applied += 1

    if applied == 0:
        return f"  {path}: already patched"

    if python:
        try:
            ast.parse(src)
        except SyntaxError as exc:
            return f"  {path}: would not parse ({exc}) -- NOT WRITTEN"
    else:
        try:
            import yaml
            doc = yaml.safe_load(src)
            for job, body in ((doc or {}).get("jobs") or {}).items():
                for i, step in enumerate((body or {}).get("steps") or [], 1):
                    if not isinstance(step, dict) or not (
                            "run" in step or "uses" in step):
                        return (f"  {path}: {job} step {i} would have neither "
                                f"run nor uses -- NOT WRITTEN")
        except ImportError:
            pass
        except Exception as exc:
            return f"  {path}: bad YAML ({exc}) -- NOT WRITTEN"

    replaced = {l for old, _, _ in edits for l in old.splitlines()}
    lost = {l for l in set(original.splitlines()) - set(src.splitlines())
            if l.strip()} - replaced
    if lost:
        return f"  {path}: would drop {len(lost)} unrelated line(s) -- NOT WRITTEN"

    path.write_text(src)
    return f"  {path}: patched {applied} place(s)"


def main() -> None:
    root = pathlib.Path(".")
    if not (root / "core/publish.py").exists():
        sys.exit("this is for the two reels repos (scidaily-reels, "
                 "k-newsreels) -- the shorts repo already has it")
    print("porting the YouTube thumbnail to this channel:")
    print(apply(root / "core/youtube.py", YT_EDITS, True))
    print(apply(root / "run.py", RUN_EDITS, True))
    print(apply(root / ".github/workflows/build.yml", BUILD_EDITS, False))


if __name__ == "__main__":
    main()
