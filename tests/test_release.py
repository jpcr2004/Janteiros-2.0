"""The Sunday release as a scenario: the suppliers it talks about, on case structures of their own
category (sim2 openings), with the release read and without it. Skipped until `uv run arena data`
has put data/release-sunday next to the package."""

import copy
from pathlib import Path

import pytest

from janteiros.events.log import NullLog
from janteiros.runner.play import make_policy, world
from janteiros.runner.state import Thread
from janteiros.world.index import World

ROOT = Path(__file__).resolve().parents[1]
W = world()
pytestmark = pytest.mark.skipif(W is None or W.release is None, reason="no Sunday release on disk")
# release supplier -> a practice case of its category (sim2 gives it an opening)
BASE = {"sup:70170": "case:P01", "sup:70190": "case:P01", "sup:70270": "case:P09", "sup:70640": "case:P04",
        "sup:70340": "case:P12"}  # fmt: skip


@pytest.fixture(scope="module")
def sim():
    from janteiros.sim2.server import suite

    return suite(seed=1, quick=True)[0]


def first_move(sim, sid, w):
    base = BASE[sid]
    case = copy.deepcopy(sim.cases[base])
    case.update(case_id=f"{base}r{sid[-3:]}", base_case_id=base, suppliers=[sid], synthetic_suppliers=True,
                trigger_doc=None)  # fmt: skip
    sim.cases[case["case_id"]] = case
    opening = sim.open("t", case["case_id"])["threads"][sid][0]
    t = Thread(sid)
    t.messages = [opening]
    pol = make_policy("main", case)
    pol.world = w.for_case(case)
    pol.claim_bank = None
    pol.setup({sid: t}, NullLog())
    return case, pol.sides[sid], pol.next_move(t, 0.0)


def test_release_ids_are_known():
    cw = W.for_case({"parts": []})
    for ref in ("news:20261014-S11", "news:20261020-S16", "doc:20261016-70170-S12", "doc:20261023-HPW-MEMO"):
        assert cw.known(ref), ref


def test_cash_short_suppliers_get_faster_payment_for_price(sim):
    for sid in ("sup:70170", "sup:70190"):
        case, side, mv = first_move(sim, sid, W)
        assert "cash_need" in side.prior.traits, sid
        pay = (case.get("term_options") or {}).get("payment_days") or []
        if "payment_days" in case["negotiable"] and pay:
            assert mv.sheets[0]["payment_days"] == min(pay), (sid, mv.sheets[0])


def test_short_time_work_means_more_room(sim):
    without = World(ROOT / "data" / "package", release="/nonexistent")
    _, side, _ = first_move(sim, "sup:70270", W)
    _, side0, _ = first_move(sim, "sup:70270", without)
    assert {"spare_capacity", "cash_need"} <= side.prior.traits
    assert side.prior.room > side0.prior.room


def test_distributor_with_a_lost_contract_has_spare_capacity(sim):
    _, side, mv = first_move(sim, "sup:70640", W)
    assert "spare_capacity" in side.prior.traits and mv is not None


def test_steel_bought_forward_gets_no_material_index_argument_first(sim):
    case, side, mv = first_move(sim, "sup:70340", W)
    assert "hedged" in side.prior.traits
    from janteiros.claims.bank import ClaimBank

    bank = ClaimBank(case, W.for_case(case))
    t = Thread("sup:70340")
    t.messages = [sim.open("t", case["case_id"])["threads"]["sup:70340"][0]]
    bank.prepare("sup:70340", t)
    first = bank.bank["sup:70340"][:2]
    assert not any(c["kind"] == "driver" and not c["refs"][0].startswith("idx:LCI") for c in first), first


def test_every_release_supplier_plays_to_a_valid_end(sim):
    from janteiros.evaluate import in_process_arena
    from janteiros.runner.play import play_case
    from janteiros.sim2.server import suite

    s, app, _ = suite(seed=2, quick=True)
    for sid, base in BASE.items():
        case = copy.deepcopy(s.cases[base])
        case.update(case_id=f"{base}x{sid[-3:]}", base_case_id=base, suppliers=[sid], synthetic_suppliers=True,
                    trigger_doc=None)  # fmt: skip
        s.cases[case["case_id"]] = case
        r = play_case(in_process_arena(s, app), case, NullLog())
        assert r["ended"] and (not r["deal"] or r["must_haves_ok"]), (sid, r)
