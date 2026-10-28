#!/usr/bin/env python3
"""Make the Groq fallback actually exist.

The build of 28 Oct died with all three script providers down:

    gemini: TimeoutError The read operation timed out
    groq:   HTTPError HTTP Error 404: Not Found
    ollama: URLError <urlopen error [Errno 111] Connection refused>

Gemini wobbling is Google's business. The other two are ours, and both
were broken from the day the chain was written:

  * GROQ 404s. config.GROQ_MODEL is "llama-3.3-70b-versatile", which
    Groq retired on 16 August 2026 -- two months before the chain was
    built on 20 October. Every Groq call since has 404'd.
  * SHORTS IS WORSE. core/llm.py reads config.GROQ_MODEL and the shorts
    config.py never defined it at all, so the call raises AttributeError
    instead. call() catches Exception, so it reported "groq unavailable"
    and looked like an outage.
  * OLLAMA CANNOT WORK on a GitHub runner -- nothing is listening on
    localhost:11434. It is third in the chain and only useful locally.

So the chain has been gemini-only on all three channels since it was
built, which is exactly why one Gemini 503 stops a channel for hours.
The redundancy was imaginary.

Two changes:

  1. GROQ_MODELS, a comma-separated LIST, walked until one answers. A
     single hardcoded name is how this died quietly; a list means the
     next retirement costs one model, not the whole link. 400 and 404
     move to the next name; 401, 429 and 5xx are about the account or
     the service, so they propagate instead of masquerading as a
     missing model.
  2. GROQ_MODELS is passed through build.yml. GROQ_MODEL never was,
     which is why no repo variable could have fixed this -- the same
     trap as STOCK_VIDEO and CTA_TEXT before it.

The default list leads with openai/gpt-oss-120b (currently listed
production, and the strongest of them for structured JSON) and ends with
llama-3.1-8b-instant, which is small but reliably present.

    python3 tools/fix_groq.py
"""
import ast
import pathlib
import sys

MODELS = "openai/gpt-oss-120b,llama-3.3-70b-versatile,llama-3.1-8b-instant"

CFG_BLOCK = '''# A LIST, not one name. "llama-3.3-70b-versatile" was retired by Groq on
# 16 Aug 2026 and every call 404'd silently for months, which left the
# provider chain gemini-only without anything saying so. Walked in order
# until one answers, so the next retirement costs one model.
#
# Tunable without a code change:
#   gh variable set GROQ_MODELS --body 'openai/gpt-oss-120b,llama-3.1-8b-instant'
GROQ_MODELS = (os.getenv("GROQ_MODELS") or os.getenv("GROQ_MODEL")
               or "%s")
''' % MODELS

# The reels repos define GROQ_MODEL; the shorts repo never did.
CFG_REELS = [(
    'GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")\n',
    CFG_BLOCK,
    "GROQ_MODELS", 1)]

CFG_SHORTS = [(
    'GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")\n',
    'GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")\n\n' + CFG_BLOCK,
    "GROQ_MODELS", 1)]

# Byte-identical in reels core/script.py and shorts core/llm.py.
CALL_EDITS = [(
    '''def _call_groq(system: str, user: str) -> str:
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
''',
    '''def _call_groq(system: str, user: str) -> str:
    """Ask Groq, trying each configured model until one exists.

    One hardcoded model name is how this link died without a sound:
    llama-3.3-70b-versatile was retired on 16 Aug 2026 and every call
    after that returned 404, so the provider chain was gemini-only and
    nothing said so. A list survives the next retirement.

    400 and 404 mean "no such model here" and move to the next name.
    Anything else -- 401 for a bad key, 429 for the rate limit, 5xx for
    an outage -- is not about the model, so it propagates rather than
    being retried pointlessly against three names.
    """
    names = [n.strip() for n in config.GROQ_MODELS.split(",") if n.strip()]
    last = None
    for name in names:
        payload = {
            "model": name,
            "temperature": 0.8,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            data = _post_json(
                "https://api.groq.com/openai/v1/chat/completions",
                payload,
                {"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            )
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404):
                last = exc
                print(f"  ! groq has no model '{name}' ({exc.code}), "
                      f"trying the next")
                continue
            raise
        if name != names[0]:
            print(f"  ! groq answered on '{name}', not '{names[0]}'")
        return data["choices"][0]["message"]["content"]
    raise last or RuntimeError(
        f"no usable Groq model in '{config.GROQ_MODELS}'")
''',
    "no usable Groq model in", 1)]


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
    if "GROQ_MODELS" in src:
        return f"  {path}: already patched"
    anchor = "          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}\n"
    if src.count(anchor) != 1:
        return f"  {path}: anchor appears {src.count(anchor)}x -- NOT TOUCHED"
    new = src.replace(
        anchor,
        anchor + "          # Walked in order; a retired model costs one "
                 "name, not the link.\n"
                 f"          GROQ_MODELS: ${{{{ vars.GROQ_MODELS || '{MODELS}' }}}}\n")
    try:
        import yaml
        doc = yaml.safe_load(new)
        envs = [s.get("env") or {}
                for j in doc["jobs"].values() for s in j["steps"]]
        if not any("GROQ_MODELS" in e for e in envs):
            return f"  {path}: did not land in a step env -- NOT WRITTEN"
    except ImportError:
        pass
    except Exception as exc:
        return f"  {path}: YAML would not parse ({exc}) -- NOT WRITTEN"
    path.write_text(new)
    return f"  {path}: GROQ_MODELS passed through"


def main() -> None:
    root = pathlib.Path(".")
    reels = (root / "core/publish.py").exists()      # reels have Instagram
    call_file = root / ("core/script.py" if reels else "core/llm.py")
    if not call_file.exists():
        sys.exit("run this from a channel repo")

    print(f"groq fallback ({'reels' if reels else 'shorts'}):")
    print(apply(root / "config.py", CFG_REELS if reels else CFG_SHORTS))
    print(apply(call_file, CALL_EDITS))
    print(add_yml(root / ".github/workflows/build.yml"))
    print(f"""
  Groq now tries, in order:
    {MODELS.replace(',', chr(10) + '    ')}

  Check which your key can actually reach:
    curl -s https://api.groq.com/openai/v1/models \\
      -H "Authorization: Bearer $GROQ_API_KEY" \\
      | python3 -c "import json,sys; [print(' ', m['id']) for m in json.load(sys.stdin)['data']]"

  Then reorder without a code change:
    gh variable set GROQ_MODELS --body '<the one that works>' -R sujee119/<repo>
""")


if __name__ == "__main__":
    main()
