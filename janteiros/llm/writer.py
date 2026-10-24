"""The writer: the one human paragraph of our e-mail. It answers the supplier's points and
acknowledges its situation; code writes every figure, ID, claim and the proposal around it. The
paragraph must hold no digits, no data IDs and nothing that leaks our position; any violation
and the template is used instead."""

import re

from .client import ask_json

SYSTEM = (
    "You write one short paragraph of a purchasing e-mail for Hollerau Pumpenwerke, a German pump "
    "maker, to a supplier. Warm, respectful, concrete, plain business English, 2 to 4 sentences, "
    "at most 90 words. Respond to what the supplier wrote and acknowledge its situation. Do not "
    "make offers, do not state prices, percentages, dates, quantities or any number, do not "
    "mention data, deadlines, urgency, budgets, alternatives, walking away or a target. Do not "
    "promise anything. The proposal and figures follow below your paragraph, written by someone "
    "else; you may refer to them as 'our proposal below'. No greeting and no sign-off."
)
SCHEMA = {
    "type": "object",
    "properties": {"paragraph": {"type": "string"}},
    "required": ["paragraph"],
    "additionalProperties": False,
}
INTENT = {
    "first": "This is our first reply to its quote. We propose terms below.",
    "counter": "We answer its counter-offer with a step of our own below.",
    "close": "We are close and propose terms that should let us settle.",
    "trade": "Its price seems fixed; we propose other terms instead.",
    "keepalive": "We are reviewing its offer and will come back shortly.",
}
DIGIT = re.compile(r"\d")
ID = re.compile(r"\b[a-z]{2,6}:[\w.@#/-]+", re.I)
LEAK = re.compile(
    r"\b(urgent|deadline|asap|budget|walk|alternative|other supplier|competitor|target|limit|maximum|"
    r"final offer|must|cannot accept|ultimatum|lawyer|legal|contract breach|cheapest|%|eur|€|percent)\b",
    re.I,
)


def clean_input(text, n=1500):
    """The supplier's words without figures (a figure in the input invites one in the output)."""
    text = DIGIT.sub("#", text or "")
    text = ID.sub("[ref]", text)
    return text[:n]


def ok(paragraph):
    p = (paragraph or "").strip()
    if not 20 <= len(p) <= 700:
        return False
    if DIGIT.search(p) or ID.search(p) or LEAK.search(p):
        return False
    if any(w in p.lower() for w in ("dear ", "kind regards", "best regards", "sincerely")):
        return False
    return True


class Writer:
    def paragraph(self, case, t, move, budget):
        timeout = min(15.0, budget - 1.0)
        if timeout < 3.0:
            return None
        last = t.last.get("body") or ""
        user = (
            f"Situation: {INTENT.get(move.tone, INTENT['counter'])}\n"
            f"Case: {clean_input(case.get('title'), 200)}\n"
            f"The supplier's last e-mail (figures removed):\n<<<\n{clean_input(last)}\n>>>\n"
            "Write the paragraph."
        )
        out = ask_json("writer", SYSTEM, user, SCHEMA, timeout, max_tokens=1500)
        p = (out or {}).get("paragraph")
        return p.strip() if p and ok(p) else None
