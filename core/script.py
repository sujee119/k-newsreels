"""Turn a ScienceDaily summary into an original narration script.

IMPORTANT: ScienceDaily's summaries are copyrighted text. This module is
deliberately written to produce an independent rewrite -- different sentence
structure, different vocabulary, spoken register -- not a reflow of the
original wording. The similarity guard at the bottom rejects drafts that
stay too close to the source.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import config

UA = "scidaily-reels/1.0"


SECTIONS = ("hook", "groundwork", "method", "findings", "sowhat", "button")


@dataclass
class Script:
    hook: str
    groundwork: str
    method: str
    findings: str
    sowhat: str
    button: str
    caption: str          # the Instagram post caption
    narration: str        # what actually gets spoken
    shots: list = field(default_factory=list)   # [{"cue":..., "query":...}]
    hashtags: list = field(default_factory=list)
    hook_options: list = field(default_factory=list)
    cover_text: str = ""    # the scroll-stopper burned over the opening

    def word_count(self) -> int:
        return len(self.narration.split())

    def outline(self) -> str:
        body = "\n\n".join(
            f"{name.upper()}\n{getattr(self, name)}" for name in SECTIONS)
        if self.hook_options:
            alts = "\n".join(f"  {i+1}. {o}"
                              for i, o in enumerate(self.hook_options))
            body += f"\n\n---\nHOOKS CONSIDERED (swap one in if you prefer)\n{alts}\n"
        return body


SYSTEM_PROMPT = """You write retention-optimised narration for {total_seconds}-second
vertical science videos. Your job is to make a viewer who stops scrolling stay
to the last second -- without ever overstating what the research actually found.

You will be given the TITLE and SUMMARY of a science news story.

Your viewer is a curious non-scientist. They have no background in this
field. By the end they should be able to explain the study to someone else:
what question it asked, what the researchers actually did, what they found,
and how much it means. Retention devices keep them watching -- but the
substance is the point, not decoration around a hook.

== THE STRUCTURE (follow this shape) ==

HOOK (0-{t_hook}s, {hook_min}-{hook_max} words) -- THE MOST IMPORTANT LINE YOU WRITE

  Most viewers leave in the first two seconds. The hook is the whole job.

  WRITE THE HOOK LAST. Draft the rest of the script first, so you know
  exactly what the payoff is, then come back and write an opening that
  earns it. A hook written before you know the ending always hedges.

  BUILD IT FROM THIS ARTICLE AND NOTHING ELSE. Every noun in your hook --
  the organism, the place, the object, the number -- must come from the
  source text you were given above. Do not borrow subject matter from the
  examples below; they are unrelated stories, shown only to demonstrate
  the SHAPE of a good opening.

  What a good hook does, in four parts:
    1. Names the actual thing, as specifically as the article allows.
    2. Names where or when, if the article says.
    3. Makes one big claim that is genuinely true of this study.
    4. Gives one concrete detail that makes you want the rest.

  Shape examples, all from DIFFERENT stories -- imitate the construction,
  never the content:

    "A fabric invented for army uniforms just tripled a strawberry harvest,
     and it works by letting the rain through while keeping beetles out."

    "Zoo lions fed one big carcass a week started behaving like wild lions
     within days. They spent three times longer eating."

    "A 97-million-year-old fossil is magnetic. Something in the ancient
     ocean may have been navigating by Earth's magnetic field."

  Each names its own subject, its own number, its own claim. Yours must do
  the same using only what is in your source text.

  Do NOT follow a template. Write the sentence you would text a friend if
  you had just read this paper and wanted them to care in five seconds.

  Then write two more that open in genuinely different ways -- a different
  entry point into the same story, not a reworded version of the first.
  Put all three in "hook_options" and pick the best as "hook".

  Only three hard rules:
   - Don't open with "Scientists", "Researchers", "A new study", "Have you
     ever", "Did you know", "Imagine" or "What if". Lead with the thing
     itself.
   - Every striking claim must be true and must be paid off later in the
     video. Dramatic is good; vague or unearned is not.
   - Say it out loud. If it sounds like a press release, rewrite it.

GROUNDWORK (5-25s)
  Teach the one idea the viewer needs before anything else can land. Assume
  zero background. Define every term the moment you use it, in the same
  breath, with a plain comparison. This is the section that makes the video
  worth watching rather than just scrolling past -- do not rush it.
  If the study is about a protein, say what the protein normally does. If
  it's about an organism, say what kind of thing it is.

WHAT THEY ACTUALLY DID (25-45s)
  The method, in plain language. Who did it, what they measured, on what,
  and how they could tell. Be concrete: "they tracked 40 lions for six
  months", not "researchers conducted a study". Numbers help -- but only
  numbers that appear in the source. If the sample was small, or it was
  done in mice, or in a dish, say so here, plainly and without apology.

WHAT THEY FOUND (45-68s)
  The result, in two or three beats, each adding one new fact. This is
  where you close the loop from the hook. Use short pivots between beats:
  "But here's the thing." / "It gets stranger." / "And then they checked
  again."

SO WHAT (68-82s)
  What this changes, and what it does not. Be honest about the limits --
  what would still need to be shown before this matters practically. A
  viewer who understands the caveat trusts you more, not less.

BUTTON (82-85s)
  One short line that lands the idea. Stop there.

== HARD RULES ==

1. ORIGINAL WORDING. The summary is copyrighted. Do not reuse its sentences,
   phrasing, or sentence order. Re-explain from scratch in spoken English.
   Never copy more than three consecutive words from the summary.

