"""Claude via the official SDK. Every call is optional: no key, JT_NO_LLM, a timeout, a refusal or
an invalid answer all return None and the caller falls back to code. No SDK retries: the 60 s
clock is ours to keep."""

import json
import os
import threading
import time

STATS_LOCK = threading.Lock()
STATS: dict = {}  # role -> {"calls", "ok", "fallback", "seconds"}


def enabled():
    return not os.environ.get("JT_NO_LLM") and bool(os.environ.get("LLM_API_KEY"))


_client = None
_client_lock = threading.Lock()


def client():
    global _client
    with _client_lock:
        if _client is None:
            import anthropic

            # only our own key: an ANTHROPIC_API_KEY in the shell may belong to something else
            key = os.environ.get("LLM_API_KEY")
            _client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=30.0)
        return _client


def model():
    return os.environ.get("LLM_MODEL") or "claude-opus-5-5"


def effort():
    e = (os.environ.get("LLM_EFFORT") or "low").lower()
    return e if e in ("low", "medium", "high", "xhigh", "max") else "low"


def record(role, ok, seconds):
    with STATS_LOCK:
        s = STATS.setdefault(role, {"calls": 0, "ok": 0, "fallback": 0, "seconds": 0.0})
        s["calls"] += 1
        s["ok" if ok else "fallback"] += 1
        s["seconds"] = round(s["seconds"] + seconds, 2)


def ask_json(role, system, user, schema, timeout, max_tokens=1200):
    """One structured call; the parsed JSON object, or None (timeout, refusal, bad JSON)."""
    if not enabled() or timeout < 1.5:
        return None
    t0 = time.monotonic()
    out = None
    try:
        import anthropic

        r = (
            client()
            .with_options(timeout=timeout, max_retries=0)
            .messages.create(
                model=model(),
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": effort(), "format": {"type": "json_schema", "schema": schema}},
            )
        )
        if r.stop_reason not in ("refusal", "max_tokens"):
            text = next((b.text for b in r.content if b.type == "text"), None)
            if text:
                out = json.loads(text)
                if not isinstance(out, dict):
                    out = None
    except (anthropic.APIError, ValueError, TypeError, KeyError):
        out = None
    except Exception:  # noqa: BLE001 - never let the model cost a move
        out = None
    record(role, out is not None, time.monotonic() - t0)
    return out


def writer_from_env():
    if not enabled():
        return None
    from .writer import Writer

    return Writer()


def reader_from_env():
    if not enabled():
        return None
    from .reader import Reader

    return Reader()
