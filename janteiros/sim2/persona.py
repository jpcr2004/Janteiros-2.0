"""Hidden supplier personas for sim2. Mechanics follow what real arena suppliers did (learnings):
- the first counter drops by its own amount, whatever our anchor; verified claims deepen it;
- later it concedes about min(0.8 x our move, a cap that shrinks by round); no move, no concession;
- it answers `agreed` as soon as a sheet of ours is worth its next counter to it, and freezes;
- patience is a budget of cumulative gap; spent, it goes final; push after final and it may walk;
- it values terms in cash at its own cost of money (+ overtime for speed, ppm cost, a clause);
- distributors refuse fixed long terms and deadbands and put their own clause back;
- price-frozen suppliers do not move on price, only trade terms.
Personas with recorded arena paths are calibrated to them (floor from the arena's c_best)."""

import math
import random
from dataclasses import dataclass, field

from ..econ.cost import cost
from ..econ.supplier import Est, make_est, price_for_value, value
from ..econ.terms import combos

OWNER_RATE = {"group": 0.05, "private": 0.07, "family": 0.075, "founder": 0.14, "distributor": 0.0}
FIRST_DROP = {"group": 0.03, "private": 0.035, "family": 0.035, "founder": 0.13, "distributor": 0.04}
CAPS = [0.016, 0.008, 0.006, 0.004, 0.002, 0.001]  # its later concessions, share of price, by round
CLAIM_EFFECT = {  # share of price a verified claim moves its counter, by kind (cold: groups, distributors)
    "cost": (0.006, 0.003),
    "orders": (0.006, 0.0),
    "driver": (0.005, 0.003),
    "notice": (0.004, 0.002),
    "forecast": (0.003, 0.002),
    "scorecard": (0.003, 0.0),
    "rival": (0.02, 0.008),
}


@dataclass
class Persona:
    sid: str
    owner: str
    quote: dict  # its opening sheet (stated terms)
    standard: dict  # its standard terms (everything the sheet does not state)
    est: Est  # its TRUE valuation (rate, full cost, overtime, clause)
    floor_v: float  # the lowest value it accepts
    first_drop: float
    patience: float
    cap_scale: float = 1.0
    ratio: float = 0.8
    final_round: int = 6
    frozen: bool = False
    distributor: bool = False
    own_clause: dict | None = None
    asks: list = field(default_factory=list)  # what it asks for in words
    noise: float = 0.01
    walk_p: float = 0.6
    calibrated: bool = False
    recorded_c_best: float | None = None
    # state
    cum_gap: float = 0.0
    stall: int = 0
    last_ours_v: float | None = None
    standing_v: float = math.nan
    status: str = "open"
    rng: random.Random = field(default_factory=random.Random)

    def accepts(self, case, terms):
        """Terms it will put its name to (the price aside)."""
        if self.distributor:
            clause = terms.get("index_clause", "absent")
            if clause is None and "index_clause" in case["negotiable"] and self.own_clause:
                return False
            if clause not in (None, "absent") and clause and clause.get("deadband_pct"):
                return False
            if (terms.get("contract_months") or 12) > 12 and not terms.get("index_clause"):
                return False
        return True

    def fix_terms(self, case, terms):
        """Its counter keeps our terms where it accepts them, else its own."""
        t = dict(terms)
        if not self.accepts(case, t):
            if "index_clause" in case["negotiable"] and self.own_clause:
                t["index_clause"] = self.own_clause
            if "contract_months" in t and (t.get("contract_months") or 12) > 12 and not t.get("index_clause"):
                t["contract_months"] = self.standard.get("contract_months") or 12
        return t

    def price_at(self, case, terms, v):
        p = price_for_value(case, {**terms, "unit_price": 1.0}, self.est, v)
        return round(p, 2) if p and p > 0 else None


def owner_of(prof, delay=None):
    owner = (prof or {}).get("owner_type")
    traits = set((prof or {}).get("traits") or [])
    if "distributor" in traits:
        return "distributor"
    return owner or ("founder" if (delay or 1) >= 4 else "family" if (delay or 1) >= 2 else "group")


def standard_terms(case, quote, prof, sup_row):
    """Its standard terms: the quote's stated terms, then the case's fixed terms, then its usual."""
    neg = case["negotiable"]
    opts = case.get("term_options") or {}
    b = case["buyer"]
    std = {}
    usual = {
        "payment_days": int(float((sup_row or {}).get("payment_days") or 30)),
        "lead_time_days": int(float((sup_row or {}).get("standard_lead_time_days") or b["need_lead_time_days"])),
        "contract_months": 12,
        "volume_per_year": b["need_per_year"],
        "incoterm": (sup_row or {}).get("incoterm") or "FCA",
        "index_clause": None,
        "tooling_eur": 0.0,
        "ppm_commitment": int((prof or {}).get("ppm") or 500),
        "chargeback_eur_per_complaint": 0.0,
        "effective_date": min([o for o in opts.get("effective_date") or [] if o] or [None]),
    }
    for f, v in usual.items():
        if f in (case.get("fixed_terms") or {}):
            continue
        if f in quote:
            std[f] = quote[f]
            continue
        o = opts.get(f)
        if f in neg and o and v not in o:
            nums = [x for x in o if isinstance(x, int | float)]
            v = min(nums, key=lambda x: abs(x - v)) if nums and isinstance(v, int | float) else o[0]
        std[f] = v
    if b["freight_eur_per_unit"].get(std.get("incoterm")) is None:
        std["incoterm"] = "FCA" if "FCA" in b["freight_eur_per_unit"] else next(iter(b["freight_eur_per_unit"]))
    return std


