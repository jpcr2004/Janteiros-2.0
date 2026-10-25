"""Entry points: one case, a whole set in parallel, the Live Final (see waves.py)."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import flags
from ..arena.client import Arena, ArenaError
from ..events.log import EventLog
from .session import CaseRunner, Ended

_world = None
_world_lock = threading.Lock()


def world():
    """The data package + Sunday release + case facts index, loaded once per process."""
    global _world
    with _world_lock:
        if _world is None:
            try:
                from ..world.index import World

                root = Path(os.environ.get("ARENA_DATA") or "data/package")
                if not root.is_absolute():
                    root = Path(__file__).resolve().parents[2] / root
                _world = World.load(root)
            except Exception:  # noqa: BLE001 - play without data rather than not at all
                _world = False
        return _world or None


def make_policy(name, case, arena=None):
    w = world()
    if w is not None:
        w = w.for_case(case)
    if name == "trivial":
        from ..policy.trivial import Trivial

        return Trivial(case, w)
    from ..llm.client import reader_from_env, writer_from_env
    from ..policy.negotiator import Negotiator

    local = arena.local if arena is not None else True
    reader = reader_from_env() if not os.environ.get("JT_NO_READER") else None
    # Template wording is the safe default.  The optional paragraph requires an explicit opt-in.
    writer = (
        writer_from_env() if os.environ.get("JT_LLM_WRITER") == "1" and not os.environ.get("JT_NO_WRITER") else None
    )
    return Negotiator(case, w, writer, learn=not local, reader=reader)


def play_case(arena, case, log, policy="main", deadline=None, final=False, **clocks):
    clog = log.bind(case_id=case["case_id"])
    pol = make_policy(policy, case, arena)
    runner = CaseRunner(arena, case, pol, clog, deadline=deadline, final=final, **clocks)
    try:
        return runner.run()
    except Ended as e:
        return runner.done(e.result)


def new_arena(log=None):
    def on_call(method, path, status, seconds):
        if log is not None and (method == "POST" or status >= 400):
            log.emit("http", method=method, path=path, status=status, seconds=round(seconds, 3))

    return Arena(on_call=on_call)


def play_case_id(case_id, policy="main", log=None):
    log = log or EventLog(
        meta={"mode": "play", "case": case_id, "arena": os.environ.get("ARENA_URL"), "flags": flags.state()}
    )
    arena = new_arena(log)
    case = next((c for s in ("sandbox", "practice") for c in _cases(arena, s) if c["case_id"] == case_id), None)
    if case is None:
        case = {"case_id": case_id}
    return play_case(arena, case, log, policy)


def _cases(arena, set_):
    try:
        return arena.cases(set_)
    except ArenaError:
        return []


def run_set(set_, policy="main", parallel=10, waves=5, only=None, log=None):
    log = log or EventLog(
        meta={"mode": "run", "set": set_, "arena": os.environ.get("ARENA_URL"), "flags": flags.state()}
    )
    arena = new_arena(log)
    if set_ == "final":
        from .waves import play_waves

        return play_waves(arena, log, policy=policy, waves=waves)
    cases = [c for c in arena.cases(set_) if c.get("hosted", True)]
    if only:
        cases = [c for c in cases if c["case_id"] in only]

    def one(case):
        try:
            return case["case_id"], play_case(arena, case, log, policy)
        except Exception as e:  # noqa: BLE001 - one broken case must not stop the others
            log.emit("error", case_id=case["case_id"], where="case", error=repr(e)[:500])
            return case["case_id"], e

    with ThreadPoolExecutor(max_workers=min(parallel, 10)) as pool:
        return dict(pool.map(one, cases))
