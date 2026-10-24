import os
import shutil
import sys

sys.path.insert(0, "tests")
os.environ["JT_NO_LLM"] = "1"
from arena_kit.sim import Sim, load_cases  # noqa: E402
from conftest import Clock, sim_arena  # noqa: E402

from janteiros.events.log import EventLog  # noqa: E402
from janteiros.runner.play import play_case  # noqa: E402

shutil.rmtree("runs/dbg-kit", ignore_errors=True)
sim = Sim(load_cases(), clock=Clock())
a = sim_arena(sim)
log = EventLog(root="runs", run_id="dbg-kit")
for c in ["case:S01", "case:S02", "case:S03"]:
    r = play_case(a, sim.cases[c], log, policy="main")
    print(c, r["deal"], round(r["score"], 3))
