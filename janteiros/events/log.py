"""Append-only JSONL event stream per run: runs/<run_id>/events.jsonl. The UI and the evaluation
read only this. Thread-safe; a failing write never breaks a move."""

import datetime as dt
import json
import math
import subprocess
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"


def git_commit() -> tuple[str, bool]:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no", "--", ".", ":!results"],
                               cwd=ROOT, capture_output=True, text=True)  # fmt: skip
        # results/ is where measurements land: it does not make the measured code dirty
        return (sha.stdout.strip() or "none"), bool(dirty.stdout.strip())
    except Exception:  # noqa: BLE001
        return "none", False


def _clean(x):
    if isinstance(x, float):
        return None if math.isnan(x) else ("inf" if math.isinf(x) else round(x, 4))
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, list | tuple | set):
        return [_clean(v) for v in x]
    return x


class EventLog:
    def __init__(self, run_id=None, root=RUNS, meta=None):
        self.run_id = run_id or dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.dir = Path(root) / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "events.jsonl"
        self.commit, self.dirty = git_commit()
        self.t0 = time.monotonic()
        self.lock = threading.Lock()
        self.subscribers = []  # in-process listeners: fn(event)
        self.emit("run_start", commit=self.commit, dirty=self.dirty, **(meta or {}))

    def emit(self, kind, case_id=None, session_id=None, supplier_id=None, **payload):
        ev = {
            "ts": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
            "t": round(time.monotonic() - self.t0, 3),
            "run_id": self.run_id,
            "commit": self.commit,
            "kind": kind,
            "case_id": case_id,
            "session_id": session_id,
            "supplier_id": supplier_id,
            **_clean(payload),
        }
        try:
            line = json.dumps(ev, ensure_ascii=False, default=str)
            with self.lock:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            for fn in list(self.subscribers):
                fn(ev)
        except Exception:  # noqa: BLE001 - logging must never cost a move
            pass
        return ev

    def bind(self, **ctx):
        return BoundLog(self, ctx)


class BoundLog:
    """The log with case/session/supplier fixed."""

    def __init__(self, log, ctx):
        self.log, self.ctx = log, ctx

    def emit(self, kind, **payload):
        return self.log.emit(kind, **{**self.ctx, **payload})

    def bind(self, **ctx):
        return BoundLog(self.log, {**self.ctx, **ctx})


class NullLog:
    run_id = "null"

    def emit(self, kind, **payload):
        return {}

    def bind(self, **ctx):
        return self


def read_events(run_id, root=RUNS):
    path = Path(root) / run_id / "events.jsonl"
    out = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out
