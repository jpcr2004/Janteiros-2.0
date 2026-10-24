"""Real supplier price paths from the recorded arena sessions -> data/sim2/paths.json:
per (case, supplier): opening terms, every counter (round, price, status, terms), our sheets'
prices, claim results, and the arena's c_best / c_open per session."""

import json
import pathlib
import sys
from collections import defaultdict

SRC = pathlib.Path(
    sys.argv[1] if len(sys.argv) > 1 else "~/IdeaProjects/ehl-final-procurecabana/logs/sessions"
).expanduser()
out = defaultdict(lambda: {"sessions": []})
for p in sorted(SRC.rglob("*.json")):
    try:
        d = json.loads(p.read_text())
    except ValueError:
        continue
    r = d.get("result") or {}
    trace = r.get("trace") or []
    if not trace:
        continue
    case = d["case_id"]
    per = defaultdict(list)
    for step in trace:
        mv = step.get("move") or {}
        actor = step.get("actor")
        if actor == "buyer":
            sid = mv.get("supplier_id") or (mv.get("to"))
            per["_buyer"].append(
                {
                    "round": step.get("round"),
                    "type": mv.get("type"),
                    "sheets": [s.get("terms") for s in mv.get("term_sheets") or []],
                    "refs": mv.get("refs") or [],
                    "body": (mv.get("body") or "")[:3000],
                    "to": sid,
                }
            )
        else:
            per[actor].append(
                {
                    "round": step.get("round"),
                    "status": mv.get("status"),
                    "terms": mv.get("terms"),
                    "claim_results": mv.get("claim_results"),
                    "signals": mv.get("signals"),
                    "missing": mv.get("missing_fields"),
                    "reasons": step.get("reasons"),
                    "text": (mv.get("text") or "")[:2000],
                }
            )
    for sid, moves in per.items():
        if sid == "_buyer":
            continue
        out[f"{case}|{sid}"]["sessions"].append(
            {
                "session_id": d["session_id"],
                "moves": moves,
                "buyer": per["_buyer"],
                "c_best": r.get("c_best"),
                "c_open": r.get("c_open"),
                "c_deal": r.get("c_deal"),
                "score": r.get("score"),
                "deal_supplier": r.get("supplier_id"),
                "terms": r.get("terms"),
                "relationship": (r.get("cost_breakdown") or {}).get("relationship"),
            }
        )
pathlib.Path("data/sim2").mkdir(parents=True, exist_ok=True)
pathlib.Path("data/sim2/paths.json").write_text(json.dumps(out, indent=0))
for k, v in sorted(out.items()):
    ss = v["sessions"]
    opens = [s["moves"][0]["terms"].get("unit_price") for s in ss if s["moves"] and s["moves"][0]["terms"]]
    lows = [min((m["terms"] or {}).get("unit_price") or 1e9 for m in s["moves"]) for s in ss]
    firsts = [s["moves"][1]["terms"].get("unit_price") for s in ss if len(s["moves"]) > 1 and s["moves"][1]["terms"]]
    print(
        f"{k:28} n={len(ss):3} open={opens[0] if opens else None} first={sorted(firsts)[:1]}..{sorted(firsts)[-1:]} low={min(lows):.2f}"
    )
