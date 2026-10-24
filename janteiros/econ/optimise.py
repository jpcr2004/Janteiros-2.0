"""The term-space optimiser. For every allowed combination of non-price fields:
- at a target C for us: the price that costs us exactly that C, and what the package is worth to
  the supplier there (`at_cost`): the package it values most is the one to offer;
- at a target value to the supplier: the price that pays it the same, and our C there
  (`at_value`): the cheapest such package is the deal it should take as readily.
A Pareto list over (our C, its value) drives anchors, multi-sheet offers, trades and the close."""

import math
from dataclasses import dataclass

from .cost import cost, musts_ok, price_for_cost
from .supplier import Est, price_for_value, value
from .terms import combos


@dataclass
class Package:
    terms: dict
    c: float  # our C
    v: float  # its value per unit

    @property
    def price(self):
        return self.terms["unit_price"]


def _valid(case, terms, std):
    p = terms.get("unit_price")
    return isinstance(p, int | float) and p > 0.01 and musts_ok(case, terms, std)


def at_cost(case, std, est: Est, target_c, fixed=None, ref_price=None):
    """Packages that cost us (just under) `target_c`, best value to the supplier first."""
    out = []
    for combo in combos(case, std, fixed):
        p = price_for_cost(case, {**combo, "unit_price": ref_price or 1.0}, std, target_c, est,
                           hi=max(1.0, (ref_price or 1.0) * 4))  # fmt: skip
        if p is None:
            continue
        terms = {**combo, "unit_price": p}
        if not _valid(case, terms, std):
            continue
        v = value(case, terms, est)
        if math.isfinite(v):
            out.append(Package(terms, cost(case, terms, std, est), v))
    out.sort(key=lambda k: -k.v)
    return out


def at_value(case, std, est: Est, target_v, fixed=None):
    """Packages worth `target_v` to the supplier, cheapest for us first."""
    out = []
    for combo in combos(case, std, fixed):
        p = price_for_value(case, combo, est, target_v)
        if p is None or not p > 0.01:
            continue
        terms = {**combo, "unit_price": round(p, 2)}
        if not _valid(case, terms, std):
            continue
        c = cost(case, terms, std, est)
        if math.isfinite(c):
            out.append(Package(terms, c, value(case, terms, est)))
    # equal cost for us (to the euro): the package friendlier to the supplier first
    out.sort(key=lambda k: (round(k.c), friendliness(k.terms)))
    return out


def friendliness(terms):
    """Lower sorts first = friendlier to the supplier: a longer contract and more volume (security,
    free for us beyond our horizon and need), a later delivery, a looser ppm, a smaller chargeback."""
    return (-(terms.get("contract_months") or 0), -(terms.get("volume_per_year") or 0),
            -(terms.get("lead_time_days") or 0), -(terms.get("ppm_commitment") or 0),
            terms.get("chargeback_eur_per_complaint") or 0)  # fmt: skip


def pareto(pkgs):
    """Packages no other beats on both our C (lower) and its value (higher)."""
    front, best_v = [], -math.inf
    for k in sorted(pkgs, key=lambda k: (k.c, -k.v)):
        if k.v > best_v + 1e-9:
            front.append(k)
            best_v = k.v
    return front


def relationship_optimum(case, terms, std, est, lo, hi, steps=240):
    """The price in [lo, hi] at `terms` with the lowest C (relationship charge included)."""
    best_p, best_c = None, math.inf
    for i in range(steps + 1):
        p = round(lo + (hi - lo) * i / steps, 2)
        c = cost(case, {**terms, "unit_price": p}, std, est)
        if c < best_c - 1e-6:
            best_p, best_c = p, c
    return best_p, best_c


def diverse(pkgs, case, n=3, key_fields=None):
    """Up to n packages that differ in their non-price terms."""
    seen, out = set(), []
    fields = key_fields or [f for f in case["negotiable"] if f != "unit_price"]
    for k in pkgs:
        sig = tuple(repr(k.terms.get(f)) for f in fields)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(k)
        if len(out) >= n:
            break
    return out
