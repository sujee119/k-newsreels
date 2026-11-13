#!/usr/bin/env python3
"""Use MoneyPrinterTurbo's clip-search design instead of per-shot queries.

Reading harry0703/MoneyPrinterTurbo showed the difference was never
filtering -- it was WHAT GETS ASKED of the video library:

    MoneyPrinterTurbo          this pipeline (until now)
    5 terms per VIDEO          1 query per SHOT
    1-3 words, English         specific noun phrases
    "always add the main       written for a stock PHOTO library
      subject of the video"
    fetch all terms' candi-    per-shot query, first hit wins
      dates, then select

Their own comment: fetch candidates for all keywords first, then select.

A small video library can answer "power tools". It cannot answer
"damaged screw head", so it returns whatever is popular -- which is how
a reel about an AI screwdriver got social-media scrolling and a horse's
eye. And because every MPT term carries the video's main subject, a
term can never wander off-topic in the first place.

WHAT THIS DOES

  * script.py gains video_terms(), which asks the existing provider
    chain for chronological search terms using MPT's prompt, vendored
    VERBATIM (MIT, Copyright (c) 2024 Harry -- see the docstring in the
    generated function). Their match_script_order mode is the one used:
    terms in narration order, so term N lands on shot N.
  * visuals.py gains prime_clip_pool(), which fetches candidates for
    every term across every video source and pools them.
  * resolve_shot draws from that pool first. Per-shot video search
    stays as a second try for when the pool is empty (LLM down, no
    terms), and photos remain the final fallback.

Rather than reimplementing their search, this adopts their design and
leaves the existing per-source code -- which already mirrors theirs on
rendition choice and resolution floor -- in place.

MPT_TERMS=0 disables the pool and restores per-shot queries.

    python3 tools/fix_mptterms.py
"""
import ast
import pathlib
import sys

CFG_EDITS = [(
    'CLIP_MATCH_MIN = int(os.getenv("CLIP_MATCH_MIN") or 1)\n',
    '''CLIP_MATCH_MIN = int(os.getenv("CLIP_MATCH_MIN") or 1)
# How many broad, subject-anchored search terms to ask the model for,
# MoneyPrinterTurbo style, instead of one narrow query per shot. Their
# default is 5; capped against the shot count at the call site. 0
# disables the pool and restores per-shot queries.
MPT_TERMS = int(os.getenv("MPT_TERMS") or 6)
''',
    "MPT_TERMS", 1)]

# --------------------------------------------------------------- script.py
SCRIPT_EDITS = [(
    '''def build_caption(script: Script, title: str, link: str,
''',
    '''def video_terms(subject: str, narration: str, amount: int = 6) -> list[str]:
    """Broad, subject-anchored stock-video search terms, in script order.

    The prompt below is MoneyPrinterTurbo's generate_terms prompt, used
    verbatim under its MIT licence (Copyright (c) 2024 Harry,
    github.com/harry0703/MoneyPrinterTurbo). It is quoted rather than
    paraphrased because its exact constraints are the thing that works:
    1-3 words, English only, and "always add the main subject of the
    video" -- which is what stops a term wandering off the topic and
    returning a horse's eye for a story about a screwdriver.

    match_script_order is their chronological mode, so term N describes
    an earlier moment than term N+1 and the pool can be assigned to
    shots in order.
    """
    amount = max(1, int(amount or 1))
    example = json.dumps(
        ["opening visual topic"]
        + [f"script visual topic {i}" for i in range(2, max(amount, 1))]
        + ["final visual topic"])
    prompt = f"""
# Role: Video Search Terms Generator

## Goals:
Generate {amount} chronological stock-video search terms that follow the order of topics in the video script.

## Constrains:
1. the search terms are to be returned as a json-array of strings.
2. each search term should consist of 1-3 words, always add the main subject of the video.
3. you must only return the json-array of strings. you must not return anything else. you must not return the script.
4. the search terms must be related to the subject of the video.
5. reply with english search terms only.
6. keep the terms in the same order as the script narration; earlier terms must describe earlier visual moments.

## Output Example:
{example}

## Context:
### Video Subject
{subject}

### Video Script
{narration}

Please note that you must use English for generating video search terms; Chinese is not accepted.
""".strip()

    try:
        raw = call("You generate stock-video search terms.", prompt)
    except Exception as exc:
        print(f"  ! video terms unavailable ({type(exc).__name__}) -- "
              f"falling back to per-shot queries")
        return []

    # The chain is told json_object, so a bare array may arrive wrapped.
    terms = None
    try:
        obj = json.loads(raw)
        if isinstance(obj, list):
            terms = obj
        elif isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, list):
                    terms = v
                    break
    except json.JSONDecodeError:
        m = re.search(r"\\[.*?\\]", raw, re.S)
        if m:
            try:
                terms = json.loads(m.group(0))
            except json.JSONDecodeError:
                terms = None
    if not isinstance(terms, list):
        print("  ! video terms were not a json array -- per-shot queries")
        return []

    out, seen = [], set()
    for t in terms:
        t = " ".join(str(t).split())[:60]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    print(f"  video terms: {', '.join(out[:amount]) or '(none)'}")
    return out[:amount]


def build_caption(script: Script, title: str, link: str,
''',
    "def video_terms(", 1)]

