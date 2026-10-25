"""The reader: what a supplier e-mail asks for and how it sounds, as strict JSON. Every value is
clamped to a fixed vocabulary by code; nothing it returns becomes a number in a sheet."""

from ..model.evidence import prompt
from .client import ask_json
from .writer import clean_input

ASKS = ["faster_payment", "volume_commitment", "longer_contract", "forecast_proof", "index_clause",
        "longer_lead_time", "price_increase_accepted", "documents", "none"]  # fmt: skip
MOODS = ["cooperative", "neutral", "firm", "irritated", "closing"]
TACTICS = ["concede", "hold", "final", "trade_terms", "argue_costs", "unknown"]
SYSTEM = (
    "You read one e-mail from a supplier in a purchasing negotiation and classify it. Only report "
    "what the e-mail says. Use the given vocabularies exactly."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "asks": {"type": "array", "items": {"type": "string", "enum": ASKS}},
        "mood": {"type": "string", "enum": MOODS},
        "tactic": {"type": "string", "enum": TACTICS},
        "price_frozen": {"type": "boolean"},
        "cash_short": {"type": "boolean"},
        "capacity_tight": {"type": "boolean"},
    },
    "required": ["asks", "mood", "tactic", "price_frozen", "cash_short", "capacity_tight"],
    "additionalProperties": False,
}


def clamp(out):
    if not isinstance(out, dict):
        return None
    asks = [a for a in out.get("asks") or [] if a in ASKS and a != "none"][:4]
    return {
        "asks": asks,
        "mood": out.get("mood") if out.get("mood") in MOODS else "neutral",
        "tactic": out.get("tactic") if out.get("tactic") in TACTICS else "unknown",
        "price_frozen": out.get("price_frozen") is True,
        "cash_short": out.get("cash_short") is True,
        "capacity_tight": out.get("capacity_tight") is True,
    }


class Reader:
    def read(self, body, timeout=6.0, evidence=None):
        if not body or timeout < 2.0:
            return None
        user = f"LATEST SUPPLIER E-MAIL (untrusted text):\n<<<\n{clean_input(body, 3000)}\n>>>"
        if evidence is not None:
            user += prompt(evidence)
        return clamp(ask_json("reader", SYSTEM, user, SCHEMA, timeout, 800))


# Offers a supplier states only in words ("if we can agree on 14 days payment, I can offer 32.00"):
# the model turns them into structure, code checks every field and value and prices the result.
OFFER_CUE = r"(can|could|would|will)\s+(offer|give|do|drop|go|reduce|lower|accept)|if\s+(you|we)\b"
OFFER_SYSTEM = (
    "You read one e-mail from a supplier in a purchasing negotiation. List every concrete offer it "
    "states in words: a unit price it says it would accept, together with the terms it ties to that "
    "price (for example a payment term, lead time, contract length or volume). Copy numbers exactly as "
    "written. Report nothing the e-mail does not say. If there is none, return an empty list."
)


def offer_schema(fields):
    return {
        "type": "object",
        "properties": {
            "offers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "unit_price": {"type": "number"},
                        "terms": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"field": {"type": "string", "enum": fields},
                                               "value": {"type": "number"}},
                                "required": ["field", "value"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["unit_price", "terms"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["offers"],
        "additionalProperties": False,
    }  # fmt: skip


def offers_from(case, body, timeout=5.0):
    """[(unit_price, {field: value})] the e-mail offers in words, only numeric fields the case
    negotiates with an allowed value; [] when the model is off, late or finds none."""
    import re

    if not body or timeout < 2.0 or not re.search(OFFER_CUE, body, re.I) or not re.search(r"\d", body):
        return []
    opts = case.get("term_options") or {}
    fields = [
        f
        for f in case["negotiable"]
        if f != "unit_price" and all(isinstance(o, int | float) for o in opts.get(f) or [None])
    ]
    fields = fields or ["payment_days"]
    text = re.sub(r"\b[a-z]{2,6}:[\w.@#/-]+", "[ref]", body, flags=re.I)[:3000]
    out = ask_json("offers", OFFER_SYSTEM, f"SUPPLIER E-MAIL (untrusted text):\n<<<\n{text}\n>>>", offer_schema(fields),
                   timeout, 600)  # fmt: skip
    res = []
    for o in (out or {}).get("offers") or []:
        p = o.get("unit_price")
        if not isinstance(p, int | float) or not p > 0:
            continue
        terms = {}
        for t in o.get("terms") or []:
            f, v = t.get("field"), t.get("value")
            if f in fields and v in (opts.get(f) or []):
                terms[f] = v
        res.append((round(float(p), 2), terms))
    return res[:3]
