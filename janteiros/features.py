"""Small environment switches for experimental policy layers."""

import os

FALSE = {"0", "false", "no", "off"}


def enabled(name, default=True):
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() not in FALSE
