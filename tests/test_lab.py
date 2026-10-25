"""Lab rules: send back a supplier's own cheaper alternative (MIRROR), make a valid package standing
before giving up or jumping to the ceiling (RESCUE), and the generic prior stays off unless asked."""

import json
import pathlib

from janteiros import flags
from janteiros.econ.cost import gate
from janteiros.events.log import NullLog
from janteiros.runner.play import make_policy
from janteiros.runner.state import Thread

ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = {
    c["case_id"]: c for f in ("practice", "sandbox") for c in json.loads((ROOT / f"data/cases/{f}.json").read_text())
}
P10 = {"unit_price": 33.8, "lead_time_days": 42, "volume_per_year": 23400, "contract_months": 24}


def thread(case_id, sheets, status="final_offer"):
    case = CASES[case_id]
    sid = case["suppliers"][0]
    t = Thread(sid)
    t.messages = [
        {"from": "sales@x", "status": "open", "body": "Our quote.", "attachments": [],
         "term_sheets": [{"term_sheet_id": f"offer:{sid[4:]}-0", "terms": {**P10, "unit_price": 34.5}}]},
        {"from": "buyer", "term_sheets": [{"terms": {**P10, "unit_price": 30.0}}]},
        {"from": "sales@x", "status": status, "body": "Our offer.", "attachments": [],
         "term_sheets": [{"term_sheet_id": f"offer:{sid[4:]}-1{'-a' * i}", "terms": s} for i, s in enumerate(sheets)]},
    ]  # fmt: skip
    pol = make_policy("main", case)
    pol.setup({t.sid: t}, NullLog())
    return case, t, pol


def test_mirror_sends_back_its_alternative_when_it_costs_us_less():
    case, t, pol = thread("case:P10", [P10, {**P10, "unit_price": 32.9}])
    pick = pol.mirror_pick(t, "final_offer")
    assert pick is not None and pick[1]["unit_price"] == 32.9
    mv = pol.mirror_move(t, pick, "final_offer")
    assert mv.sheets == [pick[1]] and mv.allow_at_standing
    assert pol.mirror_pick(t, "final_offer") is None  # once per supplier


def test_mirror_judges_by_cost_not_by_price():
    slow = {**P10, "unit_price": 33.7, "lead_time_days": 56}  # cheaper price, slower delivery
    case, t, pol = thread("case:P10", [P10, slow])
    c_st = gate(case, P10, t.std, pol.est(t))[1]
    c_slow = gate(case, slow, t.std, pol.est(t))[1]
    assert c_slow > c_st  # the precondition: the late delivery costs more than the 0.10 saved
    assert pol.mirror_pick(t, "final_offer") is None


def test_mirror_waits_until_the_thread_is_done_and_can_be_switched_off(monkeypatch):
    case, t, pol = thread("case:P10", [P10, {**P10, "unit_price": 32.9}], status="open")
    assert pol.mirror_pick(t, None) is None
    monkeypatch.setenv("JT_X_MIRROR", "0")
    assert pol.mirror_pick(t, "final_offer") is None


def test_rescue_makes_a_valid_package_standing_without_an_alternative():
    # its sheet leaves the volume to its standard terms, below the must-have (>= 22230)
    case, t, pol = thread("case:P10", [{"unit_price": 33.0, "lead_time_days": 42, "volume_per_year": 19890,
                                        "contract_months": 24}])  # fmt: skip
    ok = gate(case, t.standing[1], t.std, pol.est(t))[0]
    assert not ok
    assert pol.rescue_due(t, ok, "patience used (0.28)")
    mv = pol.rescue_move(t, "patience used (0.28)")
    assert mv is not None and len(mv.sheets) == 1
    assert gate(case, mv.sheets[0], t.std, pol.est(t))[0]
    assert mv.sheets[0]["volume_per_year"] >= 22230


def test_experiments_measured_worse_stay_off_unless_named(monkeypatch):
    monkeypatch.delenv("JT_X_GENERIC", raising=False)
    monkeypatch.delenv("JT_X_ROBUST_FC", raising=False)
    monkeypatch.delenv("JT_X_ALL", raising=False)
    monkeypatch.delenv("JT_X_PROBE", raising=False)
    assert not flags.on("GENERIC") and not flags.on("ROBUST_FC") and not flags.on("PROBE")
    assert flags.on("RESCUE") and flags.on("MIRROR")
    monkeypatch.setenv("JT_X_ALL", "1")
    assert not flags.on("GENERIC")
    monkeypatch.setenv("JT_X_GENERIC", "1")
    assert flags.on("GENERIC")
