import json
import pathlib

from janteiros.runner.state import Thread
from janteiros.validate.check import validate

CASES = {
    c["case_id"]: c for c in json.loads((pathlib.Path(__file__).parents[1] / "data/cases/practice.json").read_text())
}
P02 = CASES["case:P02"]


def thread():
    t = Thread("sup:70510")
    t.messages = [{"from": "sales@70510", "status": "open", "body": "",
                   "term_sheets": [{"term_sheet_id": "offer:70510-0",
                                    "terms": {"unit_price": 370.96, "payment_days": 30, "index_clause": None}}]}]  # fmt: skip
    return t


def msg(**kw):
    m = {"subject": "Re: offer", "body": "Dear Sir or Madam,\nthank you.", "refs": [],
         "term_sheets": [{"terms": {"unit_price": 355.0, "payment_days": 60, "index_clause": None}}]}  # fmt: skip
    m.update(kw)
    return m


def test_a_good_message_passes():
    assert validate(P02, msg(), thread()) == []


def test_validator_blocks_bad_sheets():
    t = thread()
    assert validate(P02, msg(term_sheets=[{"terms": {"unit_price": 355.0, "payment_days": 60}}]), t)
    assert validate(
        P02, msg(term_sheets=[{"terms": {"unit_price": 355.0, "payment_days": 61, "index_clause": None}}]), t
    )
    assert validate(P02, msg(term_sheets=[{"terms": {"unit_price": 0, "payment_days": 60, "index_clause": None}}]), t)
    assert validate(
        P02, msg(term_sheets=[{"terms": {"unit_price": 355.123, "payment_days": 60, "index_clause": None}}]), t
    )
    above_alt = {"unit_price": 999.0, "payment_days": 60, "index_clause": None}
    assert any("alternative" in p for p in validate(P02, msg(term_sheets=[{"terms": above_alt}]), t))
    at_standing = {"unit_price": 371.0, "payment_days": 30, "index_clause": None}
    assert any("standing" in p for p in validate(P02, msg(term_sheets=[{"terms": at_standing}]), t))
    four = [{"terms": {"unit_price": 350.0 + i, "payment_days": 60, "index_clause": None}} for i in range(4)]
    assert any("3 term sheets" in p for p in validate(P02, msg(term_sheets=four), t))


def test_validator_blocks_words_numbers_and_unknown_ids():
    t = thread()
    assert validate(P02, msg(body="We have an urgent deadline."), t)
    assert validate(P02, msg(body="Our spend was 2,637 EUR."), t)
    assert validate(P02, msg(body="See idx:CU-NOTATION@2026-08."), t)  # not one of our claims
    claim = {"sentence": "x", "refs": ["idx:CU-NOTATION@2026-08"]}
    assert validate(P02, msg(body="See idx:CU-NOTATION@2026-08.", refs=claim["refs"]), t, claims=[claim],
                    known_ids=lambda r: False)  # fmt: skip
    assert validate(P02, msg(body="See idx:CU-NOTATION@2026-08.", refs=claim["refs"]), t, claims=[claim],
                    known_ids=lambda r: True) == []  # fmt: skip
    assert validate(P02, msg(body="x" * 8001), t)
    assert validate(P02, {"action": {"type": "accept", "term_sheet_id": "offer:70510-9"}}, t)
    assert validate(P02, {"action": {"type": "accept", "term_sheet_id": "offer:70510-0"}}, t) == []
