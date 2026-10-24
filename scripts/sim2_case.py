"""Play sim2 cases in-process and print their traces: python scripts/sim2_case.py case:D01 [case:P08 ...]"""

import os
import shutil
import subprocess
import sys

os.environ.setdefault("JT_NO_LLM", "1")
from janteiros.evaluate import in_process_arena  # noqa: E402
from janteiros.events.log import EventLog  # noqa: E402
from janteiros.runner.play import play_case  # noqa: E402
from janteiros.sim2.server import suite  # noqa: E402

sim, app, _ = suite(seed=1, quick=True)
shutil.rmtree("runs/dbg-sim2", ignore_errors=True)
log = EventLog(root="runs", run_id="dbg-sim2")
for cid in sys.argv[1:]:
    arena = in_process_arena(sim, app)
    r = play_case(arena, sim.cases[cid], log)
    s = sim.sessions[r["session_id"]]
    for sup in s["threads"]:
        p = sim.personas[(s["id"], sup)]
        print(
            cid,
            sup,
            f"persona {p.owner} floor_v {p.floor_v:.3f} fd {p.first_drop:.3f} patience {p.patience:.2f} cum {p.cum_gap:.2f} rate {p.est.rate:.3f} fc {p.est.full_cost}",
        )
    subprocess.run([sys.executable, "scripts/show_run.py", "runs/dbg-sim2", cid])
