"""Claude via the official SDK. Every call is optional: no key, JT_NO_LLM, a timeout, a refusal or
an invalid answer all return None and the caller falls back to code."""

import os


def enabled():
    return not os.environ.get("JT_NO_LLM") and bool(
        os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    )


def writer_from_env():
    if not enabled():
        return None
    try:
        from .writer import Writer

        return Writer.from_env()
    except Exception:  # noqa: BLE001
        return None
