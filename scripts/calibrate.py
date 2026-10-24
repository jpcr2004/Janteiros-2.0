"""sim2 calibration report: (1) C_open and C_best per case against the arena's recorded values;
(2) replay of recorded real sessions: our recorded buyer messages fed to the sim2 persona, its
counters against the real supplier's counters, round by round."""

import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from janteiros.econ.cost import closing  # noqa: E402
from janteiros.sim2.server import build_sim  # noqa: E402

hist = [json.loads(x) for x in (ROOT / "results/history.jsonl").read_text().splitlines()]
last = next(h for h in reversed(hist) if "sim2" in h["suites"])
fx = json.loads((ROOT / "tests/fixtures/results.json").read_text())
arena = {}
for r in fx:
    a = arena.setdefault(r["case_id"], {"c_open": r["c_open"], "c_best": r["c_best"]})
    a["c_best"] = min(a["c_best"], r["c_best"])
print("case      arena C_open   sim2 C_open   d%     arena C_best   sim2 C_best   d%")
devs = []
for row in last["suites"]["sim2"]["cases"]:
    cid = row["case"]
    if cid not in arena or not row.get("c_open"):
        continue
    a = arena[cid]
    d1 = (row["c_open"] / a["c_open"] - 1) * 100
    d2 = (row["c_best"] / a["c_best"] - 1) * 100
    devs.append(abs(d2))
    print(
        f"{cid:9} {a['c_open']:13.0f} {row['c_open']:13.0f} {d1:6.2f}  {a['c_best']:13.0f} {row['c_best']:13.0f} {d2:6.2f}"
    )
print(f"mean |d C_best| {statistics.mean(devs):.2f} %")

# (2) replay recorded sessions against the persona
paths = json.loads((ROOT / "data/sim2/paths.json").read_text())
sim = build_sim(seed=1, clock=lambda: 1_000_000.0, no_clock=True)
import hashlib  # noqa: E402

key = hashlib.sha256(b"cal").hexdigest()
sim.teams[key] = "cal"
errs, first_errs, n_sessions = [], [], 0
per_case = {}
for k, v in paths.items():
    cid, sup = k.split("|")
    if cid not in sim.cases or len(sim.cases[cid]["suppliers"]) > 1:
        continue
    for sess in v["sessions"][:12]:
        buyer = [b for b in sess["buyer"] if b.get("type") == "message"]
        theirs = [m for m in sess["moves"] if m.get("terms")]
        if len(theirs) < 2 or not buyer:
            continue
        r = sim.open(key, cid)
        sid = r["session_id"]
        n_sessions += 1
        for i, b in enumerate(buyer):
            if i + 1 >= len(theirs):
                break
            sheets = [s for s in b["sheets"] if s]
            case = sim.cases[cid]
            if any(set(s) != set(case["negotiable"]) for s in sheets):
                break
            try:
                reply = sim.message(
                    key, sid, sup, {"body": b["body"], "refs": b["refs"], "term_sheets": [{"terms": s} for s in sheets]}
                )
            except Exception as e:  # noqa: BLE001
                print("replay stop", cid, e)
                break
            real = theirs[i + 1]["terms"].get("unit_price")
            got = (reply.get("term_sheets") or [{}])[0].get("terms", {}).get("unit_price")
            if real and got:
                e = (got / real - 1) * 100
                errs.append(abs(e))
                per_case.setdefault(cid, []).append(e)
                if i == 0:
                    first_errs.append(abs(e))
            if reply.get("status") in ("agreed", "walked_away") or sim.sessions[sid]["status"] != "open":
                break
        if sim.sessions[sid]["status"] == "open":
            sim._end(sim.sessions[sid], "no_deal", None)
print(f"\nreplay: {n_sessions} recorded single-supplier sessions, {len(errs)} counters")
print(f"mean |price error| {statistics.mean(errs):.2f} %, first counter {statistics.mean(first_errs):.2f} %")
for cid, es in sorted(per_case.items()):
    print(
        f"   {cid:9} n={len(es):3} mean {statistics.mean(es):+6.2f} %  mean|.| {statistics.mean(map(abs, es)):5.2f} %"
    )
_ = closing
