"""sim3: out-of-distribution suppliers, an independent judge for robustness (not for tactics).

sim2's personas share the agent's value model and were calibrated on the same real paths the
rules came from, so agent and sim can agree because they are wrong alike. sim3 shares none of that:
- each supplier draws hidden economics from wide ranges (room 2-25 %, cost of money 0-40 %/year,
  overtime, security, volume and clause values of either sign, its real ppm) and values a sheet with
  the arena's own formula (`arena_kit.cost.supplier_value`) plus those hidden extras;
- each supplier follows one behaviour family drawn per seed: reciprocal, Boulware or conceder
  (a calendar of its own, whatever we do), frozen, impatient, anchor punisher, hold punisher,
  package seeker, noisy; some ignore claims, some always counter on their own terms;
- quotes come from the data package only (no recorded session of any event);
- C_best follows the arena's definition for this supplier: the cheapest package it would accept
  (its floor), any allowed terms, against the alternative.

    uv run jt eval --suite sim3 --no-llm        (in-process, frozen clock)
"""

import copy
import itertools
import math
import random
from dataclasses import dataclass, field

from arena_kit.cost import supplier_value, total_cost
from arena_kit.sim import create_app

from .econ.cost import c_alt, closing, musts_ok
from .sim2 import judge
from .sim2.server import Sim2, base_cases, load_world, score, variants

FAMILIES = ("reciprocal", "boulware", "conceder", "frozen", "impatient", "anchor_punisher", "hold_punisher",
            "package_seeker", "noisy")  # fmt: skip


@dataclass
class Hidden:
    family: str
    standard: dict
    quote: dict
    rate: float
    ppm: float
    overtime: float  # share of price per day faster than its standard lead time
    security: float  # share of price per 12 months beyond its standard contract
    volume: float  # share of price per +10 % volume
    clause: float  # share of price an index clause is worth to it (times the material share)
    room: float
    first_share: float  # its first counter as a share of its room
    ratio: float
    cap: float
    decay: float
    patience: float
    final_round: int
    walk_p: float
    claim_bonus: float
    stubborn: bool  # counters on its own standard terms
    noise: float
    full_cost_share: float  # relationship goals: full cost as a share of its floor price
    floor_u: float = math.nan
    standing_u: float = math.nan
    last_ours_u: float | None = None
    last_terms: dict | None = None
    cum_gap: float = 0.0
    stall: int = 0
    status: str = "open"
    rng: random.Random = field(default_factory=random.Random)


def draw(rng, quote, standard):
    fam = rng.choice(FAMILIES)
    rate = rng.choice([rng.uniform(0.0, 0.02), rng.uniform(0.03, 0.09), rng.uniform(0.1, 0.4)])
    h = Hidden(
        family=fam, standard=dict(standard), quote=dict(quote), rate=rate,
        ppm=float(standard.get("ppm_commitment") or 500) * rng.uniform(0.3, 1.5),
        overtime=rng.uniform(0.0, 0.008), security=rng.uniform(-0.01, 0.03), volume=rng.uniform(-0.02, 0.03),
        clause=rng.uniform(-0.02, 0.08), room=rng.uniform(0.02, 0.25), first_share=rng.uniform(0.0, 0.6),
        ratio=rng.uniform(0.2, 1.0), cap=rng.uniform(0.005, 0.03), decay=rng.uniform(0.4, 0.9),
        patience=rng.uniform(0.08, 0.6), final_round=rng.randint(3, 9), walk_p=rng.uniform(0.0, 0.8),
        claim_bonus=0.0 if rng.random() < 0.3 else rng.uniform(0.0, 0.01), stubborn=rng.random() < 0.3,
        noise=rng.uniform(0.0, 0.01), full_cost_share=rng.uniform(0.96, 1.0), rng=rng,
    )  # fmt: skip
    if fam == "impatient":
        h.patience, h.walk_p = rng.uniform(0.03, 0.1), max(h.walk_p, 0.5)
    if fam == "noisy":
        h.noise = rng.uniform(0.02, 0.04)
    if fam == "frozen":
        h.room = rng.uniform(0.0, 0.01)
    return h


