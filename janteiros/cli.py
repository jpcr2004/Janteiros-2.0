"""jt: play one case, run a set (sandbox, practice, final), evaluate, serve the UI."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIM_URL = "http://localhost:8000"


def load_env(path=ROOT / ".env"):
    """KEY=VALUE lines from .env into the environment; real environment variables win."""
    p = Path(path)
    if p.is_file():
        for line in p.read_text().splitlines():
            key, sep, value = line.partition("=")
            value = value.strip().strip("\"'")
            if sep and value and not key.strip().startswith("#"):
                os.environ.setdefault(key.strip(), value)


def summary(r):
    keep = ("deal", "supplier_id", "end_reason", "c_open", "c_deal", "c_alt", "c_best", "score", "rounds")
    return {k: r.get(k) for k in keep if k in r}


def main(argv=None):
    load_env()
    ap = argparse.ArgumentParser(prog="jt")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("play", help="play one case")
    p.add_argument("case_id")
    p.add_argument("--sim", action="store_true", help=f"force ARENA_URL={SIM_URL}")
    p.add_argument("--policy", default="main", choices=["main", "trivial"])
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--real", action="store_true", help="allow a non-local arena (humans only)")
    r = sub.add_parser("run", help="play every case of a set; final: wait for the waves")
    r.add_argument("--set", default="sandbox", choices=["sandbox", "practice", "final"])
    r.add_argument("--sim", action="store_true")
    r.add_argument("--policy", default="main", choices=["main", "trivial"])
    r.add_argument("--no-llm", action="store_true")
    r.add_argument("--parallel", type=int, default=10)
    r.add_argument("--waves", type=int, default=5)
    r.add_argument("--cases", nargs="*", help="only these case ids")
    r.add_argument("--real", action="store_true", help="allow a non-local arena (humans only)")
    e = sub.add_parser("eval", help="the offline benchmark")
    e.add_argument("--note", default="")
    e.add_argument("--no-llm", action="store_true")
    e.add_argument("--quick", action="store_true", help="fewer seeds")
    e.add_argument("--suite", default="all", choices=["all", "kit", "sim2"])
    e.add_argument("--allow-dirty", action="store_true", help="scratch run on uncommitted code")
    e.add_argument("--agent", default="jt", choices=["jt", "old", "naive"])
    rp = sub.add_parser("report", help="diagnose a run (default: the latest play/run)")
    rp.add_argument("run_id", nargs="?")
    u = sub.add_parser("ui", help="serve the UI")
    u.add_argument("--port", type=int, default=8765)
    s = sub.add_parser("sim2", help="serve the calibrated supplier simulator")
    s.add_argument("--port", type=int, default=8001)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--no-clock", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")  # fmt: skip

    if args.cmd in ("play", "run"):
        if args.sim:
            os.environ["ARENA_URL"] = SIM_URL
        from .arena.client import is_local

        if not is_local(os.environ.get("ARENA_URL")) and not args.real:
            print(f"ARENA_URL={os.environ.get('ARENA_URL')} is not local: pass --real to play there.")
            return 2
        if args.no_llm:
            os.environ["JT_NO_LLM"] = "1"
        from .runner.play import play_case_id, run_set

        if args.cmd == "play":
            out = play_case_id(args.case_id, policy=args.policy)
            print(json.dumps(summary(out), indent=2, default=str))
        else:
            out = run_set(args.set, policy=args.policy, parallel=args.parallel, waves=args.waves, only=args.cases)
            for cid, res in out.items():
                print(cid, res if isinstance(res, Exception) else summary(res))
    elif args.cmd == "eval":
        if args.no_llm:
            os.environ["JT_NO_LLM"] = "1"
        from .evaluate import main as run_eval

        return run_eval(note=args.note, quick=args.quick, suite=args.suite, agent=args.agent, allow_dirty=args.allow_dirty)
    elif args.cmd == "report":
        from .report import main as report

        return report(args.run_id)
    elif args.cmd == "ui":
        from .ui_server import serve

        serve(args.port)
    elif args.cmd == "sim2":
        from .sim2.server import serve

        serve(args.port, args.seed, no_clock=args.no_clock)
    return 0


if __name__ == "__main__":
    sys.exit(main())