2. THE HOOK MUST BE HONEST. You may tease, withhold, and delay. You may NOT
   promise something the story does not contain. If your hook implies a
   revelation, the payoff must deliver exactly that revelation. A loop you
   open and never close is the one unforgivable error here.

3. ACCURACY OVER DRAMA, ALWAYS. Association is not cause. Mice are not
   humans. Lab results are not treatments. "May" is not "will". If the
   finding is preliminary, the script says so -- preferably inside a beat,
   so it costs no retention. Never invent a number, a name, or a result.

4. BANNED PHRASES. These are hype with no curiosity behind them:
   "changes everything", "scientists are baffled", "they never expected",
   "this will blow your mind", "you won't believe", "breaking the internet",
   "game-changer", "groundbreaking", "the truth about", "what they found
   next will shock you", "not what you think" used with no actual reversal.
   You MAY use a reversal line like "it's not what you'd expect" ONLY when
   the story contains a genuine reversal of an obvious assumption, and you
   then state what the real answer is.

5. SPOKEN RHYTHM. Short sentences. Vary length. Fragments are fine. No
   subordinate-clause pile-ups. Read it aloud in your head -- if you run out
   of breath, cut it.

6. NO JARGON without an immediate plain gloss. "A protist -- a single-celled
   thing, not a plant or an animal."

7. EXPLAIN, DON'T GESTURE. "They used a new sequencing technique" tells the
   viewer nothing. "They read the organism's DNA letter by letter, using a
   method fast enough to do a whole genome in a day" teaches them something.
   Every claim in the script must trace to the source material given to you.
   If the source does not say how something was done, do not guess -- leave
   it out and spend the words on what the source does say.

8. LENGTH: target {target_words} words. Hard ceiling {max_words}. This is a
   TIGHT format. Every sentence must carry information the one before it did
   not. There is no room for scene-setting, restatement or a warm-up line --
   the first sentence is already the hook. If a sentence does not teach
   something or move the story, cut it rather than shorten it. Pick the one
   most interesting thing the study found and spend the words there; a
   shallow tour of four findings is worse than one explained properly.

9. No call to action. No "follow for more". End on meaning.

== THE SHOT LIST ==

You also decide what the viewer SEES. Supply {shots_min}-{shots_max} shots covering the
whole script. More distinct shots means more distinct imagery -- too few and
the same picture has to be held for many seconds. Each shot is a stock-image search query plus the exact narration
phrase it should appear on.

- "cue" MUST be 3-5 words copied VERBATIM from your own narration, exactly
  as written, including the same punctuation-free spelling. It marks where
  that image cuts in. Cues must run in narration order and be spread evenly
  across the whole script -- do not cluster them all in the first {cluster_seconds}s.
- "query" is 2-4 plain words aimed at a stock photo library. Think about
  what photograph actually exists. "pond water sample" exists.
  "a gene being misread" does not.
- Prefer concrete, photographable subjects: the organism, the instrument,
  the lab bench, the crop, the landscape, the animal, a microscope view.
- For abstract beats, choose an honest visual metaphor from the subject's
  own world -- for a DNA-reading beat, "DNA sequencing gel" beats
  "glowing digital brain". Never pick something that implies a claim the
  study does not make.
- Do not reuse the same query twice.

Return ONLY valid JSON, no markdown fence:
{{
  "hook_options": ["three different hooks, using three different patterns"],
  "hook":       "the strongest of your three, copied exactly",
  "groundwork": "{t_hook}-{t_ground}s, the one piece of background the viewer needs, no more",
  "method":     "{t_ground}-{t_method}s, what the researchers actually did",
  "findings":   "{t_method}-{t_find}s, what they found, closing the hook's loop",
  "sowhat":     "{t_find}-{t_sowhat}s, what it changes and what it doesn't",
  "button":     "{t_sowhat}-{total_seconds}s, one closing line",
  "loop_open":  "in a few words, what question the hook plants",
  "loop_close": "in a few words, where that question gets answered",
  "cover_text": "2-5 WORDS, ALL CAPS, the scroll-stopper (see COVER TEXT below)",
  "caption":    "2-3 sentence Instagram caption, worded differently from the narration",
  "hashtags":   ["3-5 lowercase tags specific to THIS story, no # symbol, no spaces"],
  "shots": [
    {{"cue": "3-5 words verbatim from your narration",
      "query": "stock photo search term"}}
    // {shots_min}-{shots_max} of these, in narration order, each query different
  ]
}}"""


COVER_GUIDANCE = """

== COVER TEXT (the words on the thumbnail) ==

Separate from the hook. The hook is heard; this is READ, in the Reels tab,
by someone scrolling past at speed with the sound off. It is two to five
words, in capitals, and it is the only thing standing between your story
and a thumb flick.

Write it from THIS article. Name the actor the story is actually about --
the agency, the organism, the technology, the place -- using a word that
appears in the title or the text. A cover line that would fit a hundred
other stories is a wasted one.

What makes one work:
  - it claims something happened, rather than describing a subject
  - it creates a gap the viewer wants closed, and the video closes it
  - it survives being read in under a second
  - it is true to what the research found, at the strength it found it

What kills one:
  - a topic label ("NEW SPACE RESEARCH") -- that is a caption, not a hook
  - a question, which invites "no" and a scroll
  - words longer than about twelve letters; they shrink the whole line
  - anything the article does not actually support

