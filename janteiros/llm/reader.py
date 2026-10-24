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
