"""Stress the agent with mutated case files on sim2 (in-process, frozen clock):
python scripts/stress.py [n] [seed]. Reports crashes, policy errors, validator blocks, deals that
break a must-have or cost more than the alternative, walks without an alternative."""

import copy
import json
import math
import os
import random
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("JT_NO_LLM", "1")
from arena_kit.sim import create_app  # noqa: E402

from janteiros.econ.cost import c_alt  # noqa: E402
from janteiros.evaluate import in_process_arena  # noqa: E402
from janteiros.events.log import EventLog, read_events  # noqa: E402
from janteiros.runner.play import play_case  # noqa: E402
from janteiros.sim2.server import base_cases, build_sim  # noqa: E402

GOALS = {"stabilise": 10, "secure_supply": 25, "quality_first": 8, "leverage": 3, "routine": 5}
FIELD_OPTIONS = {
    "payment_days": [14, 30, 45, 60, 90],
    "lead_time_days": [21, 28, 35, 42, 56],
    "contract_months": [12, 24, 36],
    "incoterm": ["EXW", "FCA", "DAP"],
    "ppm_commitment": [300, 800, 1500, 2500],
    "chargeback_eur_per_complaint": [0, 100, 250, 500],
}


def mutate(case, rng, i):
    c = copy.deepcopy(case)
    c["case_id"] = f"{case['case_id']}x{i}"
    c["base_case_id"] = case["case_id"]
    b = c["buyer"]
    kind = rng.choice(["goal", "must", "alt", "rounds", "field", "unknown", "combo"])
    kinds = [kind] if kind != "combo" else rng.sample(["goal", "must", "alt", "rounds", "field"], 3)
    for k in kinds:
        if k == "goal":
            g = rng.choice(list(GOALS))
            b["category_goal"], b["fallback_risk_pct"] = g, GOALS[g]
            if g in ("stabilise", "secure_supply"):
                b["sustainable_margin_pct"] = rng.choice([0.5, 2, 4, 6, 8])
                b["relationship_eur_per_point"] = round(
                    rng.uniform(0.004, 0.02)
                    * b["need_per_year"]
                    * (b.get("current_unit_price") or b["fallback_unit_price"])
                )
            else:
                b.pop("sustainable_margin_pct", None)
                b.pop("relationship_eur_per_point", None)
        if k == "must":
            neg = [f for f in c["negotiable"] if f in ("lead_time_days", "payment_days", "ppm_commitment")]
            if neg:
                f = rng.choice(neg)
                opts = c["term_options"].get(f) or FIELD_OPTIONS[f]
                b.setdefault("must_haves", []).append(
                    {"field": f, "op": "<=" if f != "payment_days" else ">=", "value": sorted(opts)[len(opts) // 2]}
                )
        if k == "alt":
            choice = rng.choice(["none", "quote", "current"])
            if choice == "none":
                b["alternative"] = {"kind": "none", "ref": None, "terms": {}}
            else:
                base = b.get("current_unit_price") or b["fallback_unit_price"]
                terms = dict((b.get("alternative") or {}).get("terms") or {})
                terms.update(unit_price=round(base * rng.uniform(1.0, 1.12), 2), volume_per_year=b["need_per_year"],
                             contract_months=round(12 * b["horizon_years"]))  # fmt: skip
                b["alternative"] = {
                    "kind": "quote" if choice == "quote" else "current_contract",
                    "ref": None,
                    "terms": terms,
                }
        if k == "rounds":
            c["round_limit"] = rng.choice([4, 6, 8])
        if k == "field":
            free = [f for f in FIELD_OPTIONS if f not in c["negotiable"]]
            if free:
                f = rng.choice(free)
                c["negotiable"].append(f)
                c["term_options"][f] = FIELD_OPTIONS[f]
                c.get("fixed_terms", {}).pop(f, None)
        if k == "unknown":
            c["suppliers"] = [f"sup:9{rng.randint(1000, 9999)}" for _ in c["suppliers"]]
            c["synthetic_suppliers"] = True
            c["trigger_doc"] = None
    return c


def main(n=200, seed=3):
    rng = random.Random(seed)
    bases = list(base_cases().values())
    cases = {}
    for i in range(n):
        m = mutate(rng.choice(bases), rng, i)
        cases[m["case_id"]] = m
    sim = build_sim(seed=seed, clock=lambda: 1_000_000.0)
    sim.cases = cases
    app = create_app(sim)
    log = EventLog(meta={"mode": "stress", "n": n})
    problems = Counter()
    rows = []

    def one(cid):
        try:
            r = play_case(in_process_arena(sim, app), cases[cid], log)
            return cid, r, None
        except Exception as e:  # noqa: BLE001
            return cid, {}, repr(e)

    with ThreadPoolExecutor(8) as pool:
        for cid, r, err in pool.map(one, list(cases)):
            case = cases[cid]
            alt = c_alt(case)
            if err:
                problems["crash"] += 1
                print("CRASH", cid, err)
            elif r.get("deal"):
                if not r.get("must_haves_ok"):
                    problems["must-have broken"] += 1
                    print("MUST", cid)
                if math.isfinite(alt) and r["c_deal"] >= alt:
                    problems["above alternative"] += 1
                    print("ABOVE ALT", cid, r["c_deal"], alt)
            elif not math.isfinite(alt):
                problems["no deal without alternative"] += 1
                print("NO DEAL, no alt", cid, r.get("end_reason"))
            rows.append(r.get("score") or 0.0)
    for e in read_events(log.run_id):
        if e["kind"] == "error" and e.get("where") in ("policy", "setup", "claims", "profile", "loop"):
            problems[f"error {e['where']}"] += 1
            if problems[f"error {e['where']}"] <= 3:
                print("ERROR", e.get("case_id"), e.get("where"), e.get("error"))
        if e["kind"] == "validate" and not e.get("ok"):
            problems["validator block"] += 1
            if problems["validator block"] <= 5:
                print("BLOCK", e.get("case_id"), e.get("problems"))
    print(
        f"{n} variants: mean score {sum(rows) / len(rows):.3f}; problems: {dict(problems) or 'none'}; run {log.run_id}"
    )
    json.dumps(problems)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 200, int(sys.argv[2]) if len(sys.argv) > 2 else 3)