Five words maximum. No punctuation except an exclamation mark if it earns
one. Never reuse wording you put in the spoken hook -- the viewer should
get something new when the audio starts.
"""


TIER_GUIDANCE = {
    "oddcrime": (
        "\n\nThis is an unusual-crime story, told for how absurd it is.\n"
        "- NEVER name the accused, the suspect or any private individual. "
        "Say 'a man', 'two women', 'the thieves'.\n"
        "- Say 'charged' or 'accused', never guilty, unless the source "
        "states a conviction.\n"
        "- The humour is in the plan going wrong, never at a victim's "
        "expense. If anyone was hurt, this is the wrong story -- say so "
        "in the hook rather than playing it for laughs.\n"
        "- No speculation about motive."),
    "caution": (
        "\n\nThis is a HEALTH story. Do not imply a treatment is available "
        "or proven unless the source says so. Name the study stage (cells, "
        "animals, trial phase) in the method section. The 'so what' section "
        "must state plainly what has not been shown yet."),
    "sensitive": (
        "\n\nThis is a BREAKING NEWS story about conflict, markets or crime. "
        "Early reporting on these is frequently corrected later.\n"
        "- Attribute every contested claim to who said it: 'officials said', "
        "'the company reported'. Never state a disputed claim in your own "
        "voice.\n"
        "- Do not give casualty figures, and do not speculate about blame, "
        "motive or what happens next.\n"
        "- For markets: report what happened. Never imply what anyone should "
        "buy, sell or hold, and never forecast a price.\n"
        "- For crime: say 'charged' or 'accused', never guilty, and do not "
        "name private individuals.\n"
        "- The 'so what' section must say what is still unconfirmed."),
}


def _build_user_prompt(title: str, summary: str, article: str = "",
                       tier: str = "safe") -> str:
    parts = [f"TITLE: {title}", f"SUMMARY: {summary}"]
    if article:
        parts.append(
            "FULL ARTICLE (source material for your own rewrite -- this is "
            "copyrighted text, so do not reuse its wording):\n" + article)
    text = "\n\n".join(parts)
    return text + TIER_GUIDANCE.get(tier, "")


# ----------------------------------------------------------------- providers

def _post_json(url: str, payload: dict, headers: dict, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA, **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


_GEMINI_MODEL_CACHE: list[str] = []


def _gemini_models() -> list[str]:
    """Ask the key which models it can actually call.

    Hardcoding a model name is brittle: Google renames and retires them, and
    availability differs between keys and tiers, which shows up as a 404 on
    generateContent even though the key is perfectly valid. So we ask.
    """
    if _GEMINI_MODEL_CACHE:
        return _GEMINI_MODEL_CACHE

    candidates: list[str] = []
    if config.GEMINI_MODEL:
        candidates.append(config.GEMINI_MODEL)

    try:
        url = ("https://generativelanguage.googleapis.com/v1beta/models"
               f"?key={config.GEMINI_API_KEY}&pageSize=200")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())

        usable = []
        for m in data.get("models", []):
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            name = m["name"].removeprefix("models/")
            if any(bad in name for bad in ("vision", "embedding", "aqa", "tts",
                                           "image", "live", "thinking")):
                continue
            usable.append(name)

        # Prefer fast, cheap, recent. Flash models suit short scripts.
        def rank(n: str) -> tuple:
            return (0 if "flash" in n else 1,
                    0 if "latest" not in n else 1,
                    -len(n))

        usable.sort(key=rank)
        candidates += [u for u in usable if u not in candidates]
    except Exception as exc:
        print(f"  ! could not list Gemini models: {exc}")

    _GEMINI_MODEL_CACHE[:] = candidates
    return candidates


def _call_gemini(system: str, user: str) -> str:
    models = _gemini_models()
    if not models:
        raise RuntimeError(
            "No Gemini model available. Check GEMINI_API_KEY, or run:\n"
            "  curl -s 'https://generativelanguage.googleapis.com/v1beta/"
            "models?key=YOUR_KEY' | grep '\"name\"'")

    last = None
    for model in models[:6]:
        for attempt in (1, 2, 3):
            try:
                return _gemini_once(model, system, user)
            except urllib.error.HTTPError as exc:
                # 429 is the free tier's per-minute cap; 5xx is Gemini
                # being down. Both are transient and neither was
                # retried here -- only 404 was handled, so the 503s of
                # 20 Oct raised on the first call, four times over.
                if exc.code == 429 or 500 <= exc.code < 600:
                    if attempt < 3:
                        wait = 25 * attempt
                        why = ("rate-limited (429)" if exc.code == 429
                               else f"returned {exc.code}, its end not ours")
                        print(f"  ! gemini {why}, waiting {wait}s "
                              f"-- attempt {attempt} of 3")
                        time.sleep(wait)
                        continue
                    last = exc
                    break
                if exc.code != 404:
                    raise
                last = exc
                print(f"  ! model '{model}' not available, trying next")
                if model in _GEMINI_MODEL_CACHE:
                    _GEMINI_MODEL_CACHE.remove(model)
                break
    raise last or RuntimeError("no usable Gemini model")


def _gemini_once(model: str, system: str, user: str) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={config.GEMINI_API_KEY}")
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"},
    }
    data = _post_json(url, payload, {})
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_groq(system: str, user: str) -> str:
    payload = {
        "model": config.GROQ_MODEL,
        "temperature": 0.8,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = _post_json(
        "https://api.groq.com/openai/v1/chat/completions",
        payload,
        {"Authorization": f"Bearer {config.GROQ_API_KEY}"},
    )
    return data["choices"][0]["message"]["content"]


def _call_ollama(system: str, user: str) -> str:
    payload = {
        "model": config.OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = _post_json(f"{config.OLLAMA_HOST}/api/chat", payload, {})
    return data["message"]["content"]


PROVIDERS = {"gemini": _call_gemini, "groq": _call_groq, "ollama": _call_ollama}


class AllProvidersFailed(OSError):
    """Every provider in the chain failed.

    OSError deliberately: each failure was a failed network call, and
    write_script's attempt loop already catches OSError. So the chain
    is retried across all four attempts with no new handler, and only
    a total outage reaches _fallback().
    """


def call(system: str, user: str) -> str:
    """Ask each provider in turn until one answers.

    Narration has worked this way since the start -- TTS_PROVIDERS is
    a comma-separated chain and speak() walks it, which is why a dead
    voice service has never cost a reel. The script asked one
    provider, so Gemini's 503s on 20 Oct took both reels channels down
    for twelve hours while four attempts hit the same dead endpoint.

    Same signature as a provider function, so callers already doing
    fn(system, user) need no change.
    """
    names = [n.strip() for n in config.SCRIPT_PROVIDERS.split(",") if n.strip()]
    problems: list[str] = []
    for name in names:
        fn = PROVIDERS.get(name)
        if fn is None:
            problems.append(f"{name}: no such provider")
            continue
        try:
            out = fn(system, user)
        except Exception as exc:
            problems.append(f"{name}: {type(exc).__name__} {str(exc)[:80]}")
            print(f"  ! {name} unavailable, trying the next provider")
            continue
        if name != names[0]:
            print(f"  ! answered by '{name}', not '{names[0]}'")
        return out
    raise AllProvidersFailed(
        "no script provider answered:\n  " + "\n  ".join(problems))


# ----------------------------------------------------------- similarity guard

def _shingles(text: str, n: int = 4) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def overlap_ratio(draft: str, source: str, n: int = 4) -> float:
    """Fraction of the draft's n-grams that also appear in the source."""
    d, s = _shingles(draft, n), _shingles(source, n)
    if not d:
        return 0.0
    return len(d & s) / len(d)


