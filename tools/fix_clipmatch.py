#!/usr/bin/env python3
"""Reject stock clips that have nothing to do with the shot.

The 28 Oct reel was about an AI screwdriver and showed social-media
scrolling and a horse's eye. Eleven shots, all "matched", all wrong.

The cause is that Pixabay's VIDEO library is a small fraction of its
image library, and its API answers every query with something rather
than with nothing. "damaged screw head" has no matching footage, so it
returns whatever is popular. fix_stockvideo.py then took the first
result unconditionally, because that is what the image path did -- and
with the image pool being huge, first-result was usually fine.

So the rule that was missing: A RELEVANT STILL BEATS AN IRRELEVANT CLIP.
Motion is worth nothing if the picture is about something else.

Pixabay returns per-hit `tags`, which is the signal to use: require at
least one meaningful word in common between the query and the tags.
"electric screwdriver closeup" against tags "social media, phone,
scrolling" shares nothing and is dropped; against "screwdriver, tool,
workshop" it shares "screwdriver" and is kept.

A query where nothing passes returns no clips at all, and resolve_shot
already falls through to the photo sources -- so the failure mode
becomes "today's behaviour" rather than "wrong picture".

The accepted clip's matching words are printed, so the build log finally
shows WHY a clip was chosen. That is what was missing: eleven lines
saying "[clip] electric screwdriver closeup" told us a clip was found,
never that it was right.

CLIP_MATCH_MIN=0 disables the gate and restores the old behaviour.

    python3 tools/fix_clipmatch.py
"""
import ast
import pathlib
import sys

CFG_EDITS = [(
    'STOCK_VIDEO_MIN_PX = int(os.getenv("STOCK_VIDEO_MIN_PX") or 540)\n',
    '''STOCK_VIDEO_MIN_PX = int(os.getenv("STOCK_VIDEO_MIN_PX") or 540)
# How many words a clip's tags must share with the shot query before the
# clip is believed. Pixabay's video library is small and its API answers
# every query with something, so without this a reel about an AI
# screwdriver gets social-media scrolling and a horse's eye -- which is
# exactly what happened on 28 Oct. 0 disables the gate.
CLIP_MATCH_MIN = int(os.getenv("CLIP_MATCH_MIN") or 1)
''',
    "CLIP_MATCH_MIN", 1)]

