"""The policy interface the runner drives, and the move it returns. Code decides every number."""

import math
from dataclasses import dataclass, field

from ..econ.cost import c_alt, cost, gate


@dataclass
class Move:
    kind: str  # send | accept | walk
    sid: str | None = None
    sheets: list = field(default_factory=list)  # complete term dicts
    claims: list = field(default_factory=list)  # [{"sentence", "refs", ...}]
    points: list = field(default_factory=list)  # plain sentences code wrote (answers, asks)
    ask: str | None = None  # a question to the supplier
    reason: str = ""  # which rule fired
    keepalive: bool = False
    allow_above_alt: bool = False
    allow_at_standing: bool = False
    tone: str = "warm"


class Policy:
    """Defaults: accept the cheapest standing sheet that passes the gate, else walk."""

    def __init__(self, case, world=None, writer=None):
        self.case, self.world, self.writer = case, world, writer
        self.log = None

    def setup(self, threads, log, resumed=False):
        self.threads, self.log = threads, log

    def est(self, t):
        return None

    def pessimistic_est(self, t):
        return None

    def known_id(self, ref):
        return self.world.known(ref) if self.world else True

    def wants_message(self, t) -> bool:
        return False

    def keep_alive(self, t) -> bool:
        return False

    def priority(self, t):
        """Lower is more promising (who keeps a clock when time is short)."""
        return self.c_of(t, t.standing[1])

    def next_move(self, t, budget):
        return None

    def keepalive_move(self, t):
        return Move("send", t.sid, ask="Could you confirm that your offer stands as sent?", keepalive=True,
                    reason="keepalive", allow_at_standing=True)  # fmt: skip

    def safe_move(self, t, problems):
        return Move("send", t.sid, ask="Could you confirm your current offer and its terms?", reason="safe fallback",
                    allow_at_standing=True)  # fmt: skip

    def observe(self, t, msg, reply, move):
        pass

    def best_standing(self, threads):
        """(C, thread) of the cheapest standing sheet that passes the gate, or None."""
        best = None
        for t in threads.values():
            if not t.live:
                continue
            sid, terms = t.standing
            if not sid:
                continue
            ok, c, _ = gate(self.case, terms, t.std, self.est(t))
            if ok and self.pessimistic_est(t) is not None:
                # the relationship charge rides on a full cost we only estimate: the deal must
                # beat the alternative with a higher one too (a lower margin costs more)
                ok = gate(self.case, terms, t.std, self.pessimistic_est(t))[0]
            if ok and (best is None or c < best[0]):
                best = (c, t)
        return best

    def close_decision(self, threads, final=False):
        best = self.best_standing(threads)
        if best:
            return Move("accept", best[1].sid, reason="cheapest standing sheet passing the gate")
        if final or not any(self.wants_message(t) for t in threads.values() if t.live):
            live = [t for t in threads.values() if t.live]
            return Move("walk", live[0].sid if live else None, reason="nothing passes the gate")
        return None

    def compose(self, t, move, budget):
        from .compose import compose

        return compose(self.case, t, move, self.writer, budget, self.log)

    def c_alt(self):
        return c_alt(self.case)

    def c_of(self, t, terms):
        return cost(self.case, terms, t.std, self.est(t)) if terms else math.inf
