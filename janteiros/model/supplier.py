"""The online supplier model: per thread, from its messages only (so a resume rebuilds it exactly).
Everything is measured in the supplier's own value per unit (`econ.supplier.value`: the price at
its standard terms that pays it the same), since that is what it compares: its standing value,
our offers' value, its concessions, their ratio to our moves, patience used (cumulative gap), an
extrapolated floor, and a status."""

import math
import statistics
from dataclasses import dataclass, field

from ..econ.cost import cost
from ..econ.supplier import value


@dataclass
class View:
    quote_v: float = math.nan
    quote_c: float = math.inf
    quote_price: float | None = None
    theirs: list = field(default_factory=list)  # its standing value after each of its e-mails
    theirs_c: list = field(default_factory=list)  # our C of its standing sheet
    prices: list = field(default_factory=list)
    ours: list = field(default_factory=list)  # max value of our sheets per message
    ours_c: list = field(default_factory=list)  # min C of our sheets per message
    concessions: list = field(default_factory=list)  # its moves in value (positive = toward us)
    our_moves: list = field(default_factory=list)
    ratios: list = field(default_factory=list)
    cum_gap: float = 0.0  # sum of relative gaps (share of price) at each of our sheets
    status: str = "open"
    floor_v: float = math.nan
    first_drop: float = 0.0  # its first concession, share of its quote price

    @property
    def price(self):
        return (self.prices[-1] if self.prices else self.quote_price) or 1.0

    @property
    def standing_v(self):
        return self.theirs[-1] if self.theirs else math.nan

    @property
    def standing_c(self):
        return self.theirs_c[-1] if self.theirs_c else math.inf

    @property
    def last_ours(self):
        return self.ours[-1] if self.ours else None

    def rel(self, dv):
        return dv / self.price if self.price else 0.0

    @property
    def ratio(self):
        good = [r for r in self.ratios if r is not None]
        return statistics.median(good[-3:]) if good else None

    @property
    def frozen(self):
        """It answered our last two moves with (almost) nothing."""
        c = self.concessions[-2:]
        return len(c) >= 2 and all(self.rel(x) < 0.0005 for x in c)


def view(case, t, est, cost_est=None):
    v = View()
    std = t.std
    for m in t.messages:
        if m.get("from") == "buyer":
            sheets = [s.get("terms") for s in m.get("term_sheets") or [] if s.get("terms")]
            if not sheets:
                continue
            vals = [value(case, s, est) for s in sheets]
            vals = [x for x in vals if x == x]
            if not vals:
                continue
            ov = max(vals)
            if v.theirs:
                v.cum_gap += max(0.0, v.rel(v.standing_v - ov))
            if v.ours:
                v.our_moves.append(ov - v.ours[-1])
            v.ours.append(ov)
            v.ours_c.append(min(cost(case, s, std, cost_est) for s in sheets))
            continue
        sheets = m.get("term_sheets") or []
        if not sheets:
            continue
        terms = sheets[0].get("terms") or {}
        if terms.get("unit_price") is None:
            continue
        sv = value(case, terms, est)
        if sv != sv:
            continue
        if not v.theirs:
            v.quote_v, v.quote_price = sv, terms["unit_price"]
            v.quote_c = cost(case, terms, std, cost_est)
        else:
            dv = v.theirs[-1] - sv
            v.concessions.append(dv)
            # it answers our latest sheet; our move is that sheet against the one before
            mv = v.our_moves[-1] if len(v.ours) >= 2 else None
            v.ratios.append(dv / mv if mv and mv > 0 else None)
            if len(v.concessions) == 1 and v.quote_price:
                v.first_drop = dv / v.quote_price
        v.theirs.append(sv)
        v.theirs_c.append(cost(case, terms, std, cost_est))
        v.prices.append(terms["unit_price"])
        v.status = m.get("status") or "open"
    v.floor_v = floor(v)
    return v


FIRST_CAP = 0.016  # its first capped concession after the first counter, share of price (D01)


def next_concession(v: View, prior_ratio=0.5, prior_room=0.06):
    """Its next concession in value, decaying like its last ones. The first counter is its own
    (independent of our anchor), so only the concessions after it predict the next."""
    if not v.concessions:
        return prior_room * 0.4 * v.price
    d = [max(0.0, x) for x in v.concessions[1:]]
    if not d:
        return FIRST_CAP * v.price
    if len(d) >= 2 and d[-2] > 0:
        r = min(1.0, d[-1] / d[-2])
    else:
        r = prior_ratio
    return d[-1] * r


def floor(v: View, prior_ratio=0.5, prior_room=0.06):
    """Where its standing value is heading: geometric extrapolation of its concessions."""
    if not v.theirs:
        return math.nan
    if v.status in ("agreed", "final_offer"):
        return v.standing_v
    if not v.concessions:
        return v.standing_v - prior_room * v.price
    d = [max(0.0, x) for x in v.concessions]
    r = min(0.85, d[-1] / d[-2]) if len(d) >= 2 and d[-2] > 0 else prior_ratio
    remaining = d[-1] * r / (1 - r)
    return v.standing_v - remaining
