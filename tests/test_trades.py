import json
import pathlib

from janteiros.econ.cost import cost
from janteiros.econ.supplier import make_est, value
from janteiros.policy.trades import candidates, requested_fields

CASES = {
    c["case_id"]: c for c in json.loads((pathlib.Path(__file__).parents[1] / "data/cases/practice.json").read_text())
}


def test_requests_only_select_fields():
    assert requested_fields({"faster_payment"}) == [("payment_days", "lower")]
    assert requested_fields({"volume_commitment"}) == [("volume_per_year", "higher")]
    assert ("lead_time_days", "higher") in requested_fields(set(), {"capacity_full"})


def test_payment_trade_preserves_supplier_value_and_improves_buyer_cost():
    case = CASES["case:P02"]
    std = {"payment_days": 30, "index_clause": None}
    est = make_est(case, std, rate=0.20)
    main = {"unit_price": 355.0, "payment_days": 60, "index_clause": None}
    out = candidates(case, std, est, None, main, asks={"faster_payment"})
    assert out
    trade = out[0]
    assert trade.terms["payment_days"] < main["payment_days"]
    assert abs(value(case, trade.terms, est) - value(case, main, est)) < 0.02
    assert cost(case, trade.terms, std) < cost(case, main, std)


def test_trade_is_not_repeated():
    case = CASES["case:P02"]
    std = {"payment_days": 30, "index_clause": None}
    est = make_est(case, std, rate=0.20)
    main = {"unit_price": 355.0, "payment_days": 60, "index_clause": None}
    first = candidates(case, std, est, None, main, asks={"faster_payment"})
    from janteiros.policy.trades import signature

    assert first
    again = candidates(case, std, est, None, main, asks={"faster_payment"}, seen={signature(first[0].terms)})
    assert all(signature(row.terms) != signature(first[0].terms) for row in again)
