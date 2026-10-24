"""Milestone-1 policy: one counter 5 % under each standing price, then the cheapest valid close."""

from ..econ.terms import complete
from .base import Move, Policy


class Trivial(Policy):
    def wants_message(self, t):
        return not t.ours and t.standing[0] is not None and t.status == "open"

    def next_move(self, t, budget):
        _, terms = t.standing
        sheet = complete(self.case, terms, t.std, price=terms["unit_price"] * 0.95)
        return Move("send", t.sid, sheets=[sheet], reason="trivial counter -5%")
