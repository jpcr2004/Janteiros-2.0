import math

import pytest

from janteiros.econ.cost import c_alt
from janteiros.events.log import read_events
from janteiros.runner.play import play_case

CASES = ["case:S01", "case:S02", "case:S03"]


@pytest.mark.parametrize("cid", CASES)
def test_trivial_policy_ends_valid(arena, kit_sim, log, cid):
    case = kit_sim.cases[cid]
    r = play_case(arena, case, log, policy="trivial")
    assert r["ended"]
    if r["deal"]:
        assert r["must_haves_ok"] and r["c_deal"] < c_alt(case)
    else:
        assert math.isfinite(c_alt(case))  # walk only with an alternative
    kinds = {e["kind"] for e in read_events(log.run_id, log.dir.parent)}
    assert {"case_open", "quote", "result"} <= kinds