VISUALS_EDITS = [
    # the matcher, placed just before the video sources that use it
    ('''def _src_pexels_video(query: str) -> list[dict]:
''',
     '''# Words that say nothing about subject matter. A query is mostly these
# plus one or two real nouns, and matching on "closeup" would let any
# close-up of anything through.
_CLIP_STOP = {
    "the", "and", "with", "for", "from", "into", "onto", "over", "under",
    "closeup", "close", "shot", "view", "macro", "angle", "detail",
    "footage", "video", "clip", "scene", "background", "abstract",
    "modern", "new", "old", "big", "small", "slow", "fast", "person",
    "people", "man", "woman", "hand", "hands",
}


def _clip_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower())
            if len(w) > 2 and w not in _CLIP_STOP}


def _clip_matches(query: str, *fields: str) -> set:
    """Meaningful words the query and the clip's own metadata share.

    The only defence against a video API that would rather return
    something irrelevant than nothing at all.
    """
    want = _clip_words(query)
    if not want:
        return set()
    have = set()
    for f in fields:
        have |= _clip_words(f)
    return want & have


def _clip_ok(query: str, shared: set) -> bool:
    """Is the clip really about this shot, or does it just share a word?

    One shared modifier is not a match. "electric screwdriver closeup"
    against a clip titled "Cutting Wood With a Circular Electric Saw"
    shares "electric" -- and is still a saw. That got through an
    earlier version of this gate and was caught in testing.

    English noun phrases put the head noun last, so the query's final
    content word is its subject. Accept a clip that matches the subject,
    or one that matches two content words: "macro lens camera sensor"
    against "camera lens" shares two and is genuinely close, while
    sharing only "electric" is not.
    """
    if len(shared) < config.CLIP_MATCH_MIN:
        return False
    words = [w for w in re.findall(r"[a-z]+", (query or "").lower())
             if len(w) > 2 and w not in _CLIP_STOP]
    head = words[-1] if words else ""
    return head in shared or len(shared) >= 2


def _src_pexels_video(query: str) -> list[dict]:
''',
     "def _clip_matches", 1),

    # gate pexels: its only text is the url slug
    ('''        slug = (v.get("url", "").rstrip("/").split("/")[-1] or query)
        out.append({
''',
     '''        slug = (v.get("url", "").rstrip("/").split("/")[-1] or query)
        shared = _clip_matches(query, slug.replace("-", " "))
        if not _clip_ok(query, shared):
            continue
        out.append({
            "match": sorted(shared),
''',
     "shared = _clip_matches(query, slug", 1),

    # gate pixabay: it gives real tags, which is the better signal
    ('''        out.append({
            "url": pick["url"],
            "credit": {
                "title": h.get("tags", query),
''',
     '''        shared = _clip_matches(query, h.get("tags", ""))
        if not _clip_ok(query, shared):
            continue
        out.append({
            "match": sorted(shared),
            "url": pick["url"],
            "credit": {
                "title": h.get("tags", query),
''',
     "shared = _clip_matches(query, h.get", 1),

    # say WHY the clip was picked, not just that one was
    ('''                print(f"    shot {index:2d}  {cand.get('via_source','video'):10s} "
                      f"[clip] {shot.query[:28]}")
''',
     '''                why = ",".join(cand.get("match") or []) or "?"
                print(f"    shot {index:2d}  {cand.get('via_source','video'):10s} "
                      f"[clip] {shot.query[:26]}  <-{why[:24]}")
''',
     "<-{why[:24]}", 1),
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


def add_yml(path: pathlib.Path) -> str:
    if not path.exists():
        return f"  {path}: not here, skipped"
    src = path.read_text()
    if "CLIP_MATCH_MIN" in src:
        return f"  {path}: already patched"
    anchor = "          PIXABAY_API_KEY: ${{ secrets.PIXABAY_API_KEY }}\n"
    if src.count(anchor) != 1:
        return f"  {path}: anchor appears {src.count(anchor)}x -- NOT TOUCHED"
    new = src.replace(anchor, anchor +
                      "          # 0 disables the relevance gate on stock clips.\n"
                      "          CLIP_MATCH_MIN: ${{ vars.CLIP_MATCH_MIN || '1' }}\n"
                      "          STOCK_VIDEO: ${{ vars.STOCK_VIDEO || '1' }}\n")
    try:
        import yaml
        doc = yaml.safe_load(new)
        envs = [s.get("env") or {}
                for j in doc["jobs"].values() for s in j["steps"]]
        if not any("CLIP_MATCH_MIN" in e for e in envs):
            return f"  {path}: did not land in a step env -- NOT WRITTEN"
    except ImportError:
        pass
    except Exception as exc:
        return f"  {path}: YAML would not parse ({exc}) -- NOT WRITTEN"
    path.write_text(new)
    return f"  {path}: CLIP_MATCH_MIN + STOCK_VIDEO passed through"


def main() -> None:
    root = pathlib.Path(".")
    if not (root / "core/visuals.py").exists():
        sys.exit("run from a channel repo")
    if "_src_pixabay_video" not in (root / "core/visuals.py").read_text():
        sys.exit("run tools/fix_stockvideo.py first -- no video sources here")
    print("only believe a clip that matches the shot:")
    print(apply(root / "config.py", CFG_EDITS))
    print(apply(root / "core/visuals.py", VISUALS_EDITS))
    print(add_yml(root / ".github/workflows/build.yml"))
    print("""
  The build log now says why each clip was chosen:

    shot  0  pixabay-v  [clip] electric screwdriver   <-screwdriver

  A shot with no matching footage falls through to a photo, which is
  the right answer -- a relevant still beats an irrelevant clip.
  Expect a MIX of [clip] and photo lines now, not eleven clips.

  Too strict?   gh variable set CLIP_MATCH_MIN --body 2   (stricter)
  Off entirely? gh variable set STOCK_VIDEO --body 0
""")


if __name__ == "__main__":
    main()
