"""The Live Final: poll the case list through the 403 period, start every open case of a wave at
once, close each before the wave's cap, retry a crashed case while its wave has time, and run
unattended until the last wave has closed. A restarted process picks running sessions up again
(a second POST is a 409 naming the session). Never reads the forbidden endpoints (the client
refuses them)."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..arena.client import ArenaError
from .play import play_case

POLL_S = 2.0
RETRY_MIN_LEFT_S = 60.0  # a crashed case is retried while its wave has at least this much left
MAX_TRIES = 6


def play_waves(
    arena,
    log,
    policy="main",
    waves=5,
    parallel=10,
    poll=POLL_S,
    wall=time.time,
    sleep=time.sleep,
    mono=time.monotonic,
    stop=None,
):
    caps: dict = {}  # wave -> its cap as wall time, from the latest seconds_left
    closed: set = set()
    seen_waves: set = set()
    results: dict = {}
    running: dict = {}  # case_id -> future
    tries: dict = {}
    lock = threading.Lock()

    def one(case):
        cid, wave = case["case_id"], case.get("wave")
        try:
            r = play_case(arena, case, log, policy, deadline=lambda: caps.get(wave), final=True, mono=mono,
                          wall=wall)  # fmt: skip
            with lock:
                results[cid] = r
            return r
        except Exception as e:  # noqa: BLE001 - one broken case must not stop the others
            log.emit("error", case_id=cid, where="final_case", error=repr(e)[:500], tries=tries.get(cid))
            return e

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        while True:
            if stop is not None and stop():
                break
            try:
                cases = arena.cases("final")
            except ArenaError as e:
                if e.status == 403:  # no wave has started yet
                    cases = []
                else:
                    log.emit("error", where="poll", status=e.status, error=str(e.detail)[:300])
                    sleep(poll)
                    continue
            except Exception as e:  # noqa: BLE001 - venue Wi-Fi: keep polling
                log.emit("error", where="poll", error=repr(e)[:300])
                sleep(poll)
                continue
            now = wall()
            for case in cases:
                w = case.get("wave")
                if w not in seen_waves:
                    seen_waves.add(w)
                    log.emit("wave", wave=w, theme=case.get("theme"), open=case.get("open"),
                             seconds_left=case.get("seconds_left"))  # fmt: skip
                if not case.get("open"):
                    if w not in closed:
                        closed.add(w)
                        log.emit("wave", wave=w, closed=True)
                    continue
                if case.get("seconds_left") is not None:
                    caps[w] = now + float(case["seconds_left"])
                cid = case["case_id"]
                fut = running.get(cid)
                if cid in results:
                    continue
                left = caps.get(w, now) - now
                if fut is None or (fut.done() and isinstance(fut.result(), Exception)):
                    if fut is not None and (left < RETRY_MIN_LEFT_S or tries.get(cid, 0) >= MAX_TRIES):
                        continue
                    tries[cid] = tries.get(cid, 0) + 1
                    log.emit("case_start", case_id=cid, wave=w, theme=case.get("theme"), seconds_left=left,
                             attempt=tries[cid], suppliers=case.get("suppliers"), type=case.get("type"))  # fmt: skip
                    running[cid] = pool.submit(one, case)
            if len(closed) >= waves and all(f.done() for f in running.values()):
                break
            sleep(poll)
    return {cid: results.get(cid) or (f.result() if f.done() else None) for cid, f in running.items()}