# -------------------------------------------------------------- visuals.py
VISUALS_EDITS = [(
    '''def resolve_shot(shot: Shot, workdir: Path, index: int,
''',
    '''_CLIP_POOL: list[dict] = []


def prime_clip_pool(terms: list[str]) -> int:
    """Fetch clips for every term up front, then let shots draw on them.

    MoneyPrinterTurbo's model: candidates for all keywords first, then
    select. Asking the library one broad question per video beats asking
    it a narrow one per shot, because a small video index can answer
    "power tools" and cannot answer "damaged screw head".

    Terms arrive in narration order, so the pool is in rough script
    order and shots can take from the front.
    """
    _CLIP_POOL.clear()
    if not terms:
        return 0
    seen = set()
    for term in terms:
        for name, fn in VIDEO_SOURCES:
            try:
                cands = fn(term)
            except Exception:
                continue
            for cand in cands:
                if cand["url"] in seen:
                    continue
                seen.add(cand["url"])
                cand["via_source"] = name
                cand["term"] = term
                _CLIP_POOL.append(cand)
    print(f"  clip pool: {len(_CLIP_POOL)} clip(s) from "
          f"{len(terms)} term(s)")
    return len(_CLIP_POOL)


def resolve_shot(shot: Shot, workdir: Path, index: int,
''',
    "def prime_clip_pool(", 1),
(
    '''    if config.STOCK_VIDEO:
        for cand in video_candidates_for(shot.query):
''',
    '''    # The pool first: built from the whole script's terms, so a clip
    # here was chosen for the video rather than scraped for this shot.
    if config.STOCK_VIDEO and _CLIP_POOL:
        for cand in list(_CLIP_POOL):
            if cand["url"] in used:
                _CLIP_POOL.remove(cand)
                continue
            dest = workdir / f"shot{index:02d}.mp4"
            if _download_video(cand["url"], dest):
                used.add(cand["url"])
                _CLIP_POOL.remove(cand)
                shot.path = dest
                shot.credit = cand["credit"]
                shot.is_video = True
                print(f"    shot {index:2d}  {cand.get('via_source','pool'):10s} "
                      f"[pool] {cand.get('term','')[:26]}")
                return True
            _CLIP_POOL.remove(cand)

    if config.STOCK_VIDEO:
        for cand in video_candidates_for(shot.query):
''',
    "[pool] ", 1),
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


RUN_EDITS = [(
    '''    used: set[str] = set()
    found = sum(visuals.resolve_shot(s, work, i, used)
''',
    '''    # MoneyPrinterTurbo's approach: ask the model for a handful of
    # broad, subject-anchored terms covering the whole script, fetch
    # every term's clips into one pool, then let the shots draw on it.
    # Per-shot queries asked the video library eleven narrow questions
    # it could not answer.
    if getattr(config, "MPT_TERMS", 0):
        terms = script_mod.video_terms(
            story.title, narration,
            amount=max(1, min(config.MPT_TERMS, len(shots))))
        visuals.prime_clip_pool(terms)

    used: set[str] = set()
    found = sum(visuals.resolve_shot(s, work, i, used)
''',
    "MPT_TERMS", 1)]


def add_yml(path: pathlib.Path) -> str:
    if not path.exists():
        return f"  {path}: not here, skipped"
    src = path.read_text()
    if "MPT_TERMS" in src:
        return f"  {path}: already patched"
    anchor = "          CLIP_MATCH_MIN: ${{ vars.CLIP_MATCH_MIN || '1' }}\n"
    if src.count(anchor) != 1:
        return f"  {path}: anchor appears {src.count(anchor)}x -- NOT TOUCHED"
    new = src.replace(anchor, anchor +
                      "          # 0 restores per-shot queries.\n"
                      "          MPT_TERMS: ${{ vars.MPT_TERMS || '6' }}\n")
    try:
        import yaml
        doc = yaml.safe_load(new)
        envs = [s.get("env") or {}
                for j in doc["jobs"].values() for s in j["steps"]]
        if not any("MPT_TERMS" in e for e in envs):
            return f"  {path}: did not land in a step env -- NOT WRITTEN"
    except ImportError:
        pass
    except Exception as exc:
        return f"  {path}: YAML would not parse ({exc}) -- NOT WRITTEN"
    path.write_text(new)
    return f"  {path}: MPT_TERMS passed through"


def main() -> None:
    root = pathlib.Path(".")
    reels = (root / "core/publish.py").exists()
    if not reels:
        sys.exit("reels repos only for now (needs run.py's [5/7] block); "
                 "the shorts repo builds its shots differently")
    vis = root / "core/visuals.py"
    if "_src_coverr_video" not in vis.read_text():
        sys.exit("run tools/fix_coverr.py first")
    print("MoneyPrinterTurbo clip-search design:")
    print(apply(root / "config.py", CFG_EDITS))
    print(apply(root / "core/script.py", SCRIPT_EDITS))
    print(apply(vis, VISUALS_EDITS))
    print(apply(root / "run.py", RUN_EDITS))
    print(add_yml(root / ".github/workflows/build.yml"))
    print("""
  Next build logs the terms and the pool:

    video terms: ai screwdriver, power tools, precision driver, ...
    clip pool: 34 clip(s) from 6 term(s)
      shot  0  coverr-v   [pool] ai screwdriver

  [pool] means a clip chosen for the video. [clip] is the old per-shot
  path, kept for when the pool is empty. A photo line means neither had
  anything, which is the correct answer.

  Vendored from MoneyPrinterTurbo (MIT, (c) 2024 Harry).
  MPT_TERMS=0 restores per-shot queries.
""")


if __name__ == "__main__":
    main()
