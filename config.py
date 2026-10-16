"""Central configuration. Everything is overridable via environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).parent
STATE_DIR = ROOT / "state"
OUT_DIR = ROOT / "out"
ASSETS_DIR = ROOT / "assets"

for d in (STATE_DIR, OUT_DIR, ASSETS_DIR):
    d.mkdir(exist_ok=True)

# ---------------------------------------------------------------- feed

# ScienceDaily RSS feeds. Add or remove freely.
FEEDS = [
    "https://www.sciencedaily.com/rss/plants_animals/botany.xml",
    "https://www.sciencedaily.com/rss/plants_animals/microbiology.xml",
    "https://www.sciencedaily.com/rss/plants_animals/agriculture_and_food.xml",
]

# Skip stories older than this many days.
MAX_STORY_AGE_DAYS = 5

# ---------------------------------------------------------------- script

TARGET_SECONDS = 85          # desired reel length (Reels allow up to 3 min)
MAX_SECONDS = 92             # hard ceiling; drafts longer than this retry
# The delivered narration pace, and what the script is sized against. The
# two must agree: a 229-word narration ran 106s at +8% (2.15 w/s), so
# hitting 3.3 w/s needs roughly +66%. Sizing the script faster than the
# voice actually speaks is what pushed reels to 107-109s against an 85s
# target.
WORDS_PER_SECOND = float(os.getenv("WORDS_PER_SECOND", "3.0"))

# At 85s the script needs real substance, so the RSS blurb alone is not
# enough source material -- the full article is fetched and summarised.
USE_FULL_ARTICLE = True

# Which LLM rewrites the summary into a script.
# "gemini" | "groq" | "ollama" | "none"
# "none" falls back to a deterministic template (safe, but flat).
SCRIPT_PROVIDER = os.getenv("SCRIPT_PROVIDER", "gemini")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# ---------------------------------------------------------------- voice

# `edge-tts --list-voices` shows every option. All free, no key.
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-AndrewMultilingualNeural")
TTS_RATE = os.getenv("TTS_RATE", "+40%")

# Pitch multiplier for engines with no male voice. gTTS has exactly one
# English voice and it is female, which is why reels came out female while
# TTS_VOICE named a male one. 1.0 leaves audio alone; 0.88 reads as male.
VOICE_DEEPEN = float(os.getenv("VOICE_DEEPEN", "0.88"))

# Narration engines, tried in order until one produces audio. edge sounds
# best but talks to an undocumented Microsoft endpoint that 403s datacenter
# IPs without notice -- which killed every build on 13 Oct. gtts needs no
# key either; espeak is offline and cannot be blocked, so it is the floor
# that guarantees a reel still gets made.
TTS_PROVIDERS = os.getenv("TTS_PROVIDERS", "edge,gtts,espeak")

# ---------------------------------------------------------------- video

WIDTH, HEIGHT = 1080, 1920
FPS = 30

# x264 preset for the final encode. "medium" looks best; "veryfast" is ~4x
# quicker and the difference is hard to see on a phone.
VIDEO_PRESET = os.getenv("VIDEO_PRESET", "veryfast")

# Stock imagery. Openverse and Wikimedia need no key and are always tried;
# these two keys are optional and improve the look when present.
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")

# Crossfade length between shots, seconds.
SHOT_TRANSITION = 0.40

# No single image stays on screen longer than this. Longer beats are split
# into sub-shots with different images, which is what keeps the video
# feeling cut to the narration rather than being a slideshow.
MAX_SHOT_SECONDS = 4.0
MIN_SHOT_SECONDS = 1.6

# Ken Burns strength. Higher = more obvious movement.
KENBURNS_ZOOM = 0.22

# Darken imagery so white captions stay readable over it.
SHOT_DIM = 0.22

# Caption styling
CAPTION_FONT = os.getenv("CAPTION_FONT", "DejaVu Sans")
CAPTION_SIZE = 86
CAPTION_ACTIVE = "&H0043F6FF"      # ASS is &HAABBGGRR -> amber highlight
CAPTION_IDLE = "&H00FFFFFF"
CAPTION_OUTLINE = "&H00000000"
CAPTION_MARGIN_V = 420             # px from bottom; keeps clear of IG's UI

# ---------------------------------------------------------------- audio

# Drop your own .mp3/.m4a/.wav files into assets/music/ and one is picked
# per video. Nothing is downloaded automatically -- see README on why.
MUSIC_DIR = ASSETS_DIR / "music"
MUSIC_ENABLED = os.getenv("MUSIC_ENABLED", "1") not in ("0", "false", "False")

# Music level relative to narration, and how hard it ducks under speech.
MUSIC_GAIN = float(os.getenv("MUSIC_GAIN", "0.16"))   # 0.0-1.0
MUSIC_DUCK_RATIO = 8                                   # sidechain ratio
MUSIC_FADE = 2.0                                       # in/out seconds

# ---------------------------------------------------------------- cover

# The scroll-stopper: a short all-caps line burned over the opening of the
# reel, which is also the frame Instagram shows in the Reels tab. Drawn as
# an overlay rather than a spliced-in card, so it adds no duration, no
# silence, and leaves the rest of the render untouched. If it fails the
# reel is rendered again without it.
COVER_ENABLED = os.getenv("COVER_ENABLED", "1") not in ("0", "false", "False")

# How long the line stays on screen, and how long it takes to fade.
COVER_SECONDS = float(os.getenv("COVER_SECONDS", "3.2"))
COVER_FADE = 0.45

# Largest size is tried first; the first that fits three lines wins.
COVER_SIZES = (118, 104, 92, 80, 70)
COVER_MAX_LINES = 3
# "middle" centres the block vertically whatever the line count, which
# is where the eye lands first. "top" uses COVER_TOP as a fixed offset.
COVER_ALIGN = os.getenv("COVER_ALIGN", "middle")
COVER_TOP = int(os.getenv("COVER_TOP", "330"))     # px from the top, align=top only
COVER_MAX_CHARS = 34                                # hard cap on the phrase

# Which millisecond Instagram should use as the Reels-tab thumbnail. Inside
# the overlay window, so the grid shows the phrase rather than a random frame.
COVER_THUMB_MS = int(os.getenv("COVER_THUMB_MS", "900"))

# ---------------------------------------------------------------- outro

# Spoken call to action, appended to every script. Empty string disables.
CTA_TEXT = os.getenv("CTA_TEXT", "Follow for more science, every day.")

# Text on the end card.
CTA_CARD = os.getenv("CTA_CARD", "FOLLOW\nfor more science")
CTA_CARD_SECONDS = 2.6

# ---------------------------------------------------------------- publish

REQUIRE_APPROVAL = os.getenv("REQUIRE_APPROVAL", "1") not in ("0", "false", "False")

# Auto-publish: a reel posts itself this many minutes after being built,
# unless it was rejected in the meantime. The build workflow waits this
# long and then reads Telegram, so the window is real rather than
# depending on the (heavily throttled) poll cron.
AUTO_PUBLISH = os.getenv("AUTO_PUBLISH", "1") not in ("0", "false", "False")
AUTO_PUBLISH_MINUTES = int(os.getenv("AUTO_PUBLISH_MINUTES", "10"))

# Tiers above this still need an explicit tap. 'caution' lets science,
# health and odd-crime post themselves; conflict/markets/politics do not.
AUTO_PUBLISH_MAX_TIER = os.getenv("AUTO_PUBLISH_MAX_TIER", "caution")

IG_USER_ID = os.getenv("IG_USER_ID", "")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
IG_GRAPH_VERSION = os.getenv("IG_GRAPH_VERSION", "v21.0")

# Where the finished mp4 is uploaded so Instagram can fetch it by URL.
# "r2" | "github" | "manual"
HOST_PROVIDER = os.getenv("HOST_PROVIDER", "manual")

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY", "")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY", "")
R2_BUCKET = os.getenv("R2_BUCKET", "")
R2_PUBLIC_BASE = os.getenv("R2_PUBLIC_BASE", "")  # e.g. https://pub-xxxx.r2.dev

# Optional free notification channel for the approval step.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------- attribution

HASHTAGS = (
    "#science #research #sciencenews #biology #plantscience "
    "#microbiology #scicomm #didyouknow"
)
