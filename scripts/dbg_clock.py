"""Play one case file on the kit sim with fake time: python scripts/dbg_clock.py case:P05 28"""

import os
import shutil
import sys

sys.path.insert(0, "tests")
os.environ["JT_NO_LLM"] = "1"
from arena_kit.sim import Sim  # noqa: E402
from conftest import FakeTime, all_case_files, timed_arena  # noqa: E402

from janteiros.events.log import EventLog, read_events  # noqa: E402
from janteiros.runner.play import play_case  # noqa: E402

cid = sys.argv[1] if len(sys.argv) > 1 else "case:P05"
lat = float(sys.argv[2]) if len(sys.argv) > 2 else 28.0
shutil.rmtree("runs/dbg-clock", ignore_errors=True)
fake = FakeTime(latency=lat)
case = all_case_files()[cid]
sim = Sim({cid: case}, clock=fake)
arena = timed_arena(sim, fake)
log = EventLog(root="runs", run_id="dbg-clock")
r = play_case(arena, case, log, mono=fake, wall=fake)
print({k: r.get(k) for k in ("deal", "score", "end_reason", "rounds")})
for e in read_events("dbg-clock", "runs"):
    k = e["kind"]
    if k in ("send", "timeout", "clock", "accept", "walk", "error") or (
        k == "model_update" and (e.get("stop") or e.get("pruned"))
    ):
        what = e.get("reason") or e.get("what") or e.get("stop") or e.get("detail") or e.get("error")
        print(f"{e['t']:6.1f}", k, e.get("supplier_id"), e.get("age"), what, e.get("keepalive") or "")
