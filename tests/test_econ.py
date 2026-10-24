import json
import math
import pathlib

import pytest

from janteiros.econ.cost import breakdown, c_alt, cost, gate, price_for_cost
from janteiros.econ.optimise import at_cost, at_value
from janteiros.econ.supplier import make_est, price_for_value, value
from janteiros.econ.terms import combos, complete

HERE = pathlib.Path(__file__).parent
CASES = {
    c["case_id"]: c
    for f in ("practice", "sandbox")
    for c in json.loads((HERE.parent / "data" / "cases" / f"{f}.json").read_text())
}
RESULTS = json.loads((HERE / "fixtures" / "results.json").read_text())


def test_cost_engine_reproduces_every_recorded_arena_deal():
    """C without the relationship charge (which needs the supplier's hidden full cost) matches the
    arena's breakdown component by component on all recorded real deals."""
    assert len(RESULTS) > 150
    for r in RESULTS:
        case = CASES[r["case_id"]]
        b = breakdown(case, r["terms"])
        for k, v in r["cost_breakdown"].items():
            if k != "relationship":
                assert b[k] == pytest.approx(v, abs=0.05), (r["session_id"], k)
        assert cost(case, r["terms"]) == pytest.approx(r["c_deal"] - r["cost_breakdown"]["relationship"], abs=0.05)


def test_gate_rejects_must_have_break_and_above_alternative():
    p05 = CASES["case:P05"]
    terms = {"unit_price": 30.0, "payment_days": 30, "lead_time_days": 56}
    assert gate(p05, terms)[0] is False  # lead time must be <= 42
    assert gate(p05, {**terms, "lead_time_days": 42})[0] is True  # no alternative: any valid deal
    p02 = CASES["case:P02"]
    alt = c_alt(p02)
    assert math.isfinite(alt)
    assert gate(p02, {"unit_price": 999.0, "payment_days": 30, "index_clause": None})[0] is False
    assert gate(p02, {"unit_price": 0.0, "payment_days": 30, "index_clause": None})[0] is False


def test_price_for_cost_inverts_cost():
    case = CASES["case:P04"]
    terms = complete(case, {"unit_price": 5.0}, {})
    target = cost(case, terms) - 100
    p = price_for_cost(case, terms, {}, target)
    assert cost(case, {**terms, "unit_price": p}) < target <= cost(case, {**terms, "unit_price": p + 0.01})


def test_value_is_linear_and_invertible():
    case = CASES["case:P01"]
    est = make_est(case, {"payment_days": 30}, rate=0.08)
    terms = complete(case, {"unit_price": 135.0}, {})
    p = price_for_value(case, terms, est, 130.0)
    assert value(case, {**terms, "unit_price": p}, est) == pytest.approx(130.0, abs=1e-6)


def test_optimiser_packages_are_valid_and_ranked():
    case = CASES["case:P05"]
    est = make_est(case, {"payment_days": 30, "lead_time_days": 42}, rate=0.13)
    pk = at_value(case, {}, est, 30.0)
    assert pk and all(a.c <= b.c for a, b in zip(pk, pk[1:], strict=False))
    for k in pk:
        assert k.terms["lead_time_days"] <= 42 and k.terms["unit_price"] > 0
    ac = at_cost(case, {}, est, pk[0].c + 1000, ref_price=30.0)
    assert ac and all(a.v >= b.v for a, b in zip(ac, ac[1:], strict=False))
    assert len(combos(case)) == 5 * 4
