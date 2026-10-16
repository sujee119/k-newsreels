"""Telegram approval flow.

The build job sends you the finished reel with two buttons. A separate poll
job (running on a schedule) picks up which button you pressed and either
publishes to Instagram or discards the reel.

Buttons rather than chat commands, because callback_data survives being
read on a phone days later and carries the slug with it -- there is no
"which reel did they mean" ambiguity.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

import config

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method: str, **kwargs) -> dict:
    url = API.format(token=config.TELEGRAM_BOT_TOKEN, method=method)
    files = kwargs.pop("files", None)
    resp = requests.post(url, data=kwargs, files=files, timeout=180)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram {method}: {data.get('description')}")
    return data["result"]


def enabled() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


# ------------------------------------------------------------------ sending

def send_text(text: str) -> dict:
    return _call("sendMessage", chat_id=config.TELEGRAM_CHAT_ID, text=text,
                 parse_mode="HTML", disable_web_page_preview=True)


def send_for_approval(video: Path, slug: str, title: str,
                      caption_preview: str, duration: float) -> dict:
    """Send the reel with Approve / Reject buttons. Returns the message."""
    keyboard = {"inline_keyboard": [[
        {"text": "Approve + post", "callback_data": f"ok:{slug}"},
        {"text": "Reject", "callback_data": f"no:{slug}"},
    ]]}

    body = (
        f"<b>{title[:150]}</b>\n"
        f"{duration:.0f}s\n\n"
        f"<i>Caption preview</i>\n{caption_preview[:600]}"
    )

    size_mb = video.stat().st_size / 1e6
    if size_mb > 48:
        # Bot API caps uploads at 50 MB; send the text and let the link serve.
        return _call("sendMessage", chat_id=config.TELEGRAM_CHAT_ID,
                     text=body + f"\n\n(video is {size_mb:.0f} MB, too large "
                                 f"to attach -- review from the release link)",
                     parse_mode="HTML",
                     reply_markup=json.dumps(keyboard))

    with open(video, "rb") as fh:
        return _call("sendVideo", chat_id=config.TELEGRAM_CHAT_ID,
                     caption=body, parse_mode="HTML",
                     supports_streaming=True,
                     reply_markup=json.dumps(keyboard),
                     files={"video": (video.name, fh, "video/mp4")})


# ------------------------------------------------------------------ polling

OFFSET_PATH = config.STATE_DIR / "tg_offset.json"


def _load_offset() -> int:
    if OFFSET_PATH.exists():
        try:
            return int(json.loads(OFFSET_PATH.read_text()).get("offset", 0))
        except Exception:
            return 0
    return 0


def _save_offset(offset: int) -> None:
    OFFSET_PATH.write_text(json.dumps({"offset": offset}))


def fetch_decisions() -> list[tuple[str, str, int, int]]:
    """Return [(action, slug, chat_id, message_id)] for new button presses.

    The update offset is persisted so the same press is never acted on twice
    -- important when the poll job runs every few minutes.
    """
    offset = _load_offset()
    updates = _call("getUpdates", offset=offset + 1, timeout=0,
                    allowed_updates=json.dumps(["callback_query"]))

    decisions = []
    highest = offset
    for upd in updates:
        highest = max(highest, upd["update_id"])
        cq = upd.get("callback_query")
        if not cq:
            continue
        data = cq.get("data", "")
        if ":" not in data:
            continue
        action, slug = data.split(":", 1)
        if action not in ("ok", "no"):
            continue
        msg = cq.get("message", {})
        decisions.append((action, slug, msg.get("chat", {}).get("id"),
                          msg.get("message_id")))
        try:
            _call("answerCallbackQuery", callback_query_id=cq["id"],
                  text="Working on it...")
        except RuntimeError:
            pass   # a stale callback id is not worth failing the run over

    if not decisions:
        # Silent "no decisions" hides whether updates arrived at all, which
        # made a missing-callback bug very hard to pin down.
        raw = _call("getUpdates", offset=offset + 1, timeout=0)
        kinds = [k for u in raw for k in u.keys() if k != "update_id"]
        print(f"  (telegram: offset={offset}, {len(raw)} raw update(s)"
              f"{', types: ' + ','.join(sorted(set(kinds))) if kinds else ''})")

    if highest != offset:
        _save_offset(highest)
    return decisions


def mark_done(chat_id: int, message_id: int, text: str) -> None:
    """Replace the buttons with a final status so it can't be pressed twice."""
    try:
        _call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
              reply_markup=json.dumps({"inline_keyboard": []}))
    except RuntimeError:
        pass
    try:
        send_text(text)
    except RuntimeError:
        pass