def utility(case, terms, h):
    """What a sheet is worth to this supplier per unit: the arena's cash value at its hidden cost of
    money and real ppm, plus its hidden taste for speed, security, volume and clauses."""
    std = {**h.standard, **(case.get("fixed_terms") or {})}
    t = closing(case, terms, h.standard)
    price = t.get("unit_price") or 0.0
    try:
        u = supplier_value(case, t, {"standard": h.standard, "interest_rate": h.rate, "ppm": h.ppm})
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return math.nan
    if t.get("lead_time_days") is not None and std.get("lead_time_days"):
        u -= h.overtime * price * max(0.0, std["lead_time_days"] - t["lead_time_days"])
    if t.get("contract_months") and std.get("contract_months"):
        u += h.security * price * (t["contract_months"] - std["contract_months"]) / 12
    if t.get("volume_per_year") and std.get("volume_per_year"):
        u += h.volume * price * (t["volume_per_year"] / std["volume_per_year"] - 1) * 10
    clause = t.get("index_clause")
    if clause:
        u += (
            h.clause
            * price
            * (clause.get("material_share") or 0)
            * max(0.0, 1 - (clause.get("deadband_pct") or 0) / 10)
        )
    return u


def price_at(case, terms, h, target_u):
    """The unit price at which `terms` are worth `target_u` to it (utility is linear in price)."""
    a = utility(case, {**terms, "unit_price": 1.0}, h)
    b = utility(case, {**terms, "unit_price": 2.0}, h)
    slope = b - a
    if not (slope > 1e-9) or a != a:
        return None
    p = 1.0 + (target_u - a) / slope
    return round(p, 2) if p > 0.01 else None


def supplier_arg(case, h):
    """The supplier as the arena's cost function sees it (its full cost sets the relationship charge)."""
    floor_p = price_at(case, h.quote, h, h.floor_u) or h.quote["unit_price"]
    return {"standard": h.standard, "interest_rate": h.rate, "ppm": h.ppm, "full_cost": floor_p * h.full_cost_share}


def c_of(case, terms, h):
    try:
        return total_cost(case, closing(case, terms, h.standard), h.standard, supplier_arg(case, h))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return math.inf


def best_c(case, h):
    """The cheapest package it would ever accept: every allowed term combination at its floor (and,
    with a relationship charge, any price above the floor that costs us less)."""
    neg = [f for f in case["negotiable"] if f != "unit_price"]
    opts = [(case.get("term_options") or {}).get(f) or [h.standard.get(f)] for f in neg]
    best = math.inf
    for combo in itertools.islice(itertools.product(*opts), 4000):
        terms = {**h.quote, **dict(zip(neg, combo, strict=True))}
        p0 = price_at(case, terms, h, h.floor_u)
        if not p0 or not musts_ok(case, {**terms, "unit_price": p0}, h.standard):
            continue
        for k in range(12):
            c = c_of(case, {**terms, "unit_price": round(p0 * (1 + 0.01 * k), 2)}, h)
            best = min(best, c)
    return best


