"""Upload a finished reel to YouTube as a Short.

Deliberately separate from the Instagram path. A YouTube failure must never
cost an Instagram post, so this runs after publishing and its errors are
reported rather than raised into the build.

Auth is a refresh token obtained once per channel by tools/youtube_auth.py.
Refresh tokens from an app still in "Testing" expire after seven days, so
the consent screen has to be published to production -- see that script.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import config

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = ("https://www.googleapis.com/upload/youtube/v3/videos"
              "?uploadType=resumable&part=snippet,status")
SCOPE = "https://www.googleapis.com/auth/youtube.upload"

# YouTube's own limits.
TITLE_MAX = 100
DESC_MAX = 5000


class YouTubeError(RuntimeError):
    """Anything that stopped the upload, with the API's own words."""


def enabled() -> bool:
    return bool(config.YT_ENABLED and config.YT_CLIENT_ID
                and config.YT_CLIENT_SECRET and config.YT_REFRESH_TOKEN)


def _post_form(url: str, fields: dict, timeout: int = 60) -> dict:
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise YouTubeError(f"{url}: {exc.read().decode()[:400]}") from exc


def access_token() -> str:
    """Trade the long-lived refresh token for a short-lived access token."""
    data = _post_form(TOKEN_URL, {
        "client_id": config.YT_CLIENT_ID,
        "client_secret": config.YT_CLIENT_SECRET,
        "refresh_token": config.YT_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    })
    tok = data.get("access_token")
    if not tok:
        raise YouTubeError(f"no access_token in response: {str(data)[:200]}")
    return tok


def build_title(title: str, cover: str = "") -> str:
    """A Short's title. The catchphrase leads when there is one -- it is
    already the sharpest phrasing of the story we have."""
    base = " ".join((cover or "").split()).strip()
    rest = " ".join((title or "").split()).strip()
    if base and rest:
        text = f"{base} | {rest}"
    else:
        text = base or rest or "News"
    tag = " #Shorts"
    if len(text) + len(tag) > TITLE_MAX:
        text = text[:TITLE_MAX - len(tag) - 1].rstrip(" |-,")
    return f"{text}{tag}"


def build_description(caption: str, link: str = "") -> str:
    parts = [(caption or "").strip()]
    if link and link not in (caption or ""):
        parts.append(f"Source: {link}")
    parts.append("#Shorts")
    text = "\n\n".join(p for p in parts if p)
    return text[:DESC_MAX]


def _start_session(token: str, meta: dict, size: int) -> str:
    body = json.dumps(meta).encode()
    req = urllib.request.Request(UPLOAD_URL, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Length": str(size),
        "X-Upload-Content-Type": "video/mp4",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            location = resp.headers.get("Location")
    except urllib.error.HTTPError as exc:
        raise YouTubeError(f"starting upload: {exc.read().decode()[:400]}") from exc
    if not location:
        raise YouTubeError("the API gave no upload URL")
    return location


def _send_bytes(location: str, token: str, path: Path, size: int) -> dict:
    data = path.read_bytes()
    req = urllib.request.Request(location, data=data, method="PUT", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "video/mp4",
        "Content-Length": str(size),
    })
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise YouTubeError(f"sending video: {exc.read().decode()[:400]}") from exc


def upload(video: Path, title: str, description: str,
           category_id: str = "", privacy: str = "",
           attempts: int = 3) -> str:
    """Upload and return the video id. Retries the transient failures."""
    if not enabled():
        raise YouTubeError("YouTube is not configured (YT_* secrets missing)")
    if not video.exists():
        raise YouTubeError(f"no such file: {video}")

    size = video.stat().st_size
    meta = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": str(category_id or config.YT_CATEGORY_ID),
        },
        "status": {
            "privacyStatus": privacy or config.YT_PRIVACY,
            # Required by YouTube; omitting it rejects the upload.
            "selfDeclaredMadeForKids": False,
        },
    }

    last = None
    for attempt in range(1, attempts + 1):
        try:
            token = access_token()
            location = _start_session(token, meta, size)
            result = _send_bytes(location, token, video, size)
            vid = result.get("id")
            if not vid:
                raise YouTubeError(f"no id in response: {str(result)[:200]}")
            return vid
        except YouTubeError as exc:
            last = exc
            print(f"  ! youtube attempt {attempt}: {str(exc)[:160]}")
            if attempt < attempts:
                delay = min(60, 5 * (2 ** (attempt - 1)))
                print(f"    waiting {delay}s")
                time.sleep(delay)
    raise last or YouTubeError("upload failed")


def watch_url(video_id: str) -> str:
    return f"https://youtube.com/shorts/{video_id}"
