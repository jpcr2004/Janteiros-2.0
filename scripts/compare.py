"""Compare the last jt eval with the previous jt eval and the old agent, case by case."""

import json
import pathlib

h = [json.loads(x) for x in (pathlib.Path(__file__).parents[1] / "results/history.jsonl").read_text().splitlines()]
jt = [x for x in h if x["agent"] == "jt"]
old = [x for x in h if x["agent"] == "old"]
cur, prev = jt[-1], (jt[-2] if len(jt) > 1 else None)
o = old[-1] if old else None
for name, s in cur["suites"].items():
    po = {c["case"]: c for c in (prev or {}).get("suites", {}).get(name, {}).get("cases", [])}
    oo = {c["case"]: c for c in (o or {}).get("suites", {}).get(name, {}).get("cases", [])}
    print(
        f"{name}: now {s['mean']:.4f}  prev {(prev or {}).get('suites', {}).get(name, {}).get('mean')}  old {(o or {}).get('suites', {}).get(name, {}).get('mean')}"
    )
    for c in s["cases"]:
        p = po.get(c["case"], {}).get("score")
        q = oo.get(c["case"], {}).get("score")
        flag = "  <" if q is not None and c["score"] < q - 0.02 else ""
        print(
            f"  {c['case']:12} {c.get('type') or '':15} now {c['score']:.3f}  prev {p if p is None else f'{p:.3f}'}  old {q if q is None else f'{q:.3f}'} r={c.get('rounds')}{flag}"
        )
