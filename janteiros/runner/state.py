"""What we know about one supplier thread, rebuilt from its messages (so a resume is exact)."""

import time
from dataclasses import dataclass, field

from ..arena.docs import standard_terms

ENDED = {"walked_away", "no_bid"}
CLOCK_S = 60.0


@dataclass
class Thread:
    sid: str
    messages: list = field(default_factory=list)
    replied_at: float = field(default_factory=time.monotonic)  # our clock starts at its last reply
    closed: str | None = None
    timeouts: int = 0
    pruned: bool = False  # we stopped negotiating here (another bidder is better)
    rejected: int = 0  # 422s on our messages

    @property
    def last(self) -> dict:
        return next((m for m in reversed(self.messages) if m.get("from") != "buyer"), {})

    @property
    def status(self) -> str:
        return self.last.get("status") or "open"

    @property
    def standing(self):
        """(term_sheet_id, terms) of the supplier's standing sheet, or (None, {})."""
        for m in reversed(self.messages):
            if m.get("from") == "buyer":
                continue
            sheets = m.get("term_sheets") or []
            if sheets and sheets[0].get("term_sheet_id"):
                return sheets[0]["term_sheet_id"], dict(sheets[0].get("terms") or {})
            if m.get("status") in ENDED:
                return None, {}
        return None, {}

    @property
    def std(self) -> dict:
        return standard_terms(self.messages)

    @property
    def rounds_used(self) -> int:
        return sum(m.get("from") == "buyer" for m in self.messages)

    def moves_left(self, case) -> int:
        return case.get("round_limit", 10) - self.rounds_used - self.timeouts

    @property
    def live(self) -> bool:
        return not self.closed and self.status not in ENDED

    def age(self, now) -> float:
        return now - self.replied_at

    @property
    def ours(self) -> list:
        return [m for m in self.messages if m.get("from") == "buyer"]

    @property
    def theirs(self) -> list:
        return [m for m in self.messages if m.get("from") != "buyer"]
