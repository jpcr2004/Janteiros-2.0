"""More bidders than practice ever shows (4 and 5 on one RFQ), long-tail supplier latency, fake
time: the arena answers one call per session at a time, so each thread waits for the others. The
case must still end in our own accept, with no policy error and no lost case."""

import copy

import pytest
from arena_kit.sim import Sim
from conftest import all_case_files, timed_arena
from test_clock_tail import TailTime

from janteiros.events.log import read_events
from janteiros.runner.play import play_case


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setenv("JT_NO_LLM", "1")


@pytest.mark.parametrize("n,seed", [(4, 1), (4, 2), (5, 3)])
def test_four_and_five_bidders_under_long_tail_latency(log, n, seed):
    case = copy.deepcopy(all_case_files()["case:P05"])
    case["case_id"] = f"case:P05x{n}"
    case["suppliers"] = list(case["suppliers"]) + [f"sup:DEMO-X{i}" for i in range(n - len(case["suppliers"]))]
    fake = TailTime(seed)
    sim = Sim({case["case_id"]: case}, clock=fake)
    r = play_case(timed_arena(sim, fake), case, log, mono=fake, wall=fake)
    assert r["ended"] and r.get("end_reason") == "accepted", r
    assert r["deal"] and r["must_haves_ok"], r
    events = read_events(log.run_id, log.dir.parent)
    errors = [e for e in events if e["kind"] == "error" and e.get("where") == "policy"]
    assert not errors, errors
