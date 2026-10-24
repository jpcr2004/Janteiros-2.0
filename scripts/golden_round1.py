"""Round-1 messages on every recorded real opening (data/sim2/paths.json), template text, as JSON.
`--write` records them in tests/fixtures/golden_round1.json (do it with JT_X_ALL=0 on baseline
code only); tests/test_golden.py compares the current code with all switches off against it."""

import json
import os
import pathlib
import sys

os.environ["JT_NO_LLM"] = "1"
from janteiros.events.log import NullLog  # noqa: E402
from janteiros.runner.play import make_policy  # noqa: E402
from janteiros.runner.state import Thread  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/golden_round1.json"


def openings():
    cases = {c["case_id"]: c for f in ("practice", "sandbox") for c in json.loads((ROOT / f"data/cases/{f}.json").read_text())}
    paths = json.loads((ROOT / "data/sim2/paths.json").read_text())
    out = {}
    for key, rec in paths.items():
        cid, sid = key.split("|")
        first = next((s["moves"][0] for s in rec["sessions"] if s["moves"] and s["moves"][0].get("terms")), None)
        if first and cid in cases:
            out.setdefault(cid, {})[sid] = first
    return cases, out


def round1():
    cases, ops = openings()
    out = {}
    for cid in sorted(ops):
        case = cases[cid]
        threads = {}
        for sid, op in ops[cid].items():
            t = Thread(sid)
            t.messages = [{"from": f"sales@{sid[4:]}", "status": op.get("status") or "open", "body": op.get("text", ""),
                           "signals": op.get("signals"), "attachments": [],
                           "term_sheets": [{"term_sheet_id": f"offer:{sid[4:]}-0", "terms": op["terms"]}]}]  # fmt: skip
            threads[sid] = t
        pol = make_policy("main", case)
        pol.setup(threads, NullLog())
        for sid, t in sorted(threads.items()):
            mv = pol.next_move(t, 0.0)
            if mv is None:
                out[f"{cid}|{sid}"] = None
                continue
            msg = pol.compose(t, mv, 0.0)
            out[f"{cid}|{sid}"] = {"sheets": mv.sheets, "refs": msg["refs"], "body": msg["body"],
                                   "attachments": [a["name"] for a in msg.get("attachments") or []]}  # fmt: skip
    return out


if __name__ == "__main__":
    got = round1()
    if "--write" in sys.argv:
        FIXTURE.write_text(json.dumps(got, indent=1, sort_keys=True))
        print(len(got), "round-1 messages written to", FIXTURE)
    else:
        print(json.dumps(got, indent=1, sort_keys=True))