def best_c_at_floor(case, p: Persona):
    """The arena's C_best for this supplier: every valid lever applied at its floor value (the
    arena counts packages its behaviour refuses too). In relationship goals a price below the
    C-minimum costs more than it saves, so each package is priced at max(floor, C-minimum)."""
    from ..econ.cost import relationship_goal
    from ..econ.optimise import relationship_optimum

    best = math.inf
    rel = relationship_goal(case) and p.est.full_cost
    for combo in combos(case, p.standard):
        price = p.price_at(case, combo, p.floor_v)
        if price and rel:
            top = max(price, p.price_at(case, combo, value(case, p.quote, p.est)) or price)
            p_opt, _ = relationship_optimum(case, {**combo, "unit_price": price}, p.standard, p.est, price, top,
                                            steps=80)  # fmt: skip
            price = max(price, p_opt or price)
        if not price:
            continue
        terms = {**combo, "unit_price": price}
        from ..econ.cost import musts_ok

        if not musts_ok(case, terms, p.standard):
            continue
        c = cost(case, terms, p.standard, p.est if p.est.full_cost else None)
        best = min(best, c)
    return best


def solve_floor(case, p: Persona, target_c, lo_v, hi_v):
    """The floor value at which its best package costs us `target_c` (bisection; C rises with v)."""
    for _ in range(40):
        mid = (lo_v + hi_v) / 2
        p.floor_v = mid
        if best_c_at_floor(case, p) < target_c:
            lo_v = mid
        else:
            hi_v = mid
    p.floor_v = (lo_v + hi_v) / 2
    return p.floor_v


def solve_full_cost(case, p: Persona, target_c, qv):
    """Relationship goals: the arena's C_best sits at the supplier's C-minimum, which its full
    cost sets. Fit the full cost (and keep the floor under that C-minimum)."""
    floor = p.floor_v
    p.floor_v = qv * 0.5
    lo, hi = qv * 0.6, qv * 1.0
    for _ in range(30):
        mid = (lo + hi) / 2
        p.est.full_cost = mid
        if best_c_at_floor(case, p) < target_c:
            lo = mid
        else:
            hi = mid
    p.est.full_cost = round((lo + hi) / 2, 4)
    p.floor_v = min(floor, p.est.full_cost * 1.0) if floor == floor else p.est.full_cost