IG_CAPTION_LIMIT = 2150   # Instagram's hard cap is 2200

MAX_OVERLAP = 0.12  # above this, the draft is too close to the original


# ------------------------------------------------------------- hype filter

BANNED_PHRASES = [
    "changes everything", "scientists are baffled", "baffled scientists",
    "they never expected", "blow your mind", "you won't believe",
    "you wont believe", "breaking the internet", "game-changer",
    "game changer", "groundbreaking", "the truth about",
    "will shock you", "shocked the world", "doctors hate",
    "nobody saw this coming", "this is huge", "mind-blowing",
    "mind blowing", "insane discovery", "wait for it",
]

# Overclaim patterns: hedged findings stated as settled fact.
OVERCLAIM_PATTERNS = [
    (r"\bproves?\s+that\b", "states proof"),
    (r"\bcures?\b", "claims a cure"),
    (r"\bwill\s+(?:cure|treat|eliminate|end)\b", "promises a future certainty"),
    (r"\bdefinitely\b", "overstated certainty"),
    (r"\bconfirms?\s+that\s+\w+\s+causes?\b", "asserts causation"),
]


def find_banned(text: str) -> list[str]:
    low = text.lower()
    return [p for p in BANNED_PHRASES if p in low]


def find_overclaims(text: str) -> list[str]:
    hits = []
    for pattern, label in OVERCLAIM_PATTERNS:
        if re.search(pattern, text, flags=re.I):
            hits.append(label)
    return hits


# Weakening a claim can never make it less accurate, so overclaims are
# repaired in place rather than thrown away. Retrying on these just burned
# API calls and often came back with the same phrasing.
SOFTENERS = [
    (r"\bproves\s+that\b", "suggests that"),
    (r"\bprove\s+that\b", "suggest that"),
    (r"\bproven\s+to\b", "shown to"),
    (r"\bdefinitely\b", "likely"),
    (r"\bwill\s+cure\b", "might one day help treat"),
    (r"\bwill\s+treat\b", "might one day help treat"),
    (r"\bwill\s+eliminate\b", "might reduce"),
    (r"\bwill\s+end\b", "might help end"),
    (r"\bcures\b", "may help treat"),
    (r"\bcure\b", "potential treatment for"),
    (r"\bconfirms\s+that\b", "indicates that"),
]


def soften(text: str) -> tuple[str, list[str]]:
    """Hedge overclaiming language. Returns (text, what_changed)."""
    changed = []
    for pattern, repl in SOFTENERS:
        new = re.sub(pattern, repl, text, flags=re.I)
        if new != text:
            changed.append(repl)
            text = new
    return text, changed


# Distinctive nouns from the shape examples in the prompt. If one shows up
# in a hook but not in the article, the model echoed the example.
EXAMPLE_TOKENS = [
    "strawberry", "strawberries", "beetles", "uniforms", "fabric",
    "lions", "carcass", "zoo",
    "fossil", "magnetic", "ocean",
]

WEAK_OPENERS = [
    "scientists", "researchers", "a new study", "a study", "in a new",
    "have you ever", "did you know", "imagine", "what if", "picture this",
    "new research", "a team of", "experts", "according to",
    "this is the story", "let me tell you", "here's something",
]


_STOP = {
    "the", "and", "that", "this", "with", "from", "they", "them", "their",
    "what", "when", "which", "there", "here", "then", "than", "been", "were",
    "have", "has", "had", "into", "over", "about", "just", "only", "some",
    "something", "surprising", "study", "research", "new", "found", "team",
    "could", "would", "should", "makes", "think", "things", "really",
    "every", "single", "your", "you", "it", "its", "was", "are", "but",
}


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOP}


