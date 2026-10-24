"""Switches for the changes made after baseline-r4 (= main 57683be). Each one is on by default and
can be turned off alone with JT_X_<NAME>=0; JT_X_ALL=0 turns all of them off, which gives exactly
the baseline behaviour (tests/test_golden.py checks that on every recorded real opening)."""

import os

NAMES = ("WRITER_GUARD", "HONEST_ANSWERS", "READER_NO_ECON", "FORECAST", "TWINS")


def on(name):
    assert name in NAMES, name
    v = os.environ.get(f"JT_X_{name}", os.environ.get("JT_X_ALL"))
    return v is None or v.strip().lower() not in ("0", "false", "no", "off", "")


def state():
    return {n: on(n) for n in NAMES}
