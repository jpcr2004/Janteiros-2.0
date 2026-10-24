"""Round 1 against every recorded real opening (no arena): plan, compose, validate. Reads the
recorded openings kept in this repo (data/sim2/paths.json)."""

import json
import os
import pathlib

os.environ.setdefault("JT_NO_LLM", "1")
from janteiros.events.log import NullLog  # noqa: E402
from janteiros.runner.play import make_policy  # noqa: E402
from janteiros.runner.state import Thread  # noqa: E402
from janteiros.validate.check import validate  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
cases = {
    c["case_id"]: c for f in ("practice", "sandbox") for c in json.loads((ROOT / f"data/cases/{f}.json").read_text())
}
paths = json.loads((ROOT / "data/sim2/paths.json").read_text())
openings = {}  # case -> {supplier: its opening move}
for key, rec in paths.items():
    cid, sid = key.split("|")
    first = next((s["moves"][0] for s in rec["sessions"] if s["moves"] and s["moves"][0].get("terms")), None)
    if first and cid in cases:
        openings.setdefault(cid, {})[sid] = first
n, problems = 0, 0
for cid in sorted(openings, key=lambda c: (c[5], int(c[6:]) if c[6:].isdigit() else 0)):
    case = cases[cid]
    threads = {}
    for sid, op in openings[cid].items():
        t = Thread(sid)
        t.messages = [{"from": f"sales@{sid[4:]}", "status": op.get("status") or "open", "body": op.get("text", ""),
                       "signals": op.get("signals"), "attachments": [],
                       "term_sheets": [{"term_sheet_id": f"offer:{sid[4:]}-0", "terms": op["terms"]}]}]  # fmt: skip
        threads[sid] = t
    pol = make_policy("main", case)
    pol.setup(threads, NullLog())
    for sid, t in threads.items():
        mv = pol.next_move(t, 0.0)
        if mv is None:
            print(cid, sid, "NO MOVE")
            continue
        msg = pol.compose(t, mv, 0.0)
        p = validate(case, msg, t, claims=mv.claims, known_ids=pol.known_id)
        n += 1
        problems += bool(p)
        sheet = mv.sheets[0]
        q = t.standing[1]["unit_price"]
        print(f"{cid} {sid:12} quote {q:>8} -> {sheet['unit_price']:>8} ({sheet['unit_price'] / q - 1:+.1%}) "
              f"{len(mv.sheets)} sheets, {len(mv.claims)} claims {'OK' if not p else p}")  # fmt: skip
print(n, "round-1 messages,", problems, "with problems")
