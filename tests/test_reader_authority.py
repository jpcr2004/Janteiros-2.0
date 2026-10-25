import json
import pathlib

from janteiros.policy.negotiator import Negotiator
from janteiros.runner.state import Thread

CASES = {
    c["case_id"]: c for c in json.loads((pathlib.Path(__file__).parents[1] / "data/cases/practice.json").read_text())
}


class Log:
    def emit(self, *args, **kwargs):
        pass


class AlarmistReader:
    def read(self, body, timeout=6.0, evidence=None):
        assert evidence is not None
        return {
            "asks": ["faster_payment", "volume_commitment"],
            "mood": "firm",
            "tactic": "trade_terms",
            "price_frozen": True,
            "cash_short": True,
            "capacity_tight": True,
        }


def test_reader_can_select_requests_but_cannot_mutate_economics():
    case = CASES["case:P02"]
    sid = case["suppliers"][0]
    terms = {"unit_price": 370.96, "payment_days": 30, "index_clause": None}
    opening = {
        "from": "supplier",
        "status": "open",
        "body": "Our quote follows.",
        "term_sheets": [{"term_sheet_id": "offer:open", "terms": terms}],
    }
    t = Thread(sid, messages=[opening])
    neg = Negotiator(case, reader=AlarmistReader())
    neg.setup({sid: t}, Log())
    side = neg.sides[sid]
    before = (side.est.rate, side.est.volume_value, side.est.overtime, side.est.full_cost)
    reply = {
        "from": "supplier",
        "status": "open",
        "body": "Cash is tight. A binding volume would help. We are fully booked.",
        "term_sheets": [{"term_sheet_id": "offer:reply", "terms": terms}],
    }
    t.messages.append(reply)
    neg.observe(t, None, reply, None)
    assert (side.est.rate, side.est.volume_value, side.est.overtime, side.est.full_cost) == before
    assert {"faster_payment", "volume_commitment"} <= side.asks
    assert {"price_frozen", "cash_short", "capacity_tight"} <= side.observed
