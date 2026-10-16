"""Host the finished MP4 somewhere public, then publish it to Instagram.

Instagram's Graph API does not accept a file upload for Reels -- it fetches
the video from a URL you give it. So the video must be publicly reachable
for the duration of the publish. Three strategies are supported:

  r2      Cloudflare R2 (free tier, S3-compatible, needs boto3)
  github  attach to a GitHub release via the `gh` CLI
  manual  print the path and wait for you to supply a URL

The publish itself is the documented three-step flow:
  1. create a media container (media_type=REELS, video_url=...)
  2. poll the container until status_code == FINISHED
  3. publish the container

Skipping step 2 is what produces error 9007 in production.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import config

UA = "scidaily-reels/1.0"
GRAPH = "https://graph.facebook.com"


# ------------------------------------------------------------------ hosting

def _host_r2(path: Path) -> str:
    import boto3  # lazy: only needed for this strategy

    if not all([config.R2_ACCOUNT_ID, config.R2_ACCESS_KEY,
                config.R2_SECRET_KEY, config.R2_BUCKET, config.R2_PUBLIC_BASE]):
        raise RuntimeError("R2 selected but R2_* settings are incomplete")

    client = boto3.client(
        "s3",
        endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=config.R2_ACCESS_KEY,
        aws_secret_access_key=config.R2_SECRET_KEY,
        region_name="auto",
    )
    key = f"reels/{path.name}"
    client.upload_file(str(path), config.R2_BUCKET, key,
                       ExtraArgs={"ContentType": "video/mp4"})
    return f"{config.R2_PUBLIC_BASE.rstrip('/')}/{key}"


def _host_github(path: Path) -> str:
    import subprocess
    tag = f"reel-{path.stem}"
    subprocess.run(["gh", "release", "create", tag, str(path),
                    "--title", tag, "--notes", "auto-generated reel"],
                   check=True, capture_output=True)
    out = subprocess.run(["gh", "release", "view", tag, "--json", "assets"],
                         check=True, capture_output=True, text=True)
    assets = json.loads(out.stdout)["assets"]
    return assets[0]["url"]


def host(path: Path) -> str:
    provider = config.HOST_PROVIDER
    if provider == "r2":
        return _host_r2(path)
    if provider == "github":
        return _host_github(path)
    raise RuntimeError(
        f"HOST_PROVIDER is '{provider}'. The file is ready at {path} but no "
        f"automatic host is configured -- Instagram needs a public URL. "
        f"Set HOST_PROVIDER=r2 or github, or upload it yourself and pass "
        f"--video-url.")


# ---------------------------------------------------------------- instagram

def _graph_post(endpoint: str, params: dict, timeout: int = 60) -> dict:
    url = f"{GRAPH}/{config.IG_GRAPH_VERSION}/{endpoint}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Graph API {endpoint}: {exc.read().decode()[:500]}")


def _graph_get(endpoint: str, params: dict, timeout: int = 60) -> dict:
    url = (f"{GRAPH}/{config.IG_GRAPH_VERSION}/{endpoint}?"
           + urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Graph API {endpoint}: {exc.read().decode()[:500]}")


IG_CAPTION_MAX = 2200


def trim_caption(caption: str, limit: int = IG_CAPTION_MAX) -> str:
    """Hard-trim at publish time.

    The generator also caps caption length, but manifests built before that
    existed still carry long captions, and a stored caption can come from
    any version of the code. Enforcing it here means the API can never see
    an oversized caption, whatever produced it.
    """
    caption = (caption or "").strip()
    if len(caption) <= limit:
        return caption

    # Keep the hashtag block if it is the last paragraph.
    blocks = caption.split("\n\n")
    tags = blocks[-1] if blocks and blocks[-1].lstrip().startswith("#") else ""
    body_limit = limit - (len(tags) + 6 if tags else 4)

    body = "\n\n".join(blocks[:-1] if tags else blocks)[:body_limit]
    body = body.rsplit(" ", 1)[0].rstrip(" ,.;:-")
    out = f"{body}...\n\n{tags}".strip() if tags else f"{body}..."
    print(f"  i caption trimmed {len(caption)} -> {len(out)} chars")
    return out[:limit]


def publish_reel(video_url: str, caption: str,
                 poll_seconds: int = 8, max_wait: int = 600) -> str:
    """Run the three-step publish. Returns the published media id."""
    if not (config.IG_USER_ID and config.IG_ACCESS_TOKEN):
        raise RuntimeError("IG_USER_ID and IG_ACCESS_TOKEN must be set")

    caption = trim_caption(caption)

    print("  -> creating media container")
    params = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": config.IG_ACCESS_TOKEN,
    }
    # Pick the Reels-tab thumbnail from inside the cover overlay, so the
    # grid shows the scroll-stopper instead of whichever frame Instagram
    # happens to choose. If the API will not take it, publish without --
    # a default thumbnail is worth far less than a failed post.
    thumb_ms = getattr(config, "COVER_THUMB_MS", 0)
    try:
        if thumb_ms and getattr(config, "COVER_ENABLED", False):
            container = _graph_post(f"{config.IG_USER_ID}/media",
                                    {**params, "thumb_offset": str(thumb_ms)})
        else:
            container = _graph_post(f"{config.IG_USER_ID}/media", params)
    except Exception as exc:
        print(f"  ! thumb_offset rejected ({str(exc)[:120]}); "
              f"publishing with the default thumbnail")
        container = _graph_post(f"{config.IG_USER_ID}/media", params)
    cid = container["id"]
    print(f"     container {cid}")

    print("  -> waiting for Instagram to finish processing")
    waited = 0
    while waited < max_wait:
        time.sleep(poll_seconds)
        waited += poll_seconds
        status = _graph_get(cid, {
            "fields": "status_code,status",
            "access_token": config.IG_ACCESS_TOKEN,
        })
        code = status.get("status_code")
        if code == "FINISHED":
            print(f"     ready after {waited}s")
            break
        if code == "ERROR":
            raise RuntimeError(f"processing failed: {status.get('status')}")
        print(f"     {code} ({waited}s)")
    else:
        raise RuntimeError(f"container not ready after {max_wait}s")

    print("  -> publishing")
    result = _graph_post(f"{config.IG_USER_ID}/media_publish", {
        "creation_id": cid,
        "access_token": config.IG_ACCESS_TOKEN,
    })
    return result["id"]


def _fingerprint(caption: str) -> str:
    """A caption's identifying head: first line, letters and digits only.

    Instagram returns captions it may have normalised, and ours carry
    hashtags and credits that could change between builds, so comparison
    uses the opening line rather than the whole string.
    """
    head = (caption or "").strip().splitlines()[0] if (caption or "").strip() else ""
    return "".join(c for c in head.lower() if c.isalnum())[:60]


class LookupUnavailable(Exception):
    """The account could not be asked. Distinct from 'nothing matched'.

    Callers must not read this as 'not posted': autopublish treats it as a
    reason to go ahead, reconcile as a reason to say it does not know.
    """


def already_posted(caption: str, look_back: int = 25) -> str | None:
    """The media id of an existing post with this caption, if there is one.

    State lives in a git commit that can fail to push, so a reel may be
    live while the repo still calls it pending. Asking the account itself
    is the only check that cannot drift. Returns None on any API trouble
    -- a failed lookup must not block publishing.
    """
    want = _fingerprint(caption)
    if not want:
        return None
    # An empty id would build the URL ".../media", which the API answers
    # with a confusing "Object with ID 'media' does not exist". Say the
    # real thing instead.
    if not config.IG_USER_ID or not config.IG_ACCESS_TOKEN:
        raise LookupUnavailable(
            "IG_USER_ID / IG_ACCESS_TOKEN are not set in this shell -- "
            "run: set -a && source .env && set +a")
    try:
        data = _graph_get(f"{config.IG_USER_ID}/media", {
            "fields": "id,caption",
            "limit": look_back,
            "access_token": config.IG_ACCESS_TOKEN,
        })
    except Exception as exc:
        raise LookupUnavailable(str(exc)) from exc
    for item in data.get("data", []):
        if _fingerprint(item.get("caption", "")) == want:
            return item.get("id")
    return None


def remaining_quota() -> int | None:
    """Instagram allows ~50 published posts per rolling 24h."""
    try:
        data = _graph_get(f"{config.IG_USER_ID}/content_publishing_limit", {
            "fields": "config,quota_usage",
            "access_token": config.IG_ACCESS_TOKEN,
        })
        row = data["data"][0]
        return row.get("config", {}).get("quota_total", 50) - row.get("quota_usage", 0)
    except Exception:
        return None


# ------------------------------------------------------------- notification

def notify(text: str, video_path: Path | None = None) -> None:
    """Optional Telegram ping for the approval step. Silent if unconfigured."""
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    try:
        base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
        data = urllib.parse.urlencode({
            "chat_id": config.TELEGRAM_CHAT_ID, "text": text}).encode()
        urllib.request.urlopen(
            urllib.request.Request(f"{base}/sendMessage", data=data), timeout=30)
    except Exception as exc:
        print(f"  ! notify failed: {exc}")
