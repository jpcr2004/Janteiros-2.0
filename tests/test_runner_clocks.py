"""Clocks, timeouts, keepalives, pruning, crash and resume, and the Live Final waves, on the kit's
scripted sim with a fake clock (every supplier reply takes `latency` seconds)."""

import pytest
from arena_kit.sim import Sim, create_app, load_cases
from conftest import FakeTime, all_case_files, timed_arena

from janteiros.events.log import read_events
from janteiros.runner.play import play_case
from janteiros.runner.session import CaseRunner
from janteiros.runner.waves import play_waves

FILES = all_case_files()


def kinds(log, kind):
    return [e for e in read_events(log.run_id, log.dir.parent) if e["kind"] == kind]


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setenv("JT_NO_LLM", "1")


@pytest.mark.parametrize("latency", [19.0, 28.0])
def test_three_bidders_no_timeouts(log, latency):
    fake = FakeTime(latency=latency)
    case = FILES["case:P05"]  # an RFQ with three bidders
    sim = Sim({case["case_id"]: case}, clock=fake)
    arena = timed_arena(sim, fake)
    r = play_case(arena, case, log, mono=fake, wall=fake)
    assert r["deal"] and r["must_haves_ok"], r
    assert kinds(log, "timeout") == []


@pytest.mark.parametrize("cid", sorted(FILES))
def test_every_case_file_plays_through(log, cid):
    """Every practice and sandbox case structure ends validly (scripted supplier, 20 s replies)."""
    fake = FakeTime(latency=20.0)
    case = FILES[cid]
    sim = Sim({cid: case}, clock=fake)
    arena = timed_arena(sim, fake)
    r = play_case(arena, case, log, mono=fake, wall=fake)
    assert r["ended"]
    assert not kinds(log, "timeout")
    if r["deal"]:
        assert r["must_haves_ok"]
    errors = [e for e in kinds(log, "error") if e.get("where") in ("policy", "setup")]
    assert not errors, errors


class Crash(BaseException):
    pass


def test_crash_mid_case_then_resume(log):
    fake = FakeTime(latency=10.0)
    sim = Sim(load_cases(), clock=fake)
    sim.rehearse(wait=0, cap=480, brk=30)
    app = create_app(sim)
    arena = timed_arena(sim, fake, app)
    case = next(c for c in arena.cases("final") if c["case_id"].endswith("S02"))
    from janteiros.runner.play import make_policy

    pol = make_policy("main", case, arena)
    sent = []
    orig = pol.next_move

    def crashing(t, budget):
        if len(sent) >= 2:
            raise Crash()  # like a killed process: not caught by the runner
        sent.append(1)
        return orig(t, budget)

    pol.next_move = crashing
    runner = CaseRunner(arena, case, pol, log.bind(case_id=case["case_id"]), mono=fake, wall=fake)
    with pytest.raises(Crash):
        runner.run()
    sid = runner.session_id
    r = play_case(arena, case, log, mono=fake, wall=fake)  # a fresh process: 409 -> resume
    assert r["session_id"] == sid and r["deal"], r
    assert any(e.get("resumed") for e in kinds(log, "case_open"))


def test_final_rehearsal_waves_in_parallel(log):
    fake = FakeTime(latency=5.0)
    sim = Sim(load_cases(), clock=fake)
    sim.rehearse(wait=30, cap=240, brk=30)
    arena = timed_arena(sim, fake)
    out = play_waves(arena, log, waves=2, wall=fake, sleep=fake.sleep, mono=fake, poll=2.0)
    assert len(out) == 6
    for cid, r in out.items():
        assert isinstance(r, dict) and r.get("deal"), (cid, r)
        assert r.get("end_reason") != "wave_cap"


def test_close_before_the_cap(log):
    """Slow replies and a short cap: the case still closes with a deal before the cap."""
    fake = FakeTime(latency=25.0)
    cases = load_cases()
    sim = Sim({"case:S02": cases["case:S02"]}, clock=fake)
    sim.rehearse(wait=0, cap=150, brk=30)
    arena = timed_arena(sim, fake)
    out = play_waves(arena, log, waves=2, wall=fake, sleep=fake.sleep, mono=fake, poll=2.0, parallel=1)
    assert len(out) == 2 and all(isinstance(r, dict) and r.get("deal") for r in out.values()), out
    assert any(e.get("what") == "close_before_cap" for e in kinds(log, "clock"))
