"""Print a run's events compactly: python scripts/show_run.py <run dir or events.jsonl> [case_id]"""

import json
import pathlib
import sys

p = pathlib.Path(sys.argv[1])
p = p / "events.jsonl" if p.is_dir() else p
only = sys.argv[2] if len(sys.argv) > 2 else None
for line in p.read_text().splitlines():
    e = json.loads(line)
    if only and e.get("case_id") not in (only, None):
        continue
    k = e["kind"]
    s = e.get("supplier_id") or ""
    if k == "quote":
        print(f"{e['t']:7.1f} QUOTE {s} {e.get('terms')} C={e.get('c')}")
    elif k == "plan" and "round" in e:
        print(
            f"{e['t']:7.1f} PLAN  {s} r{e.get('round')} target={e.get('target_v') or e.get('target_c')} {e.get('reason')} "
            f"standing={e.get('standing_c')} floor={e.get('floor_c')} cmin={e.get('c_min')}"
        )
        for sh in e.get("sheets", []):
            print(f"            sheet {sh['terms']} C={sh['c']:.0f} v={sh['v']:.3f}")
    elif k == "send":
        print(f"{e['t']:7.1f} SEND  {s} r{e.get('round')} {e.get('reason')} C={e.get('c')} claims={e.get('claims')}")
    elif k == "reply":
        print(f"{e['t']:7.1f} REPLY {s} {e.get('status')} {e.get('terms')} C={e.get('c')} {e.get('claim_results')}")
    elif k in ("accept", "walk", "timeout", "error", "validate") and (k != "validate" or not e.get("ok")):
        print(
            f"{e['t']:7.1f} {k.upper():5} {s} {e.get('reason') or e.get('detail') or e.get('problems') or e.get('error')}"
        )
    elif k == "model_update" and (e.get("stop") or e.get("pruned")):
        print(f"{e['t']:7.1f} STOP  {s} {e.get('stop') or 'pruned'}")
    elif k == "result":
        r = e.get("result") or {}
        print(
            f"{e['t']:7.1f} RESULT deal={r.get('deal')} score={r.get('score')} c_deal={r.get('c_deal')} "
            f"c_best={r.get('c_best')} c_open={r.get('c_open')} terms={r.get('terms')}"
        )