# Words that make a cover line generic. A phrase built only from these
# describes a subject instead of claiming something happened.
_COVER_FILLER = {
    "the", "a", "an", "of", "in", "on", "for", "and", "or", "to", "with",
    "new", "news", "study", "studies", "research", "scientists", "report",
    "finds", "found", "about", "this", "that", "from", "by", "at", "is",
    "are", "was", "were", "it", "its",
}

# Only these genuinely dangle at the end of a phrase. Stripping verbs or
# pronouns too turned "NASA FINALLY DID IT" into "NASA FINALLY".
_COVER_DANGLING = {
    "the", "a", "an", "of", "and", "or", "to", "with", "from", "by",
    "at", "in", "on", "for", "into", "than", "that", "as", "but",
}

_COVER_INTERROGATIVE = {
    "did", "does", "do", "can", "could", "will", "would", "is", "are",
    "was", "were", "what", "why", "how", "who", "when", "where", "should",
}

_COVER_STOP = {
    "the", "a", "an", "of", "in", "on", "for", "and", "or", "to", "with",
    "have", "has", "had", "this", "that", "from", "by", "at", "is", "are",
    "was", "were", "it", "its", "their", "they", "we", "us", "you", "your",
    "can", "now", "just", "did", "do", "does", "been", "be", "but", "not",
    "what", "how", "why", "when", "who", "all", "more", "most", "than",
}


def clean_cover(text: str, limit: int = config.COVER_MAX_CHARS) -> str:
    """Normalise a cover line: caps, no stray punctuation, within budget.

    The character budget comes first: capping words before length chopped
    "AI CAN NOW DO THE IMPOSSIBLE" -- 28 characters, comfortably inside the
    limit -- down to a dangling "AI CAN NOW DO THE".
    """
    t = " ".join(str(text or "").split()).upper()
    t = re.sub(r"[^A-Z0-9 !\'’-]", " ", t)
    kept: list[str] = []
    for w in t.split():
        if len(kept) >= 6:
            break
        if kept and len(" ".join(kept + [w])) > limit:
            break
        kept.append(w)
    # Never end on a word that leads somewhere, like "THE" or "AND".
    while len(kept) > 1 and kept[-1].lower().strip("!'’-") in _COVER_DANGLING:
        kept.pop()
    return " ".join(kept)


def cover_problems(cover: str, source: str = "", hook: str = "") -> list[str]:
    """Why a cover line would not stop a thumb. Advisory, never fatal."""
    raw = " ".join(str(cover or "").split())
    c = clean_cover(raw)
    if not c:
        return ["cover_text is empty"]
    words = c.split()
    if len(words) < 2:
        return ["cover_text is a single word -- it has to claim something"]
    # Checked on the raw text: clean_cover strips the question mark.
    if raw.endswith("?") or words[0].lower() in _COVER_INTERROGATIVE:
        return ["cover_text reads as a question, which invites a scroll"]
    if all(w.lower() in _COVER_FILLER for w in words):
        return ["cover_text is only generic words -- name what this story is about"]
    if source:
        # At least one content word must come from the story, the same
        # grounding rule the hook follows.
        low = source.lower()
        content = [w for w in words if w.lower() not in _COVER_STOP]
        if content and not any(w.lower().strip("!'’-") in low for w in content):
            return [f"cover_text uses nothing from the article: {c}"]
    if hook and c.lower() in " ".join(hook.lower().split()):
        return ["cover_text repeats the spoken hook verbatim"]
    return []


def cover_from_hook(hook: str, title: str = "") -> str:
    """Last resort when the model gives nothing usable.

    Takes the most specific words available rather than inventing a claim,
    so the overlay is still grounded even when it is not clever.
    """
    for src in (hook, title):
        words = [w.strip(".,;:!?'\"") for w in str(src).split()]
        keep = [w for w in words if w and w.lower() not in _COVER_STOP]
        if len(keep) >= 2:
            return clean_cover(" ".join(keep[:4]))
    return ""


def hook_problems(hook: str, source: str = "") -> list[str]:
    """Catch the hooks that will lose the viewer in two seconds."""
    problems = []
    h = hook.strip()
    low = h.lower()

    if not h:
        return ["The hook is empty."]

    words = h.split()
    if len(words) > 32:
        problems.append(
            f"The hook is {len(words)} words. Cut it to under 25 -- it has "
            f"to land in about five seconds.")
    if len(words) < 7:
        problems.append("The hook is too short to set up a loop. Aim for 12-25 words.")

    for opener in WEAK_OPENERS:
        if low.startswith(opener):
            problems.append(
                f"The hook opens with '{opener}', which reads as a press "
                f"release. Open on the subject or the action instead.")
            break

    first = re.split(r"(?<=[.!?])\s", h)[0]
    if first.rstrip().endswith("?"):
        problems.append(
            "The first sentence is a question. Questions invite scrolling -- "
            "lead with a statement.")

    # The prompt shows example hooks to demonstrate shape. If any of their
    # distinctive subject matter turns up here, the model copied the
    # example instead of reading the article.
    leaked = [t for t in EXAMPLE_TOKENS
              if re.search(rf"\b{t}\b", low) and t not in source.lower()]
    if leaked:
        problems.append(
            f"The hook borrows subject matter from the example hooks "
            f"({', '.join(sorted(set(leaked))[:3])}), which are unrelated "
            f"stories. Build the hook only from this article's own content.")

    # Is the hook grounded in the actual story? Rather than looking for
    # capital letters (which wrongly rejected hooks built on specific
    # lowercase nouns like "three letters in your DNA"), check that it
    # shares real content words with the source material.
    if source:
        shared = _content_words(h) & _content_words(source)
        has_number = bool(re.search(r"\d", h))
        has_proper = bool(re.search(r"(?<!^)(?<![.!?] )\b[A-Z][a-z]{2,}", h))
        if len(shared) < 2 and not has_number and not has_proper:
            problems.append(
                "The hook is too vague -- it could describe any study. Name "
                "the actual organism, place, number or object from this one.")

    return problems


