"""jt ui: the Janteiros 2.0 monitor. Reads only our own event files (runs/*/events.jsonl) and
results/history.jsonl, and pushes new events over Server-Sent Events. It never calls the arena."""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
WEB = ROOT / "ui"
HISTORY = ROOT / "results" / "history.jsonl"


def run_path(run_id):
    p = (RUNS / run_id / "events.jsonl").resolve()
    if not p.is_relative_to(RUNS.resolve()) or not p.is_file():
        raise HTTPException(404, "no such run")
    return p


def summary(path):
    """First and last events of a run, and its counts, without reading it all twice."""
    first, last, cases, results, n = None, None, set(), 0, 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            n += 1
            first = first or e
            last = e
            if e.get("case_id"):
                cases.add(e["case_id"])
            if e.get("kind") == "result":
                results += 1
    first = first or {}
    return {
        "run_id": path.parent.name, "started": first.get("ts"), "updated": (last or {}).get("ts"),
        "commit": first.get("commit"), "mode": first.get("mode"), "set": first.get("set") or first.get("suite"),
        "arena": first.get("arena"), "cases": len(cases), "results": results, "events": n,
    }  # fmt: skip


def create_app():
    app = FastAPI(title="Janteiros 2.0")

    @app.get("/api/runs")
    def runs():
        out = []
        for p in sorted(RUNS.glob("*/events.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]:
            try:
                out.append(summary(p))
            except OSError:
                continue
        return out

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str):
        p = run_path(run_id)
        data = p.read_bytes()
        data = data[: data.rfind(b"\n") + 1]  # whole lines only; the stream goes on from here
        out = []
        for line in data.decode("utf-8", "replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") != "http":
                out.append(e)
        return JSONResponse({"events": out, "offset": len(data)})

    @app.get("/api/runs/{run_id}/stream")
    async def stream(run_id: str, offset: int = 0):
        p = run_path(run_id)

        async def gen():
            pos = offset
            idle = 0
            while True:
                size = p.stat().st_size
                if size > pos:
                    with p.open("rb") as f:
                        f.seek(pos)
                        chunk = f.read(size - pos)
                    cut = chunk.rfind(b"\n") + 1
                    pos += cut
                    for line in chunk[:cut].decode("utf-8", "replace").splitlines():
                        if line.strip() and '"kind": "http"' not in line:
                            yield f"data: {line}\n\n"
                    idle = 0
                else:
                    idle += 1
                    if idle % 30 == 0:
                        yield ": keepalive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/history")
    def history():
        if not HISTORY.is_file():
            return []
        out = []
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/{name}")
    def static(name: str):
        p = (WEB / name).resolve()
        if not p.is_relative_to(WEB.resolve()) or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    return app


def serve(port=8765):
    import uvicorn

    print(f"Janteiros 2.0 UI: http://localhost:{port}", flush=True)
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
