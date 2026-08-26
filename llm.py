"""Ollama backend. Drop-in replacement for the MLX llm.py.

On prompt caching: Ollama's runner keeps the KV cache from the previous
request and matches the longest common prefix of the next one. Because we
put the persona block LAST, two speakers' prompts share everything up to
that tail — so alternating speakers only re-prefills a few hundred tokens.
That's the same benefit the MLX SpeakerCache gave us, for free. Do not
reorder the prompt blocks in play.py.

SpeakerCache is kept as a no-op shim so play.py and cli.py are unchanged.
"""
import json
import os
import urllib.error
import urllib.request

HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.environ.get("IMAGINARIUM_MODEL", "qwen3.8:27b-mlx")

# Ollama defaults to a 4096-token context and SILENTLY TRUNCATES past it.
# A long transcript would quietly lose its head. Set this deliberately.
NUM_CTX = int(os.environ.get("IMAGINARIUM_CTX", "16384"))

# Reasoning models emit a separate `thinking` field. Ollama's `think`
# parameter turns it off at the source, which is what we want: one-line
# generation and strict-JSON creation both break with reasoning on.
# Older builds that reject the parameter fall back to a prompt directive.
NO_THINK = os.environ.get("IMAGINARIUM_NO_THINK", "1") == "1"
_think_param_supported = True


def set_model(name):
    """Override the model for this process. Called by the --model flag."""
    global MODEL
    if name:
        MODEL = name
    return MODEL


def list_models():
    """Return (name, size_bytes) for everything installed."""
    req = urllib.request.Request(f"{HOST}/api/tags")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            tags = json.load(r)
    except urllib.error.URLError as e:
        raise RuntimeError(f"can't reach Ollama at {HOST} — is it running? ({e})")
    return [(m["name"], m.get("size", 0)) for m in tags.get("models", [])]


def load_model(model_name=None):
    """Verify the model exists. Returns (name, None) to match the MLX signature."""
    name = model_name or MODEL
    try:
        req = urllib.request.Request(f"{HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as r:
            tags = json.load(r)
    except urllib.error.URLError as e:
        raise RuntimeError(f"can't reach Ollama at {HOST} — is it running? ({e})")

    have = [m["name"] for m in tags.get("models", [])]
    if name not in have:
        # allow a bare stem only if exactly one tag matches it
        candidates = [h for h in have if h.split(":")[0] == name]
        if len(candidates) == 1:
            return candidates[0], None
        raise RuntimeError(
            f"model '{name}' not found.\ninstalled: {', '.join(have) or '(none)'}\n"
            f"set IMAGINARIUM_MODEL or pass --model."
        )
    return name, None


class SpeakerCache:
    """No-op. Ollama manages its own prefix cache server-side."""

    def prepare(self, key, token_ids):
        return None, token_ids

    def invalidate(self, key=None):
        pass


class OllamaError(RuntimeError):
    pass


def _post_chat(messages, stream, options, _retry=True):
    global _think_param_supported

    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": stream,
        "options": options,
    }
    if NO_THINK and _think_param_supported:
        payload["think"] = False

    req = urllib.request.Request(
        f"{HOST}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        return urllib.request.urlopen(req, timeout=600)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.load(e).get("error", "")
        except Exception:
            pass

        # older builds reject the think parameter; drop it and retry once
        if _retry and "think" in detail.lower() and _think_param_supported:
            _think_param_supported = False
            return _post_chat(messages, stream, options, _retry=False)

        if e.code == 404:
            names = []
            try:
                names = [n for n, _ in list_models()]
            except Exception:
                pass
            raise OllamaError(
                f"Ollama has no model '{MODEL}'."
                + (f"\n  installed: {', '.join(names)}" if names else "")
                + "\n  fix with /model <tag> or --model <tag>"
            ) from None
        raise OllamaError(f"Ollama returned {e.code}: {detail or e.reason}") from None
    except urllib.error.URLError as e:
        raise OllamaError(f"can't reach Ollama at {HOST} — is it running? ({e})") from None


def _messages(prompt_bundle):
    """prompt_bundle is the (system, user) pair produced by chat_wrap."""
    system, user = prompt_bundle
    # only needed on builds that don't support the think parameter
    if NO_THINK and not _think_param_supported:
        system = system + "\n\n/no_think"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _strip_think(text):
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    elif "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.lstrip()


def _visible(raw):
    """Strip reasoning blocks. Returns '' while a block is still open."""
    if "</think>" in raw:
        return raw.split("</think>", 1)[1]
    if "<think>" in raw:
        return ""
    # a partial opening tag may still be arriving
    for i in range(1, len("<think>")):
        if raw.endswith("<think>"[:i]):
            return raw[: -i]
    return raw


def stream_line(prompt_bundle, cache_key, caches, max_tokens=220, temp=0.85,
                stop_on_newline=True):
    """Generate one line, yielding text chunks.

    We do NOT use a server-side newline stop. Models routinely open their
    turn with a newline, and reasoning models emit newlines inside
    <think> blocks — either would end generation before a single visible
    character arrived. So we buffer, strip reasoning, and cut at the first
    newline that follows actual content.
    """
    options = {
        "temperature": temp,
        "top_p": 0.95,
        "num_predict": max_tokens,
        "num_ctx": NUM_CTX,
    }

    raw_buf = ""
    emitted = 0
    debug = os.environ.get("IMAGINARIUM_DEBUG") == "1"

    with _post_chat(_messages(prompt_bundle), True, options) as resp:
        for line in resp:
            if not line.strip():
                continue
            evt = json.loads(line)
            raw_buf += evt.get("message", {}).get("content", "")

            clean = _visible(raw_buf)
            body = clean.lstrip()
            lead = len(clean) - len(body)

            cut = body.find("\n") if stop_on_newline else -1
            if cut != -1:
                final = clean[: lead + cut]
                if len(final) > emitted:
                    yield final[emitted:]
                if debug:
                    print(f"\n[raw: {raw_buf!r}]", flush=True)
                return

            if len(clean) > emitted:
                yield clean[emitted:]
                emitted = len(clean)

            if evt.get("done"):
                break

    if debug:
        print(f"\n[raw: {raw_buf!r}]", flush=True)
    if emitted == 0 and raw_buf.strip():
        # everything got swallowed as reasoning — surface it rather than
        # silently producing nothing
        print(f"\n[model returned only reasoning; set IMAGINARIUM_DEBUG=1 to see it]",
              flush=True)


def complete(prompt_bundle, max_tokens=1600, temp=0.7):
    """One-shot completion. Used for character/location creation."""
    options = {
        "temperature": temp,
        "top_p": 0.95,
        "num_predict": max_tokens,
        "num_ctx": NUM_CTX,
    }
    with _post_chat(_messages(prompt_bundle), False, options) as resp:
        data = json.load(resp)
    return _strip_think(data.get("message", {}).get("content", ""))


def chat_wrap(system, user):
    """Ollama applies the chat template itself, so pass the pair through."""
    return (system, user)
