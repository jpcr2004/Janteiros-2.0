"""With every switch off (JT_X_ALL=0) the agent writes exactly the baseline round-1 messages
(main 22f7c26: baseline-r4 plus the release scenario, which moves only P08's claim order) on every recorded real opening."""

import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _golden():
    spec = importlib.util.spec_from_file_location("golden_round1", ROOT / "scripts/golden_round1.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_switches_off_reproduce_baseline(monkeypatch):
    monkeypatch.setenv("JT_X_ALL", "0")
    want = json.loads((ROOT / "tests/fixtures/golden_round1.json").read_text())
    got = json.loads(json.dumps(_golden().round1(), sort_keys=True))
    assert sorted(got) == sorted(want)
    for k in want:
        assert got[k] == want[k], k
