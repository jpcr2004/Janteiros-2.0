import pytest

from janteiros.econ.cost import c_alt
from janteiros.runner.play import play_case


@pytest.mark.parametrize("cid", ["case:S01", "case:S02", "case:S03"])
def test_main_policy_on_kit_sim(arena, kit_sim, log, cid, monkeypatch):
    monkeypatch.setenv("JT_NO_LLM", "1")
    case = kit_sim.cases[cid]
    r = play_case(arena, case, log, policy="main")
    assert r["ended"] and r["deal"], r
    assert r["must_haves_ok"] and r["c_deal"] < c_alt(case)
    print(cid, round(r["score"], 3), r.get("rounds"))
