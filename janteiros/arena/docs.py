"""Supplier attachments to text, and quote / order PDFs to terms. Every line is parsed on its own:
a line we cannot read is skipped, never fatal (a crash here once dropped a supplier at the close)."""

import re

from arena_kit.docs import LABELS, SECTIONS
from arena_kit.docs import read_document as _kit_read

CLAUSE = re.compile(r"(\S+),\s*(\d+(?:\.\d+)?)\s*%\s*material,\s*deadband\s*([\d.]+)\s*%,\s*base\s*(\S+)", re.I)
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
FLOAT_FIELDS = {"unit_price", "tooling_eur", "chargeback_eur_per_complaint"}
TEXT_FIELDS = {"effective_date", "incoterm"}
NONE_WORDS = {"none", "null", "-", "n/a", "no", "fixed", "no index clause"}


def read_document(name: str, data: bytes) -> str:
    try:
        return _kit_read(name or "", data) or ""
    except Exception:  # noqa: BLE001 - an unreadable file is empty text, not a crash
        try:
            return data.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return ""


def parse_value(field: str, text: str):
    """(ok, value) for one labelled line; ok=False when the line cannot be read."""
    text = text.strip()
    low = text.lower()
    if field == "index_clause":
        if low in NONE_WORDS or low.startswith("none") or low.startswith("no "):
            return True, None
        m = CLAUSE.search(text)
        if not m:
            return False, None
        series, share, deadband, base = m.groups()
        return True, {
            "series": series,
            "material_share": round(float(share) / 100, 4),
            "deadband_pct": float(deadband),
            "base_month": base,
        }
    if low in NONE_WORDS:
        return True, None
    if field in TEXT_FIELDS:
        return True, text.split()[0] if text else None
    m = NUMBER.search(text.replace(",", ""))
    if not m:
        return False, None
    v = float(m.group())
    return True, (v if field in FLOAT_FIELDS else int(round(v)))


def quote_terms(text: str) -> dict:
    """{section: {field: value}} for sections stated / standard / fixed / agreed."""
    out: dict = {}
    section = None
    labels = sorted(LABELS, key=len, reverse=True)
    for raw in (text or "").splitlines():
        line = raw.strip()
        for head, key in SECTIONS.items():
            if line.startswith(head):
                section = key
                break
        label = next((lab for lab in labels if line.startswith(lab + " ") or line == lab), None)
        if not (section and label):
            continue
        ok, value = parse_value(LABELS[label], line[len(label) :])
        if ok:
            out.setdefault(section, {})[LABELS[label]] = value
    return out


def standard_terms(messages: list) -> dict:
    """The supplier's standard terms from the latest quote PDF in a thread (empty if none)."""
    for msg in reversed(messages):
        if msg.get("from") == "buyer":
            continue
        for att in msg.get("attachments") or []:
            name = (att.get("name") or "").lower()
            if name.startswith("quote") and att.get("text"):
                std = quote_terms(att["text"]).get("standard")
                if std is not None:
                    return std
    return {}