def loop_is_closed(obj: dict) -> bool:
    """Cheap sanity check that the hook's promise is answered in the outro.

    Requires the model to have declared both ends of the loop, and that the
    payoff shares some substance with the question. Not airtight -- it
    catches the obvious failure of opening a loop and wandering off.
    """
    opened = str(obj.get("loop_open", "")).strip()
    closed = str(obj.get("loop_close", "")).strip()
    if not opened or not closed:
        return False

    # The loop is paid off in the findings section, with the button as a
    # fallback for scripts that land it one beat later.
    payoff = (str(obj.get("findings", "")) + " " + str(obj.get("button", ""))).lower()
    if len(payoff.split()) < 10:
        return False

    stop = {"the", "a", "an", "it", "its", "what", "why", "how", "is", "was",
            "that", "this", "of", "to", "in", "and", "they", "their", "for"}
    key = {w for w in re.findall(r"[a-z]{4,}", closed.lower()) if w not in stop}
    return bool(key & set(re.findall(r"[a-z]{4,}", payoff))) if key else False


# ----------------------------------------------------------------- fallback

def _fallback(title: str, summary: str) -> Script:
    """Deterministic, obviously-safe script when no LLM is available.

    Intentionally plain and short. A fallback should be visibly a fallback,
    not a confident-sounding script nobody checked.
    """
    hook = "Here's a new result worth a minute of your time."
    groundwork = (f"Researchers reported a finding described under the "
                  f"headline: {title.rstrip('.')}.")
    method = "The full write-up is linked below."
    findings = ""
    sowhat = ""
    button = "Worth reading the original if this one catches you."
    narration = " ".join(x for x in (hook, groundwork, method, button) if x)
    caption = (f"{title}\n\nFull story via ScienceDaily -- link in bio. "
               f"Credit belongs to the originating research institution.")
    return Script(hook, groundwork, method, findings, sowhat, button,
                  caption, narration, shots=[], hashtags=[],
                  cover_text=cover_from_hook("", title))


# --------------------------------------------------------------------- main

