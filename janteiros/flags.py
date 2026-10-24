"""Switches for the changes made after baseline-r4 (= main 57683be). Each one is on by default (PROBE
is off) and can be turned off alone with JT_X_<NAME>=0; JT_X_ALL=0 turns all of them off, which gives exactly
the baseline behaviour (tests/test_golden.py checks that on every recorded real opening)."""

import os

NAMES = ("WRITER_GUARD", "HONEST_ANSWERS", "READER_NO_ECON", "FORECAST", "TWINS", "SHEET_CHECK", "PROBE")
# off unless asked for: PROBE cost P18 0.677 -> 0.610 on the real arena (fba9c63): the distributor
# countered on the probe's higher price and anchored there
OFF_BY_DEFAULT = {"PROBE"}


def on(name):
    assert name in NAMES, name
    v = os.environ.get(f"JT_X_{name}", os.environ.get("JT_X_ALL"))
    if v is None:
        return name not in OFF_BY_DEFAULT
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def state():
    return {n: on(n) for n in NAMES}
