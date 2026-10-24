"""Append a real arena run (runs/<id>/sessions) to results/history.jsonl.
uv run python scripts/record_arena.py <run_id> <commit> "<note>" [--flags JT_X_A=1,JT_NO_LLM=1]"""

import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
run_id, commit, note = sys.argv[1:4]
flags = sys.argv[sys.argv.index("--flags") + 1] if "--flags" in sys.argv else ""
cases = {c["case_id"]: c for f in ("practice", "sandbox") for c in json.loads((ROOT / f"data/cases/{f}.json").read_text())}
rows = []
for p in sorted((ROOT / "runs" / run_id / "sessions").glob("*.json")):
    s = json.loads(p.read_text())["session"]
    r = s.get("result") or {}
    case = cases.get(s["case_id"], {})
    rows.append({"case": s["case_id"], "type": case.get("type"), "goal": (case.get("buyer") or {}).get("category_goal"),
                 "score": round(r.get("score") or 0.0, 4), "deal": bool(r.get("deal")), "c_deal": r.get("c_deal"),
                 "c_best": r.get("c_best"), "c_open": r.get("c_open"), "rounds": s.get("rounds"),
                 "end_reason": s.get("end_reason"), "crash": None})  # fmt: skip
events = ROOT / "runs" / run_id / "events.jsonl"
timeouts = sum('"timeout"' in line for line in events.open()) if events.exists() else None
mean = round(sum(r["score"] for r in rows) / len(rows), 4) if rows else 0.0
rec = {"run_id": run_id, "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds"),
       "commit": commit, "dirty": False, "agent": "jt", "model": None, "effort": None, "note": note, "flags": flags,
       "suites": {"arena-practice": {"seed": None, "mean": mean, "n": len(rows), "deals": sum(r["deal"] for r in rows),
                                     "crashes": 0, "cases": rows}}, "timeouts": timeouts}  # fmt: skip
with (ROOT / "results/history.jsonl").open("a") as f:
    f.write(json.dumps(rec) + "\n")
print(run_id, mean, " ".join(f"{r['case'][5:]} {r['score']:.3f}" for r in rows))
