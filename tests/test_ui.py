import json

from fastapi.testclient import TestClient

from janteiros import ui_server


def test_ui_serves_runs_events_and_history(tmp_path, monkeypatch):
    run = tmp_path / "r1"
    run.mkdir()
    lines = [{"kind": "run_start", "t": 0, "ts": "2026-10-24T10:00:00+00:00", "mode": "eval"},
             {"kind": "http", "t": 0.1, "ts": "2026-10-24T10:00:00+00:00"},
             {"kind": "result", "case_id": "case:S01", "t": 1, "ts": "2026-10-24T10:00:01+00:00", "result": {"score": 0.9}}]  # fmt: skip
    (run / "events.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    hist = tmp_path / "history.jsonl"
    hist.write_text(json.dumps({"run_id": "r1", "agent": "jt", "suites": {}}) + "\n")
    monkeypatch.setattr(ui_server, "RUNS", tmp_path)
    monkeypatch.setattr(ui_server, "HISTORY", hist)
    c = TestClient(ui_server.create_app())
    runs = c.get("/api/runs").json()
    assert runs[0]["run_id"] == "r1" and runs[0]["results"] == 1
    snap = c.get("/api/runs/r1/events").json()
    assert [e["kind"] for e in snap["events"]] == ["run_start", "result"]  # http calls are not shown
    assert snap["offset"] == (run / "events.jsonl").stat().st_size
    assert c.get("/api/runs/..%2F..%2Fetc/events").status_code == 404
    assert c.get("/api/history").json()[0]["run_id"] == "r1"
    assert c.get("/").status_code == 200 and c.get("/app.js").status_code == 200


def test_ui_never_talks_to_the_arena():
    import pathlib

    src = (
        pathlib.Path(ui_server.__file__).read_text()
        + (pathlib.Path(ui_server.__file__).parents[1] / "ui" / "app.js").read_text()
    )
    assert "arena.client" not in src and "ArenaClient" not in src and "https://arena" not in src
