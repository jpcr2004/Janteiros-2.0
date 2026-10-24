"""C of a term sheet (arena_kit.total_cost is the single source of truth), the alternative, the
close gate, and helpers that keep every number finite and valid. No model calls here."""

import math

from arena_kit.cost import (
    RELATIONSHIP_GOALS,
    closing_terms,
    cost_breakdown,
    must_haves_ok,
    total_cost,
)

COMPONENTS = ("price", "uncovered_need", "fallback_risk", "payment_terms", "freight", "late_delivery",
              "quality", "tooling", "index_clause", "index_risk", "relationship")  # fmt: skip


def relationship_goal(case) -> bool:
    b = case["buyer"]
    return b.get("category_goal") in RELATIONSHIP_GOALS and bool(b.get("relationship_eur_per_point"))


def safe_std(case, std):
    """Standard terms the kit can price: an Incoterm the case does not price becomes the costliest
    (as the kit assumes for a gap); fields of the wrong type are dropped."""
    std = dict(std or {})
    freight = case["buyer"]["freight_eur_per_unit"]
    if "incoterm" in std and std["incoterm"] not in freight:
        std["incoterm"] = max(freight, key=freight.get)
    for k in ("payment_days", "lead_time_days", "volume_per_year", "contract_months", "ppm_commitment"):
        if k in std and not isinstance(std[k], int | float):
            std.pop(k)
    return std


def safe_terms(case, terms):
    terms = dict(terms or {})
    freight = case["buyer"]["freight_eur_per_unit"]
    if terms.get("incoterm") is not None and terms["incoterm"] not in freight:
        terms["incoterm"] = max(freight, key=freight.get)
    return terms


def _supplier_arg(est, case=None, terms=None):
    """The kit's supplier estimate dict, or None (no relationship charge can be priced). An `Est`
    adds what the kit leaves out (overtime, ppm, clause) as the kit's per-unit `extra`."""
    if est is None:
        return None
    if hasattr(est, "kit_for") and case is not None:
        d = est.kit_for(case, terms)
    else:
        d = est.kit() if hasattr(est, "kit") else est
    return d if d and d.get("full_cost") else None


def cost(case, terms, std=None, est=None) -> float:
    """C in EUR, or inf for a sheet the cost function cannot price."""
    try:
        terms = safe_terms(case, terms)
        c = total_cost(case, terms, safe_std(case, std), _supplier_arg(est, case, terms))
    except (KeyError, TypeError, ValueError, ZeroDivisionError, AttributeError):
        return math.inf
    return c if isinstance(c, int | float) and math.isfinite(c) else math.inf


def breakdown(case, terms, std=None, est=None) -> dict:
    try:
        terms = safe_terms(case, terms)
        return cost_breakdown(case, terms, safe_std(case, std), _supplier_arg(est, case, terms))
    except (KeyError, TypeError, ValueError, ZeroDivisionError, AttributeError):
        return {}


def closing(case, terms, std=None) -> dict:
    """What a deal on `terms` closes on: the sheet, then the supplier's standard terms, then fixed."""
    return closing_terms(case, safe_terms(case, terms), safe_std(case, std))


def musts_ok(case, terms, std=None) -> bool:
    try:
        return must_haves_ok(case, safe_terms(case, terms), safe_std(case, std))
    except (KeyError, TypeError):
        return False


def has_alt(case) -> bool:
    alt = case["buyer"].get("alternative") or {}
    return alt.get("kind") not in (None, "none") and bool((alt.get("terms") or {}).get("unit_price"))


def c_alt(case) -> float:
    """C of the alternative; inf when there is none (a walk then scores 0)."""
    if not has_alt(case):
        return math.inf
    return cost(case, case["buyer"]["alternative"]["terms"])


def c_alt_score_value(case):
    """What no deal costs: the alternative, or the uncovered need at the fallback price."""
    return c_alt(case)


def gate(case, terms, std=None, est=None):
    """The close gate: (ok, C, why). A deal must have a price > 0, keep every must-have on its
    closing terms (missing fields at the supplier's standard terms) and cost less than the
    alternative, relationship charge included when we can estimate it."""
    price = (terms or {}).get("unit_price")
    if not isinstance(price, int | float) or not price > 0:
        return False, math.inf, "no positive price"
    if not musts_ok(case, terms, std):
        return False, math.inf, "breaks a must-have"
    c = cost(case, terms, std, est)
    if not math.isfinite(c):
        return False, c, "cannot price"
    alt = c_alt(case)
    if not c < alt:
        return False, c, f"not below the alternative ({c:.2f} >= {alt:.2f})"
    return True, c, "ok"


def at_price(terms, price):
    return {**terms, "unit_price": round(float(price), 2)}


def price_slope(case, terms, std=None):
    """dC/dprice without the relationship charge (C is linear in price otherwise)."""
    c1 = cost(case, at_price(terms, 10.0), std)
    c2 = cost(case, at_price(terms, 20.0), std)
    return (c2 - c1) / 10.0


def price_for_cost(case, terms, std, target_c, est=None, lo=0.01, hi=None):
    """The highest price at `terms` whose C (with relationship) is still below `target_c`, or None.
    C is convex in price (relationship falls as price rises), so search from C's minimum upward."""
    if not math.isfinite(target_c):
        return None
    hi = hi or max(1.0, (terms.get("unit_price") or 1.0) * 4)

    def c(p):
        return cost(case, at_price(terms, p), std, est)

    if not relationship_goal(case) or _supplier_arg(est, case, at_price(terms, hi)) is None:
        c1, c2 = c(10.0), c(20.0)  # C is linear in price here
        slope = (c2 - c1) / 10.0
        if not (math.isfinite(slope) and slope > 0):
            return None
        p = math.floor((10.0 + (target_c - c1) / slope) * 100) / 100
        while p > 0.01 and not c(p) < target_c:
            p = round(p - 0.01, 2)
        return p if p > 0.01 else None

    a, b = lo, hi
    for _ in range(80):
        m1, m2 = a + (b - a) / 3, b - (b - a) / 3
        a, b = (a, m2) if c(m1) <= c(m2) else (m1, b)
    low = (a + b) / 2
    if not c(low) < target_c:
        return None
    lo_p, hi_p = low, hi
    if c(hi_p) < target_c:
        return round(hi_p, 2)
    for _ in range(60):
        mid = (lo_p + hi_p) / 2
        lo_p, hi_p = (mid, hi_p) if c(mid) < target_c else (lo_p, mid)
    p = math.floor(lo_p * 100) / 100
    while p > 0.01 and not c(p) < target_c:
        p = round(p - 0.01, 2)
    return p if p > 0 else None


def break_even(case, terms, std=None, est=None):
    """The highest price at `terms` still cheaper than the alternative; None without alternative."""
    alt = c_alt(case)
    if not math.isfinite(alt):
        return None
    return price_for_cost(case, terms, std, alt, est)


def score(c_open, c_deal, c_best):
    if c_deal is None or not math.isfinite(c_deal):
        return 0.0
    if c_open - c_best <= 0.01:
        return 1.0 if c_deal <= c_best + 0.01 else 0.0
    return min(1.0, max(0.0, (c_open - c_deal) / (c_open - c_best)))
