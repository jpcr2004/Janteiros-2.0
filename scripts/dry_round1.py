"""Round 1 against every recorded real opening (no arena): plan, compose, validate."""

import glob
import json
import os

os.environ.setdefault("JT_NO_LLM", "1")
from janteiros.events.log import NullLog  # noqa: E402
from janteiros.runner.play import make_policy  # noqa: E402
from janteiros.runner.state import Thread  # noqa: E402
from janteiros.validate.check import validate  # noqa: E402

cases = {c["case_id"]: c for f in ("practice", "sandbox") for c in json.load(open(f"data/cases/{f}.json"))}
seen, n, problems = set(), 0, 0
for f in sorted(glob.glob(os.path.expanduser("~/IdeaProjects/ehl-final-procurecabana/logs/sessions/*/*.json"))):
    d = json.load(open(f))
    cid = d.get("case_id")
    if cid in seen or cid not in cases or not d.get("opening"):
        continue
    seen.add(cid)
    case = cases[cid]
    threads = {}
    for sid, op in d["opening"].items():
        t = Thread(sid)
        t.messages = [
            {
                "from": f"sales@{sid[4:]}",
                "status": op["status"],
                "body": op.get("text", ""),
                "signals": op.get("signals"),
                "term_sheets": [{"term_sheet_id": op["offer_id"], "terms": op["terms"]}],
                "attachments": [],
            }
        ]
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
        print(
            f"{cid} {sid:12} quote {q:>8} -> {sheet['unit_price']:>8} ({sheet['unit_price'] / q - 1:+.1%}) {len(mv.sheets)} sheets, "
            f"{len(mv.claims)} claims {'OK' if not p else p}"
        )
print(n, "round-1 messages,", problems, "with problems")
