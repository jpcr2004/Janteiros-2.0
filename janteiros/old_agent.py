"""jt eval --agent old: the old agent (a clean clone of Janteiros--Tacto @ claude-sim, path in
JT_OLD_AGENT) plays the same simulators through its own CLI, against a local server this process
starts. Results are read back from the simulator and recorded like ours."""

import datetime as dt
import json
import os
import socket
import statistics
import subprocess
import threading
import time
from pathlib import Path

import uvicorn

from .evaluate import HISTORY, kit_suite, sim2_suite

OLD = Path(os.environ.get("JT_OLD_AGENT", "")).expanduser()


def serve(app):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server, f"http://127.0.0.1:{port}"


def run_old(name, sim, app, items, no_llm=True, parallel=8):
    import httpx

    server, url = serve(app)
    try:
        key = httpx.post(
            f"{url}/v1/register", json={"team_name": f"old{time.time_ns()}", "event_code": sim.code}
        ).json()["api_key"]
        env = {**os.environ, "ARENA_URL": url, "ARENA_API_KEY": key, "ARENA_DATA": str(OLD / "data" / "package")}
        ids = ",".join(cid.split(":", 1)[1] for cid, _ in items)
        cmd = ["uv", "run", "buyer", "run", "--set", "practice", "--parallel", str(parallel), "--cases", ids]
        if no_llm:
            cmd.append("--no-llm")
        t0 = time.monotonic()
        p = subprocess.run(cmd, cwd=OLD, env=env, capture_output=True, text=True, timeout=3600)
        took = time.monotonic() - t0
        sessions = httpx.get(f"{url}/v1/sessions", params={"set": "practice"}, headers={"X-API-Key": key}).json()
        rows = []
        by_case = {}
        for s in sessions:
            st = httpx.get(f"{url}/v1/sessions/{s['session_id']}", headers={"X-API-Key": key}).json()
            r = st.get("result") or {}
            by_case[s["case_id"]] = {
                "case": s["case_id"], "score": round(r.get("score") or 0.0, 4), "deal": bool(r.get("deal")),
                "c_deal": r.get("c_deal"), "c_best": r.get("c_best"), "c_open": r.get("c_open"),
                "rounds": st.get("rounds"), "end_reason": st.get("end_reason"), "crash": None,
            }  # fmt: skip
        for cid, _ in items:
            case = sim.case_for(cid) if hasattr(sim, "case_for") else sim.cases[cid]
            row = by_case.get(cid) or {"case": cid, "score": 0.0, "deal": False, "crash": "not played"}
            row.update(type=case.get("type"), goal=case["buyer"].get("category_goal"))
            rows.append(row)
        return rows, took, p.returncode, p.stderr[-2000:]
    finally:
        server.should_exit = True


def main(note="", quick=False, suite="all"):
    if not (OLD / "buyer").is_dir():
        print("set JT_OLD_AGENT to a clean clone of Janteiros--Tacto (branch claude-sim)")
        return 2
    no_llm = bool(os.environ.get("JT_NO_LLM"))
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=OLD, capture_output=True, text=True
    ).stdout.strip()
    rec = {"run_id": "old-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S"), "ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
           "commit": f"old@{sha}", "dirty": False, "agent": "old", "model": None if no_llm else os.environ.get("LLM_MODEL"),
           "effort": None if no_llm else os.environ.get("LLM_EFFORT", "low"), "note": note, "suites": {}}  # fmt: skip
    suites = []
    if suite in ("all", "kit"):
        name, sim, app, items = kit_suite()
        sim.clock = time.time  # a real server: the old agent runs in real time
        suites.append((name, sim, app, items))
    if suite in ("all", "sim2"):
        name, sim, app, items = sim2_suite(quick)
        sim.clock = time.time
        suites.append((name, sim, app, items))
    for name, sim, app, items in suites:
        rows, took, code, err = run_old(name, sim, app, items, no_llm)
        scores = [r["score"] for r in rows]
        rec["suites"][name] = {"seed": 1, "mean": round(statistics.mean(scores), 4), "n": len(rows),
                               "deals": sum(r["deal"] for r in rows), "crashes": sum(bool(r.get("crash")) for r in rows),
                               "cases": rows, "seconds": round(took, 1), "exit": code}  # fmt: skip
        print(f"{name}: old agent mean {rec['suites'][name]['mean']:.4f} over {len(rows)} (exit {code}, {took:.0f}s)")
        for r in rows:
            print(
                f"   {r['case']:<22} {r.get('type') or '':<15} {r['score']:.3f} {'deal' if r['deal'] else 'NO DEAL'} {r.get('crash') or ''}"
            )
        if code:
            print(err)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return 0
