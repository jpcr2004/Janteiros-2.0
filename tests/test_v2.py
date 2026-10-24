"""The changes after baseline-r4: writer guard, honest answers, forecast proof, attachments, twins."""

import base64
import json
import pathlib

from janteiros import flags
from janteiros.events.log import NullLog
from janteiros.llm.writer import changes, ok
from janteiros.policy.base import Move
from janteiros.runner.play import make_policy
from janteiros.runner.state import Thread
from janteiros.validate.check import check_attachment, validate

ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = {c["case_id"]: c for f in ("practice", "sandbox") for c in json.loads((ROOT / f"data/cases/{f}.json").read_text())}

BLOCK = [
    "We accept your offer and look forward to working together.",
    "We agree to your terms on the bearing brackets.",
    "We are happy to commit to the full quantity with you.",
    "We confirm the order on these terms.",
    "We guarantee that all our volume goes to your plant.",
    "We would like to make you our exclusive partner.",
    "We have tried to reflect your wish for faster payment in our proposal below.",
    "Your question about volume is fair, and we have reflected it in our proposal below.",
    "We have taken this into account in preparing our proposal below.",
    "We have addressed your concerns in our offer.",
    "I can tell you that you have won this business.",
    "We will change the terms if you need it.",
]
PASS = [
    "Thank you for explaining the pressure on your energy costs. We understand it and hope our proposal below helps.",
    "Thank you for taking our points into account in your counter-offer. We appreciate the step you made.",
    "We understand that a binding volume gives you planning security, and we hear that request.",
    "We value the long cooperation with your foundry and would like to keep it going on fair terms.",
    "Thank you for reflecting the index development in your revised quote; we appreciate the openness.",
]


def test_writer_blocks_commitments_and_claims_about_the_proposal():
    for p in BLOCK:
        assert not ok(p), p
    for p in PASS:
        assert ok(p), p


def test_writer_guard_off_is_the_baseline(monkeypatch):
    monkeypatch.setenv("JT_X_WRITER_GUARD", "0")
    assert ok("We have tried to reflect your wish for faster payment in our proposal below.")


def test_writer_may_acknowledge_terms_we_do_not_change():
    p = "We have also noted your question about a binding higher volume, and we hear it."
    assert ok(p, unchanged=["volume_per_year"])
    assert not ok("We have reflected your question about volume in our proposal below.", unchanged=["volume_per_year"])


def _thread(case_id, body="Here is our offer.", terms=None):
    case = CASES[case_id]
    sid = case["suppliers"][0]
    t = Thread(sid)
    t.messages = [{"from": "sales@x", "status": "open", "body": body, "attachments": [],
                   "term_sheets": [{"term_sheet_id": f"offer:{sid[4:]}-0", "terms": terms or {"unit_price": 47.0}}]}]  # fmt: skip
    return case, t


def test_changes_lists_what_our_sheet_moves():
    case, t = _thread("case:P11", terms={"unit_price": 47.0, "ppm_commitment": 6400})
    mv = Move("send", t.sid, sheets=[{"ppm_commitment": 300, "chargeback_eur_per_complaint": 0, "unit_price": 42.0}])
    said, same = changes(case, t, mv)
    assert any("quality" in s for s in said)
    assert "payment_days" in same  # fixed in P11: the writer may not talk about payment


def test_honest_answer_on_a_fixed_payment_term():
    case, t = _thread("case:P11", "Faster payment would help us more than anything.", {"unit_price": 46.0, "ppm_commitment": 300,
                                                                                         "chargeback_eur_per_complaint": 0.0})  # fmt: skip
    pol = make_policy("main", case)
    pol.setup({t.sid: t}, NullLog())
    pts = pol.answers(t, [{"ppm_commitment": 300, "chargeback_eur_per_complaint": 0, "unit_price": 44.0}])
    assert any("fixed at 60 days" in p for p in pts)
    assert not any("reflected" in p for p in pts)


def test_forecast_csv_once_on_request():
    body = "Please provide the corresponding forecast data so we can verify this volume."
    case, t = _thread("case:P18", body, {"unit_price": 5.0, "payment_days": 30, "contract_months": 36,
                                         "index_clause": None})  # fmt: skip
    pol = make_policy("main", case)
    pol.setup({t.sid: t}, NullLog())
    att = pol.forecast_proof(t)
    assert att and att["type"] == "csv"
    csv = base64.b64decode(att["content_b64"]).decode()
    assert csv.startswith("part,forecast_month,delivery_month,quantity,unit") and "370010" in csv
    assert check_attachment(att) == []
    assert pol.forecast_proof(t) is None  # once per supplier


def test_forecast_switch_off(monkeypatch):
    monkeypatch.setenv("JT_X_FORECAST", "0")
    case, t = _thread("case:P18", "Please provide the forecast data.", {"unit_price": 5.0})
    pol = make_policy("main", case)
    pol.setup({t.sid: t}, NullLog())
    assert pol.forecast_proof(t) is None


def test_attachment_checks():
    def att(text, typ="csv"):
        return {"name": "x.csv", "type": typ, "content_b64": base64.b64encode(text.encode()).decode()}

    assert check_attachment(att("a,b\n1,2\n")) == []
    assert check_attachment(att("a,b\n1,2\n", "exe"))
    assert check_attachment(att("ref,idx:LCI-C@2026-06\n"))
    assert check_attachment({"name": "x.csv", "type": "csv", "content_b64": "%%%"})
    case, t = _thread("case:P18")
    msg = {"subject": "Re: x", "body": "Hello", "attachments": [att("ref,fc:370010@2026-09\n")]}
    assert any(p.startswith("attachment ") for p in validate(case, msg, t))


def test_flags_all_off(monkeypatch):
    monkeypatch.setenv("JT_X_ALL", "0")
    assert not any(flags.state().values())
    monkeypatch.setenv("JT_X_TWINS", "1")
    assert flags.on("TWINS") and not flags.on("FORECAST")
