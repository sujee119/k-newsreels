#!/usr/bin/env python3
"""Port the provider chain to the reels channels.

On 20 Oct Gemini returned 503 and both reels channels went dark for
twelve hours. The log shows why it was fatal rather than annoying:

    [3/7] writing script and shot list
      ! attempt 1 failed: HTTP Error 503: Service Unavailable
      ! attempt 2 failed: HTTP Error 503: Service Unavailable
      ! attempt 3 failed: The read operation timed out
      ! attempt 4 failed: The read operation timed out
      ! all attempts failed -- using fallback
      ! ...not worth posting

Four attempts, all against the same dead service, because
write_script() picks ONE provider:

    fn = PROVIDERS[provider]

while groq and ollama sit in PROVIDERS unused. Narration has never had
this problem -- TTS_PROVIDERS is "gemini,azure,edge,gtts,espeak" and
speak() walks it, which is why a dead voice service has never cost a
reel.

Three changes, matching what the shorts repo already has:

  * _call_gemini retries 429 and 5xx. It had NEITHER here -- only 404
    model-not-found -- so a 503 raised on the first call every time.
  * call() walks config.SCRIPT_PROVIDERS, defaulting to
    "<SCRIPT_PROVIDER>,groq,ollama".
  * write_script uses the chain. Because call() has a provider's
    signature, the fn(system, user) below it is untouched.

AllProvidersFailed subclasses OSError so the existing

    except (OSError, json.JSONDecodeError, KeyError) as exc:

in the attempt loop catches it: the chain is retried across all four
attempts, and only if every provider is down on every attempt does it
reach _fallback(). SCRIPT_PROVIDER=none still bypasses the LLM.

GROQ_API_KEY is already passed to the build step in both repos.

    python3 tools/fix_chain_reels.py
"""
import ast
import pathlib
import sys

CONFIG_EDITS = [(
    'SCRIPT_PROVIDER = os.getenv("SCRIPT_PROVIDER", "gemini")\n',
    '''SCRIPT_PROVIDER = os.getenv("SCRIPT_PROVIDER", "gemini")
# A CHAIN, like TTS_PROVIDERS. Asking a single provider is what took
# this channel down for twelve hours on 20 Oct when Gemini returned
# 503; narration rode it out because it has always had a chain.
# SCRIPT_PROVIDER still leads, so nothing existing changes. ollama
# costs nothing to leave in: on a runner it refuses instantly, and on
# a machine running Ollama it simply works.
SCRIPT_PROVIDERS = (os.getenv("SCRIPT_PROVIDERS")
                    or f"{SCRIPT_PROVIDER or 'gemini'},groq,ollama")
''',
    "SCRIPT_PROVIDERS", 1)]

SCRIPT_EDITS = [
    # 1. a 503 must not end the attempt on its first call
    ('''    last = None
    for model in models[:6]:
        try:
            return _gemini_once(model, system, user)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            last = exc
            print(f"  ! model '{model}' not available, trying next")
            if model in _GEMINI_MODEL_CACHE:
                _GEMINI_MODEL_CACHE.remove(model)
    raise last or RuntimeError("no usable Gemini model")
''',
     '''    last = None
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
''',
     "its end not ours", 1),

    # 2. the chain itself
    ('''PROVIDERS = {"gemini": _call_gemini, "groq": _call_groq, "ollama": _call_ollama}
''',
     '''PROVIDERS = {"gemini": _call_gemini, "groq": _call_groq, "ollama": _call_ollama}


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
        "no script provider answered:\\n  " + "\\n  ".join(problems))
''',
     "class AllProvidersFailed", 1),

    # 3. use it
    ('''    if provider == "none" or provider not in PROVIDERS:
        print("  i no LLM provider configured -- using fallback script")
        return _fallback(title, summary)

    fn = PROVIDERS[provider]
''',
     '''    if provider == "none":
        print("  i no LLM provider configured -- using fallback script")
        return _fallback(title, summary)

    # The whole chain, not one provider. The four attempts below used
    # to hit the same endpoint four times; now each attempt can fall
    # through to groq and then ollama.
    fn = call
''',
     "fn = call", 1),
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


def main() -> None:
    root = pathlib.Path(".")
    if not (root / "core/publish.py").exists():
        sys.exit("this is for the two reels repos "
                 "(scidaily-reels, k-newsreels) -- the shorts repo has its "
                 "own chain already")
    print("a provider chain for the script step:")
    print(apply(root / "config.py", CONFIG_EDITS))
    print(apply(root / "core/script.py", SCRIPT_EDITS))


if __name__ == "__main__":
    main()