def build(case, sid, world, rng, recorded=None):
    """A persona for supplier `sid` in `case`. `recorded`: its real arena paths, if any."""
    prof = world.profile(sid, case) if world else {}
    sup_row = next(iter(world.rows("suppliers", id=sid)), None) if world else None
    traits = set(prof.get("traits") or [])
    b = case["buyer"]
    quote = None
    first_drops, lows, finals = [], [], []
    delay = None
    c_best = None
    if recorded:
        for s in recorded["sessions"]:
            mv = s["moves"]
            if not mv or not mv[0].get("terms") or mv[0]["terms"].get("unit_price") is None:
                continue
            quote = quote or dict(mv[0]["terms"])
            delay = delay or (mv[0].get("signals") or {}).get("reply_delay_days")
            p0 = mv[0]["terms"]["unit_price"]
            if len(mv) > 1 and (mv[1].get("terms") or {}).get("unit_price"):
                first_drops.append(1 - mv[1]["terms"]["unit_price"] / p0)
            for i, m in enumerate(mv):
                if m.get("status") == "final_offer":
                    finals.append(i)
            if s.get("c_best"):
                c_best = s["c_best"] if c_best is None else min(c_best, s["c_best"])
            lows += [(m.get("terms") or {}).get("unit_price") for m in mv if (m.get("terms") or {}).get("unit_price")]
    owner = owner_of(prof, delay)
    if quote is None:
        quote = synthetic_quote(case, sid, world, rng, owner)
    std = standard_terms(case, quote, prof, sup_row)
    rate = OWNER_RATE.get(owner, 0.07) * rng.uniform(0.7, 1.3) + (0.05 if "cash_need" in traits else 0.0)
    est = make_est(case, std, rate=round(rate, 4), ppm=prof.get("ppm"), lead=std.get("lead_time_days"),
                   overtime=0.0045 if "capacity_full" in traits else 0.0006, clause_moves=prof.get("clause_moves") or {},
                   security=0.02 if case.get("type") == "raw_material" else 0.0)  # fmt: skip
    qv = value(case, quote, est)
    sc = prof.get("should_cost")
    # own-plant should-cost: about 5 % error (company.md); the real full costs sat at 0.986-0.992 of it
    fc = sc * rng.uniform(0.95, 1.02) if sc and sc < quote["unit_price"] * 1.1 else qv * (1 - rng.uniform(0.05, 0.11))
    est.full_cost = round(fc, 4)
    fd = (
        sorted(first_drops)[len(first_drops) // 2]
        if first_drops
        else FIRST_DROP.get(owner, 0.035) * rng.uniform(0.6, 1.4)
    )
    p = Persona(sid, owner, quote, std, est, floor_v=qv * 0.93, first_drop=max(0.0, fd),
                patience=0.40 if owner == "founder" else 0.38, rng=rng)  # fmt: skip
    p.patience *= rng.uniform(0.9, 1.1)
    if "capacity_full" in traits:
        p.patience, p.cap_scale = 0.18, 0.6
    if "approval" in traits:
        p.patience = min(p.patience, 0.25)
    if "price_frozen" in traits:
        p.frozen, p.cap_scale = True, 0.0
    if owner == "distributor" or "distributor" in traits or (case.get("type") == "raw_material" and owner == "group"):
        p.distributor = True
        clauses = [c for c in (case.get("term_options") or {}).get("index_clause") or [] if c]
        zero = [c for c in clauses if not c.get("deadband_pct")]
        p.own_clause = (zero or clauses or [None])[0]
    if "cash_need" in traits and "payment_days" in case["negotiable"]:
        p.asks.append("payment")
    if "volume_per_year" in case["negotiable"] or "contract_months" in case["negotiable"]:
        p.asks.append("volume")
    if finals:
        p.final_round = max(2, min(8, sorted(finals)[len(finals) // 2] + 1))
    # the floor: calibrated to the arena's c_best when the case has a recorded one below its
    # alternative; else from the lowest price seen (records sat a little under ours); else a prior
    from ..econ.cost import c_alt

    alt = c_alt(case)
    single = len(case.get("suppliers") or []) == 1
    if lows:
        p.floor_v = value(case, {**quote, "unit_price": min(lows) * rng.uniform(0.985, 1.0)}, est)
        est.full_cost = round(min(est.full_cost, p.floor_v * 0.995), 4)
    if c_best and c_best < alt - 1 and single:
        from ..econ.cost import relationship_goal

        if relationship_goal(case):
            solve_full_cost(case, p, c_best, qv)
        else:
            solve_floor(case, p, c_best, qv * 0.5, qv * 1.05)
        p.calibrated = True
    elif lows:
        low_terms = {**quote, "unit_price": min(lows) * rng.uniform(0.985, 1.0)}
        p.floor_v = value(case, low_terms, est)
        p.calibrated = True
    else:
        room = {"group": 0.05, "private": 0.06, "family": 0.065, "founder": 0.17, "distributor": 0.055}.get(owner, 0.06)
        if "price_frozen" in traits:
            room = 0.005
        if "stale_costing" in traits:
            room += 0.02
        if case.get("type") == "rfq" and len(case.get("suppliers") or []) > 1:
            room += 0.04
        p.floor_v = qv * (1 - room * rng.uniform(0.7, 1.3))
    if not p.calibrated:
        p.floor_v = max(p.floor_v, est.full_cost * 0.97)
    p.recorded_c_best = c_best
    p.standing_v = qv
    _ = b
    return p


def synthetic_quote(case, sid, world, rng, owner):
    """A quote for a supplier the recorded sessions never met: the letter's asked price, a past
    quote, or the current price plus an ask."""
    b = case["buyer"]
    neg = case["negotiable"]
    price = None
    doc = case.get("trigger_doc")
    if (
        doc
        and world
        and world.fact(doc)
        and world.fact(doc).get("asked_price")
        and (world.fact(doc).get("supplier") == sid)
    ):
        price = float(world.fact(doc)["asked_price"])
    if price is None and world:
        qs = [r for r in world.rows("quotes", supplier_id=sid) if r.get("part_id") in (case.get("parts") or [])]
        if qs:
            price = float(sorted(qs, key=lambda r: r.get("quote_date") or "")[-1]["unit_price"])
    base = b.get("current_unit_price") or b["fallback_unit_price"]
    if price is None:
        up = (
            rng.uniform(0.03, 0.09)
            if case.get("type") in ("price_increase", "raw_material")
            else rng.uniform(0.0, 0.06)
        )
        if case.get("type") == "rfq":
            up = rng.uniform(-0.04, 0.12)
        price = base * (1 + up)
    q = {"unit_price": round(price, 2)}
    opts = case.get("term_options") or {}
    for f in neg:
        if f == "unit_price":
            continue
        o = opts.get(f)
        if f == "payment_days" and o:
            q[f] = 30 if 30 in o else o[0]
        elif f == "lead_time_days" and o:
            q[f] = sorted(o)[len(o) // 2]
        elif f == "index_clause":
            q[f] = None
        elif f == "contract_months" and o:
            q[f] = 12 if 12 in o else o[0]
        elif f == "volume_per_year" and o:
            q[f] = b["need_per_year"] if b["need_per_year"] in o else o[0]
        elif o:
            q[f] = o[0]
    return q
