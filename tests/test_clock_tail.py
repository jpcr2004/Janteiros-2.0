"""Long-tail supplier latency (p50 ~19 s, p90 ~28 s, some 45-55 s) on the kit sim in fake time:
two and three bidders and a single supplier still end validly (our own accept or walk). A timeout may happen on a
55 s reply (the arena counts the clock from it), but it must never cost the case."""

import random

import pytest
from arena_kit.sim import Sim
from conftest import FakeTime, all_case_files, timed_arena

from janteiros.events.log import read_events
from janteiros.runner.play import play_case

FILES = all_case_files()


class TailTime(FakeTime):
    def __init__(self, seed, **kw):
        super().__init__(**kw)
        self.rng = random.Random(seed)

    @property
    def latency(self):
        u = self.rng.random()
        if u < 0.5:
            return self.rng.uniform(12, 19)
        if u < 0.9:
            return self.rng.uniform(19, 28)
        if u < 0.97:
            return self.rng.uniform(28, 40)
        return self.rng.uniform(45, 55)

    @latency.setter
    def latency(self, v):
        pass


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setenv("JT_NO_LLM", "1")


@pytest.mark.parametrize("cid,seed", [(c, s) for c in ("case:P05", "case:D02", "case:P10") for s in (1, 2, 3)])
def test_long_tail_latency_never_loses_the_case(log, cid, seed):
    fake = TailTime(seed)
    case = FILES[cid]
    sim = Sim({cid: case}, clock=fake)
    arena = timed_arena(sim, fake)
    r = play_case(arena, case, log, mono=fake, wall=fake)
    assert r["ended"] and r.get("end_reason") in ("accepted", "walked_away"), r  # D02: the kit's alternative wins
    assert r["deal"] or cid == "case:D02", r
    assert not r["deal"] or r["must_haves_ok"], r
    errors = [e for e in read_events(log.run_id, log.dir.parent) if e["kind"] == "error" and e.get("where") == "policy"]
    assert not errors, errors