class Sim3(Sim2):
    def __init__(self, cases, facts, world, seed=1, clock=None, no_clock=False):
        super().__init__(cases, facts, world, seed=seed, recorded={}, clock=clock, no_clock=no_clock)
        self.hidden = {}

    def open(self, team, case_id):
        r = super().open(team, case_id)
        s = self.sessions[r["session_id"]]
        case = s["case"]
        for sup, th in s["threads"].items():
            rng = random.Random(f"sim3|{self.seed}|{case['case_id']}|{sup}")
            h = draw(rng, th["stated"], th["standard"])
            qu = utility(case, h.quote, h)
            h.floor_u, h.standing_u = qu * (1 - h.room), qu
            self.hidden[(s["id"], sup)] = h
            th["messages"][0]["body"] = "Dear Sir or Madam,\n\nplease find our quote attached.\n\nKind regards\nSales"
        return r

    def _answer(self, s, th, msg, sheets):
        case, sup = s["case"], f"sup:{th['key']}"
        h = self.hidden[(s["id"], sup)]
        price = th["stated"]["unit_price"]
        facts = {**self.facts, **(case.get("facts") or {})}
        offers = {k: {"unit_price": v.get("unit_price")} for k, v in s["offers"].items()}
        results = judge.check(msg.get("body", ""), msg.get("refs", []), facts, offers, sup, s["used"])
        out = [{"index": r["index"], "result": r["result"]} for r in results]
        verified = sum(r["result"] == "verified" for r in results)
        contradicted = sum(r["result"] == "contradicted" for r in results)
        h.cum_gap += 0.05 * contradicted
        if not sheets:
            return self.reply(s, th, h, th["status"], th["stated"], out, "Our offer stands.")
        scored = [(utility(case, x, h), x) for x in sheets]
        scored = [(u, x) for u, x in scored if u == u]
        if not scored:
            return self.reply(s, th, h, th["status"], th["stated"], out, "We cannot read your terms.")
        u_best, best = max(scored, key=lambda z: z[0])
        rnd = th["round"]
        if h.status == "final_offer":
            if u_best >= h.standing_u - 1e-6:
                return self.agree(s, th, h, best, u_best, out)
            if h.rng.random() < h.walk_p:
                th["closed"] = "walked_away"
                return self.reply(s, th, h, "walked_away", th["stated"], out, "We withdraw our offer.")
            return self.reply(s, th, h, "final_offer", th["stated"], out, "Our final offer stands.", ["quote.pdf"])
        move = None if h.last_ours_u is None else u_best - h.last_ours_u
        quote_u = utility(case, h.quote, h)
        span = max(1e-9, quote_u - h.floor_u)
        f = h.family
        if f in ("boulware", "conceder"):
            beta = 0.3 if f == "boulware" else 2.5
            target = h.floor_u + span * (1 - min(1.0, rnd / h.final_round) ** (1 / beta))
            conc = max(0.0, h.standing_u - target)
        elif move is None:
            conc = h.first_share * span
            if f == "anchor_punisher" and u_best < quote_u - 1.5 * h.room * quote_u:
                conc = -0.01 * price  # an insult: it raises its price and spends patience
                h.cum_gap += 0.15
        elif move <= 0.001 * price:
            h.stall += 1
            conc = 0.0
            if f == "hold_punisher":
                h.stall = 2
        elif f == "package_seeker" and not self.terms_moved(case, best, h):
            conc = 0.0
        elif f == "frozen":
            conc = 0.0
        else:
            k = max(0, rnd - 2)
            conc = min(h.ratio * move, h.cap * price * h.decay**k)
        conc += h.claim_bonus * verified * price
        conc *= 1 + h.rng.uniform(-h.noise, h.noise) * 10 if h.noise else 1
        next_u = max(h.floor_u, h.standing_u - conc) if conc >= 0 else h.standing_u - conc
        h.cum_gap += max(0.0, (h.standing_u - u_best) / price)
        h.last_ours_u, h.last_terms = u_best, best
        if u_best >= next_u - 1e-9 and u_best >= h.floor_u:
            return self.agree(s, th, h, best, u_best, out)
        terms = dict(h.standard) if h.stubborn else dict(best)
        terms = {k: terms.get(k, best.get(k)) for k in case["negotiable"]}
        if not musts_ok(case, terms, h.standard):
            terms = dict(best)
        p_new = price_at(case, terms, h, next_u)
        if not p_new:
            terms, p_new = dict(th["stated"]), price
        stated = {**terms, "unit_price": p_new}
        if utility(case, stated, h) > h.standing_u + 1e-6 and conc >= 0:
            stated = dict(th["stated"])
        h.standing_u = utility(case, stated, h)
        final = (h.standing_u <= h.floor_u + 1e-6 or rnd >= h.final_round or h.cum_gap >= h.patience
                 or h.stall >= 2)  # fmt: skip
        status = "final_offer" if final else "open"
        h.status = status
        words = "This is our final offer." if final else "Our counter-offer follows."
        return self.reply(s, th, h, status, stated, out, words, ["quote.pdf"] if final else [])

    def terms_moved(self, case, sheet, h):
        prev = h.last_terms or h.quote
        return any(sheet.get(f) != prev.get(f) for f in case["negotiable"] if f != "unit_price")

    def agree(self, s, th, h, sheet, u, out, words=None):
        h.status, h.standing_u = "agreed", u
        return self.reply(s, th, h, "agreed", dict(sheet), out, "We accept your terms.", ["quote.pdf"])

    def reply(self, s, th, h, status, stated, out, words, files=()):
        body = f"Dear Sir or Madam,\n\n{words}\n\nKind regards\nSales"
        r = self._mail(s, th, status, stated, body, list(files))
        r["claim_results"] = out
        r["signals"] = {"reply_delay_days": 2, "validity_days": 30}
        s["trace"].append({"round": th["round"], "actor": f"sup:{th['key']}", "move": {"status": status},
                           "reasons": [f"sim3 {h.family}: gap {h.cum_gap:.2f} of {h.patience:.2f}"]})  # fmt: skip
        return r

    def _end(self, s, reason, sup):
        case, ths = s["case"], s["threads"]
        hs = {k: self.hidden[(s["id"], k)] for k in ths}
        opens = [c_of(case, h.quote, h) for h in hs.values() if musts_ok(case, h.quote, h.standard)]
        c_open = min(opens) if opens else min(c_of(case, h.quote, h) for h in hs.values())
        alt = c_alt(case)
        c_best = min([alt] + [best_c(case, h) for h in hs.values()])
        c_best = min(c_best, c_open)
        r = {"ended": True, "deal": False, "supplier_id": None, "offer_id": None, "terms": None, "c_open": c_open,
             "c_deal": None, "c_alt": alt if math.isfinite(alt) else None, "c_best": c_best, "must_haves_ok": None,
             "cost_breakdown": None, "trace": [],
             "families": {k: h.family for k, h in hs.items()}}  # fmt: skip
        r["score"] = score(c_open, alt, c_best) if math.isfinite(alt) else 0.0
        if sup:
            h = hs[sup]
            t = closing(case, ths[sup]["stated"], ths[sup]["standard"])
            r.update(deal=True, supplier_id=sup, offer_id=ths[sup]["offer_id"], terms=t, c_deal=c_of(case, t, h),
                     must_haves_ok=musts_ok(case, t))  # fmt: skip
            r["score"] = score(c_open, r["c_deal"], c_best) if r["must_haves_ok"] else 0.0
        s.update(status="ended", end_reason=reason, result=r)
        for th in ths.values():
            th["closed"] = th["closed"] or reason


def build_sim(seed=1, quick=False, clock=None, no_clock=False):
    """Every practice and sandbox case file plus sim2's structural variants (new suppliers, goals,
    twin bids), each played against seeded hidden suppliers; `quick` one seed, else three."""
    world = load_world()
    cases = base_cases()
    allc = {**cases, **variants(cases, world.for_case({"parts": []}))}
    suite = {}
    for k in [1] if quick else [1, 2, 3]:
        for cid, c in allc.items():
            cc = copy.deepcopy(c)
            cc["case_id"] = f"{cid}~s{k}"
            cc["base_case_id"] = c.get("base_case_id") or cid
            cc["synthetic_suppliers"] = True
            suite[cc["case_id"]] = cc
    return Sim3(suite, world.facts, world, seed=seed, clock=clock, no_clock=no_clock)


def suite(seed=1, quick=False):
    sim = build_sim(seed, quick, clock=lambda: 1_000_000.0)
    return sim, create_app(sim), [(cid, seed) for cid in sorted(sim.cases)]
