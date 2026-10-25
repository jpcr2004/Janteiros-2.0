"""jt eval: a fixed benchmark against the kit's arena-sim (S01-S03) and the sim2 suite, in this
process, with fixed seeds. One JSON line per run is appended to results/history.jsonl."""

import datetime as dt
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from . import flags
from .arena.client import Arena
from .events.log import EventLog, git_commit

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "results" / "history.jsonl"


class InProcess(httpx.BaseTransport):
    def __init__(self, app):
        self.tc = TestClient(app)

    def handle_request(self, request):
        r = self.tc.request(request.method, str(request.url), content=request.read(), headers=dict(request.headers))
        return httpx.Response(r.status_code, headers=r.headers, content=r.content)


def in_process_arena(sim, app):
    transport = InProcess(app)
    key = transport.tc.post("/v1/register", json={"team_name": f"jt{time.time_ns()}", "event_code": sim.code})
    return Arena("http://testserver", key.json()["api_key"], transport=transport)


def frozen_clock():
    return 1_000_000.0


def package_facts():
    import json as _json

    root = Path(os.environ.get("ARENA_DATA") or ROOT / "data" / "package")
    root = root if root.is_absolute() else ROOT / root
    return _json.loads((root / "facts.json").read_text(encoding="utf-8"))


def kit_suite():
    from arena_kit.sim import Sim, create_app, load_cases

    sim = Sim(load_cases(), facts=package_facts(), clock=frozen_clock)
    return "arena-sim", sim, create_app(sim), [(cid, None) for cid in sorted(sim.cases)]


def sim2_suite(quick=False):
    from .sim2.server import suite

    sim, app, items = suite(seed=1, quick=quick)
    return "sim2", sim, app, items


def sim3_suite(quick=False):
    from .sim3 import suite

    sim, app, items = suite(seed=1, quick=quick)
    return "sim3", sim, app, items


def robust(rows):
    """What a mean hides: the bad tail, deals, walks, broken rules (per suite)."""
    scores = sorted(r["score"] for r in rows)
    if not scores:
        return {}
    k = max(0, int(0.1 * len(scores)) - 1)
    return {
        "median": round(statistics.median(scores), 4),
        "p10": round(scores[k], 4),
        "worst": round(scores[0], 4),
        "deal_rate": round(sum(r["deal"] for r in rows) / len(rows), 4),
        "zeros": sum(s == 0.0 for s in scores),
        "no_deal_no_alt": sum(1 for r in rows if not r["deal"] and r.get("c_alt") is None),
    }


def play_one(agent, arena, case, log):
    if agent == "naive":
        from arena_kit.agents import NaiveAgent
        from arena_kit.play import RemoteTable, play

        return play(RemoteTable(arena), case["case_id"], NaiveAgent())
    from .runner.play import play_case

    return play_case(arena, case, log, policy="main")


def run_suite(name, sim, app, items, agent, log, parallel=8):
    def one(item):
        cid, seed = item
        arena = in_process_arena(sim, app)  # a key per case: a crashed session never blocks others
        case = sim.case_for(cid) if hasattr(sim, "case_for") else sim.cases[cid]
        t0 = time.monotonic()
        try:
            r = play_one(agent, arena, case, log)
            err = None
        except Exception as e:  # noqa: BLE001 - a crash is a result
            r, err = {}, repr(e)[:300]
        return {
            "case": cid,
            "seed": seed,
            "type": case.get("type"),
            "goal": case["buyer"].get("category_goal"),
            "bidders": len(case.get("suppliers") or []),
            "score": round(r.get("score") or 0.0, 4),
            "deal": bool(r.get("deal")),
            "c_deal": r.get("c_deal"),
            "c_best": r.get("c_best"),
            "c_open": r.get("c_open"),
            "c_alt": r.get("c_alt"),
            "families": r.get("families"),
            "rounds": r.get("rounds"),
            "end_reason": r.get("end_reason"),
            "crash": err,
            "seconds": round(time.monotonic() - t0, 2),
        }

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        return list(pool.map(one, items))