def generate(title: str, summary: str, article: str = "",
             tier: str = "safe", attempts: int = 4) -> Script:
    target_words = int(config.TARGET_SECONDS * config.WORDS_PER_SECOND)
    max_words = int(config.MAX_SECONDS * config.WORDS_PER_SECOND)
    provider = config.SCRIPT_PROVIDER

    # Everything the prompt says about pacing is derived from TARGET_SECONDS
    # rather than written into the text, so changing the reel length is one
    # number in config and the section budgets, shot count and hook size all
    # move with it. The fractions reproduce the 85s shape the prompt was
    # tuned at, so shortening rescales the structure instead of breaking it.
    total = max(12, int(config.TARGET_SECONDS))
    at = lambda frac: max(1, round(total * frac))        # noqa: E731
    t_hook, t_ground = at(0.14), at(0.30)
    t_method, t_find, t_sowhat = at(0.53), at(0.80), at(0.96)
    hook_max = max(6, round(t_hook * config.WORDS_PER_SECOND))
    hook_min = max(4, round(hook_max * 0.6))
    # One shot every 2.8-3.8s: dense enough that no still is held past
    # MAX_SHOT_SECONDS, which is what made the old 85s reels repeat images.
    shots_min = max(5, round(total / 3.8))
    shots_max = max(shots_min + 2, round(total / 2.8))
    shots_floor = max(3, shots_min - 2)
    cluster_seconds = at(0.24)

    if provider == "none":
        print("  i no LLM provider configured -- using fallback script")
        return _fallback(title, summary)

    # The whole chain, not one provider. The four attempts below used
    # to hit the same endpoint four times; now each attempt can fall
    # through to groq and then ollama.
    fn = call
    system = SYSTEM_PROMPT.format(
        target_words=target_words, max_words=max_words,
        total_seconds=total, t_hook=t_hook, t_ground=t_ground,
        t_method=t_method, t_find=t_find, t_sowhat=t_sowhat,
        hook_min=hook_min, hook_max=hook_max,
        shots_min=shots_min, shots_max=shots_max,
        cluster_seconds=cluster_seconds) + COVER_GUIDANCE
    base_user = _build_user_prompt(title, summary, article, tier)
    user = base_user
    source_text = f"{title} {summary} {article}"

    last_err = None
    best: tuple[int, dict, str, list] | None = None
    for attempt in range(1, attempts + 1):
        try:
            raw = fn(system, user)
            raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
            obj = json.loads(raw)

            narration = " ".join(
                str(obj.get(k, "")).strip() for k in SECTIONS
            ).strip()
            narration = re.sub(r"\s+", " ", narration)

            raw_shots = obj.get("shots") or []
            shots = [
                {"cue": str(s.get("cue", "")).strip(),
                 "query": str(s.get("query", "")).strip()}
                for s in raw_shots
                if isinstance(s, dict) and str(s.get("query", "")).strip()
            ]

            # Hedge overclaims in place instead of rejecting the draft.
            for key in SECTIONS:
                fixed, changed = soften(str(obj.get(key, "")))
                if changed:
                    obj[key] = fixed
            narration, softened = soften(narration)
            if softened:
                print(f"  i softened overclaiming language ({len(softened)})")

            problems: list[str] = []

            if len(shots) < shots_floor:
                problems.append(
                    f"You gave only {len(shots)} usable shots. Provide "
                    f"{shots_min}-{shots_max}, each with a cue copied verbatim "
                    f"from your narration and a concrete stock-photo query.")

            problems += hook_problems(str(obj.get("hook", "")), source_text)

            # Advisory only: a weak cover line is told to the model on a
            # retry, but it never blocks a draft that is otherwise good.
            # A reel with a plain overlay beats no reel.
            cover = clean_cover(obj.get("cover_text", ""))
            cover_issues = cover_problems(
                cover, source_text, str(obj.get("hook", "")))
            if cover_issues:
                problems.append(
                    f"{cover_issues[0]}. Give 2-5 capitalised words naming "
                    f"what this story is actually about.")
            obj["cover_text"] = cover

            opts = obj.get("hook_options") or []
            if len([o for o in opts if str(o).strip()]) < 3:
                problems.append(
                    "Give three genuinely different hook_options using three "
                    "different patterns, then pick the strongest as 'hook'.")

            missing = [s for s in SECTIONS if not str(obj.get(s, "")).strip()]
            if missing:
                problems.append(
                    f"These sections are empty: {', '.join(missing)}. "
                    f"Every section must be written.")

            ratio = overlap_ratio(narration, source_text)
            if ratio > MAX_OVERLAP:
                problems.append(
                    f"You reused the source's phrasing ({ratio:.0%} overlap). "
                    f"Rewrite the whole thing in completely different words.")

            banned = find_banned(narration)
            if banned:
                problems.append(
                    f"You used banned hype phrases: {', '.join(banned)}. "
                    f"Replace them with a concrete detail from the story.")

            overclaims = find_overclaims(narration)
            if overclaims:
                problems.append(
                    f"You overstated the finding ({'; '.join(overclaims)}). "
                    f"Hedge it the way the source does.")

            if not loop_is_closed(obj):
                problems.append(
                    "The loop you opened in the hook is not clearly answered in "
                    "the outro. Make the payoff explicitly resolve the hook.")

            words = len(narration.split())
            if words > max_words:
                problems.append(
                    f"Too long at {words} words. Cut to under {target_words}.")
            elif words < target_words * 0.65:
                problems.append(
                    f"Too short at {words} words. This needs to fill "
                    f"{config.TARGET_SECONDS} seconds -- aim for {target_words}.")

            if problems:
                print(f"  ! attempt {attempt}: {len(problems)} issue(s)")
                for p in problems:
                    print(f"      - {p.split('.')[0]}")
                if best is None or len(problems) < best[0]:
                    best = (len(problems), dict(obj), narration, shots)
                user = (base_user + "\n\nYour previous attempt had these "
                        "problems. Fix all of them:\n- " + "\n- ".join(problems))
                continue

            est = words / config.WORDS_PER_SECOND
            print(f"  + script ok: {words} words (~{est:.0f}s), "
                  f"{ratio:.0%} source overlap, loop closed")
            return Script(
                **{s: str(obj.get(s, "")).strip() for s in SECTIONS},
                caption=str(obj.get("caption", title)).strip(),
                narration=narration,
                shots=shots,
                hashtags=[re.sub(r'[^a-z0-9]', '', str(t).lower())
                          for t in (obj.get('hashtags') or [])][:5],
                hook_options=[str(o).strip() for o in opts if str(o).strip()],
                cover_text=(cover or cover_from_hook(
                    str(obj.get("hook", "")), title)),
            )

        # OSError, not URLError. A read timeout raises TimeoutError, and
        # TimeoutError is a SIBLING of URLError under OSError, not a
        # subclass -- so the old clause let it straight through. It then
        # escaped this retry loop, the best-draft path below and
        # _fallback(), and killed the whole build.
        #
        # That is the 19 Oct outage: two 503s from Gemini were caught and
        # retried correctly, then attempt 3 hung for the full 90s and
        # raised "The read operation timed out". The 503s were survivable;
        # the hang was not.
        #
        # OSError also covers connection resets, DNS and TLS failures,
        # which are the same kind of transient problem.
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            last_err = exc
            print(f"  ! attempt {attempt} failed: {exc}")
            # A 503 means "busy, come back shortly". Retrying four times
            # inside 1.4 seconds -- which is what this did -- is the same
            # as not retrying, and it threw away a whole build.
            if attempt < attempts:
                delay = min(45, 4 * (2 ** (attempt - 1)))
                print(f"    waiting {delay}s before retrying")
                time.sleep(delay)

    # Nothing was perfect. A near-miss draft beats the plain template, so
    # take the best one and say plainly what is still wrong with it.
    if best is not None:
        count, obj, narration, shots = best
        print(f"  ~ no clean draft after {attempts} attempts; using the best "
              f"one ({count} remaining issue(s)) -- review script.txt")
        # ...but not at any length. "Too long at N words" was raised as a
        # problem above and then ignored right here, which is how a
        # 174-word script became a 58-second reel on 22 Oct against a
        # 99-word ceiling. Ten of fourteen reels built after 21 Oct were
        # over it.
        narration, shots = _trim_to_ceiling(narration, shots, max_words)
        return Script(
            **{s_: str(obj.get(s_, "")).strip() for s_ in SECTIONS},
            caption=str(obj.get("caption", title)).strip(),
            narration=narration,
            shots=shots,
            hook_options=[str(o).strip()
                          for o in (obj.get("hook_options") or []) if str(o).strip()],
            cover_text=(clean_cover(obj.get("cover_text", ""))
                        or cover_from_hook(str(obj.get("hook", "")), title)),
        )

    print(f"  ! all attempts failed ({last_err}) -- using fallback")
    return _fallback(title, summary)


