"""Build an ASS subtitle file with word-by-word highlighting.

Words are grouped into short phrases that sit on screen together; the word
currently being spoken is highlighted. This is the standard "kinetic caption"
look, and because the timings come from the TTS engine itself it stays in
sync without any forced alignment.
"""
from __future__ import annotations

from pathlib import Path

import config
from core.tts import Word

MAX_WORDS_PER_PHRASE = 4
MAX_CHARS_PER_PHRASE = 28
PHRASE_BREAK_GAP = 0.45   # a pause longer than this starts a new phrase

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},{idle},{outline},&H99000000,-1,0,0,0,100,100,0,0,1,7,3,2,80,80,{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _group(words: list[Word]) -> list[list[Word]]:
    phrases: list[list[Word]] = []
    cur: list[Word] = []
    chars = 0

    for i, w in enumerate(words):
        gap = w.start - words[i - 1].end if i else 0.0
        too_long = len(cur) >= MAX_WORDS_PER_PHRASE or chars + len(w.text) > MAX_CHARS_PER_PHRASE
        if cur and (too_long or gap > PHRASE_BREAK_GAP):
            phrases.append(cur)
            cur, chars = [], 0
        cur.append(w)
        chars += len(w.text) + 1

    if cur:
        phrases.append(cur)
    return phrases


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def build_ass(words: list[Word], path: Path) -> Path:
    """Write an .ass file; returns the path."""
    lines = [HEADER.format(
        w=config.WIDTH, h=config.HEIGHT,
        font=config.CAPTION_FONT, size=config.CAPTION_SIZE,
        idle=config.CAPTION_IDLE, outline=config.CAPTION_OUTLINE,
        mv=config.CAPTION_MARGIN_V,
    )]

    for phrase in _group(words):
        for active in range(len(phrase)):
            start = phrase[active].start
            end = phrase[active].end
            if end <= start:
                end = start + 0.08

            parts = []
            for j, w in enumerate(phrase):
                txt = _escape(w.text)
                if j == active:
                    parts.append(f"{{\\c{config.CAPTION_ACTIVE}\\fscx108\\fscy108}}{txt}"
                                 f"{{\\c{config.CAPTION_IDLE}\\fscx100\\fscy100}}")
                else:
                    parts.append(txt)

            body = " ".join(parts)
            lines.append(
                f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{body}"
            )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def build_srt(words: list[Word], path: Path) -> Path:
    """Plain .srt of the same phrases, for upload as a caption file."""
    out = []
    for i, phrase in enumerate(_group(words), 1):
        start, end = phrase[0].start, phrase[-1].end
        text = " ".join(w.text for w in phrase)
        out.append(
            f"{i}\n"
            f"{_srt_ts(start)} --> {_srt_ts(end)}\n"
            f"{text}\n"
        )
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def _srt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
