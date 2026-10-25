"""Switches for the changes made after baseline-r4 (= main 57683be). Each one is on by default and
can be turned off alone with JT_X_<NAME>=0; JT_X_ALL=0 turns all of them off, which gives exactly
the baseline behaviour (tests/test_golden.py checks that on every recorded real opening)."""

import os

NAMES = (
    "WRITER_GUARD",
    "HONEST_ANSWERS",
    "READER_NO_ECON",
    "FORECAST",
    "TWINS",
    "PROBE",
    "GENERIC",
    "ROBUST_FC",
    "RESCUE",
    "MIRROR",
    "HEDGE_CLAIMS",
    "VERBAL_OFFERS",
)


DEFAULT_OFF = (
    "GENERIC",
    "ROBUST_FC",
    "PROBE",
    "HEDGE_CLAIMS",  # real P08: index arguments first 0.670, last 0.515 (25 Oct, same sheets)
)  # PROBE: real P18 0.677 -> 0.610 (25 Oct T1), no gain seen  # experiments measured worse offline: on only when asked for by name


def on(name):
    assert name in NAMES, name
    v = os.environ.get(f"JT_X_{name}")
    if v is None and name in DEFAULT_OFF:
        return False
    if v is None:
        v = os.environ.get("JT_X_ALL")
    return v is None or v.strip().lower() not in ("0", "false", "no", "off", "")


def state():
    return {n: on(n) for n in NAMES}