def _trim_to_ceiling(narration: str, shots: list,
                     max_words: int) -> tuple[str, list]:
    """Bring an over-long draft under the word ceiling.

    Sentences come out of the MIDDLE, never the end. The format is a
    hook that opens a loop and a closing line that pays it off, and
    loop_is_closed() is checked precisely because that payoff is the one
    line the reel cannot ship without. Cutting the tail -- the obvious
    implementation -- would remove it every time.

    Shots carry a "cue" copied verbatim from the narration, so any shot
    whose cue was in a dropped sentence no longer has anything to sync
    to and goes with it.
    """
    words = narration.split()
    if len(words) <= max_words:
        return narration, shots

    parts = re.findall(r"[^.!?]+[.!?]*\s*", narration)
    parts = [p for p in parts if p.strip()]
    if len(parts) < 3:                     # nothing safe to take out
        print(f"  ! {len(words)} words, over the {max_words} ceiling, but "
              f"it is {len(parts)} sentence(s) -- shipping it whole")
        return narration, shots

    keep = list(parts)
    i = len(keep) - 2                      # last sentence before the payoff
    while sum(len(p.split()) for p in keep) > max_words and len(keep) > 2:
        if i <= 0:                         # never drop the hook
            i = len(keep) - 2
            if i <= 0:
                break
        keep.pop(i)
        i -= 1

    out = "".join(keep).strip()
    n = len(out.split())
    print(f"  ~ trimmed {len(words)} words to {n} ({len(parts) - len(keep)} "
          f"sentence(s) out of the middle); hook and payoff kept")
    if n > max_words:
        # Hook plus payoff alone are over the ceiling. Nothing further can
        # come out without losing one of them, so say so rather than
        # reporting a ceiling that was not actually reached.
        print(f"  ! still {n} words, over the {max_words} ceiling -- the "
              f"hook and the payoff alone exceed it. Shipping at "
              f"~{n / max(0.1, config.WORDS_PER_SECOND):.0f}s.")

    low = out.lower()
    kept_shots = [s for s in shots
                  if str(s.get("cue", "")).strip().lower() in low]
    if len(kept_shots) != len(shots):
        print(f"  ~ dropped {len(shots) - len(kept_shots)} shot(s) whose cue "
              f"was in a trimmed sentence")
    return out, (kept_shots or shots)


def build_caption(script: Script, title: str, link: str,
                  topic: str = "", cfg: dict | None = None,
                  credits: str = "") -> str:
    """Assemble the Instagram caption.

    Structure matters here: Instagram truncates after roughly 125
    characters, so the hook sentence has to come first and the
    housekeeping goes at the bottom. Links are not clickable in captions,
    so the URL is given for attribution rather than as a call to action.
    """
    cfg = cfg or {}
    parts = [script.caption.strip()]

    parts.append(f"Source: {_publisher_name(link)} - {title.strip()}")
    parts.append(link.strip())
    parts.append("Written in our own words. Credit for the research belongs "
                 "to the study authors and their institution.")

    if credits.strip():
        parts.append(credits.strip())

    tags = _collect_hashtags(script, topic, cfg)
    if tags:
        parts.append(" ".join(tags))

    caption = "\n\n".join(p for p in parts if p)

    # Instagram rejects captions over 2200 characters. Drop the image
    # credits block first (the licences are also in the repo), then the
    # attribution line, rather than truncating mid-hashtag.
    if len(caption) > IG_CAPTION_LIMIT and credits.strip():
        parts = [x for x in parts if x != credits.strip()]
        parts.insert(-1, "Image credits in the linked source.")
        caption = "\n\n".join(p for p in parts if p)
    if len(caption) > IG_CAPTION_LIMIT:
        tags = parts[-1] if parts[-1].startswith("#") else ""
        body = caption[: IG_CAPTION_LIMIT - len(tags) - 8].rsplit(" ", 1)[0]
        caption = f"{body}...\n\n{tags}".strip()
    return caption


def _publisher_name(link: str) -> str:
    try:
        host = urllib.parse.urlparse(link).netloc.lower()
    except ValueError:
        return "source"
    host = host[4:] if host.startswith("www.") else host
    return host.split(".")[0].replace("-", " ").title() or "source"


def _collect_hashtags(script: Script, topic: str, cfg: dict) -> list[str]:
    """Story-specific tags first, then topic tags, then a few generic ones.

    A fixed list on every post is both less relevant and less useful --
    a black hole story tagged #plantscience helps nobody. Instagram also
    rewards a handful of accurate tags over a wall of broad ones, so this
    caps at 12.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(tag: str) -> None:
        t = tag if tag.startswith("#") else f"#{tag}"
        t = re.sub(r"[^#a-z0-9]", "", t.lower())
        if len(t) > 2 and t not in seen:
            seen.add(t)
            out.append(t)

    for t in script.hashtags:
        add(t)
    for t in (cfg.get("topics", {}).get(topic, {}).get("hashtags") or []):
        add(t)
    for t in (cfg.get("base_hashtags") or []):
        add(t)

    return out[:12]
