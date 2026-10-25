"""Recorded arena results (raw session JSON files) -> tests/fixtures/results.json."""

import json
import pathlib
import sys

SRC = pathlib.Path(
    sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: python scripts/extract_results.py <folder of recorded session JSON files>")
).expanduser()
out = []
for p in sorted(SRC.rglob("*.json")):
    try:
        d = json.loads(p.read_text())
    except ValueError:
        continue
    r = d.get("result") or {}
    if not r.get("deal") or not r.get("cost_breakdown"):
        continue
    keep = ("supplier_id", "terms", "c_open", "c_deal", "c_alt", "c_best", "score", "must_haves_ok", "cost_breakdown")
    out.append({"session_id": d["session_id"], "case_id": d["case_id"], **{k: r.get(k) for k in keep}})
pathlib.Path("tests/fixtures/results.json").write_text(json.dumps(out, indent=1))
print(len(out), "results")