def count_events(log):
    timeouts = fallbacks = blocks = 0
    path = log.path
    for line in path.read_text().splitlines():
        e = json.loads(line)
        if e["kind"] == "timeout":
            timeouts += 1
        elif e["kind"] == "llm_call" and e.get("fallback"):
            fallbacks += 1
        elif e["kind"] == "validate" and not e.get("ok"):
            blocks += 1
    return timeouts, fallbacks, blocks


def main(note="", quick=False, suite="all", agent="jt", allow_dirty=False):
    if agent == "old":
        from .old_agent import main as old_main

        return old_main(note=note, quick=quick, suite=suite)
    commit, dirty = git_commit()
    if dirty and not allow_dirty:
        print("refusing to evaluate uncommitted code: commit first (or pass --allow-dirty for a scratch run)")
        return 2
    log = EventLog(meta={"mode": "eval", "agent": agent, "suite": suite})
    suites = []
    if suite in ("all", "kit"):
        suites.append(kit_suite())
    if suite in ("all", "sim2"):
        suites.append(sim2_suite(quick))
    if suite in ("all", "sim3"):
        suites.append(sim3_suite(quick))
    out = {}
    for name, sim, app, items in suites:
        rows = run_suite(name, sim, app, items, agent, log)
        out[name] = rows
    timeouts, fallbacks, blocks = count_events(log)
    rec = {
        "run_id": log.run_id,
        "ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "commit": commit,
        "dirty": dirty,
        "agent": agent,
        "model": None if os.environ.get("JT_NO_LLM") else os.environ.get("LLM_MODEL", "claude-opus-5-5"),
        "effort": None if os.environ.get("JT_NO_LLM") else os.environ.get("LLM_EFFORT", "low"),
        "note": note,
        "flags": flags.state(),
        "suites": {},
        "timeouts": timeouts,
        "llm_fallbacks": fallbacks,
        "validator_blocks": blocks,
    }
    for name, rows in out.items():
        scores = [r["score"] for r in rows]
        rec["suites"][name] = {
            "seed": 1,
            "mean": round(statistics.mean(scores), 4) if scores else None,
            "n": len(rows),
            "deals": sum(r["deal"] for r in rows),
            "crashes": sum(bool(r["crash"]) for r in rows),
            **robust(rows),
            "cases": rows,
        }
    HISTORY.parent.mkdir(exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    prev = previous(agent, rec["run_id"])
    for name, s in rec["suites"].items():
        delta = ""
        if prev and name in prev["suites"] and prev["suites"][name]["mean"] is not None:
            delta = f"  (prev {prev['suites'][name]['mean']:.4f} @ {prev['commit']}, {s['mean'] - prev['suites'][name]['mean']:+.4f})"
        print(f"{name}: mean {s['mean']:.4f} over {s['n']} | deals {s['deals']} | crashes {s['crashes']}{delta}")
        print(
            f"   median {s.get('median')} | p10 {s.get('p10')} | worst {s.get('worst')} | deal rate "
            f"{s.get('deal_rate')} | zeros {s.get('zeros')} | no deal without alternative {s.get('no_deal_no_alt')}"
        )
        if name == "sim3":
            fam = {}
            for r in s["cases"]:
                for f in set((r.get("families") or {}).values()) or {"?"}:
                    fam.setdefault(f, []).append(r["score"])
            print(
                "   by family: "
                + ", ".join(f"{f} {statistics.mean(v):.3f} (n={len(v)})" for f, v in sorted(fam.items()))
            )
            continue
        for r in s["cases"]:
            print(f"   {r['case']:<22} {r['type'] or '':<15} {r['goal'] or '':<14} {r['score']:.3f} "
                  f"{'deal' if r['deal'] else 'NO DEAL'} r={r['rounds']} {r['crash'] or ''}")  # fmt: skip
    print(f"timeouts {timeouts} | llm fallbacks {fallbacks} | validator blocks {blocks} | run {log.run_id}")
    return 0


def previous(agent, run_id):
    if not HISTORY.is_file():
        return None
    rows = [json.loads(x) for x in HISTORY.read_text().splitlines() if x.strip()]
    rows = [r for r in rows if r.get("agent") == agent and r["run_id"] != run_id]
    return rows[-1] if rows else None


if __name__ == "__main__":
    sys.exit(main())
