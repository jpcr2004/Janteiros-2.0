import httpx
import pytest
from arena_kit.sim import Sim, create_app, load_cases
from fastapi.testclient import TestClient

from janteiros.arena.client import Arena
from janteiros.events.log import EventLog


class Clock:
    """A clock for the sim that only moves when told to."""

    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class InProcess(httpx.BaseTransport):
    """Hands each request to a FastAPI app in this process."""

    def __init__(self, app):
        self.tc = TestClient(app)

    def handle_request(self, request):
        r = self.tc.request(request.method, str(request.url), content=request.read(), headers=dict(request.headers))
        return httpx.Response(r.status_code, headers=r.headers, content=r.content)


def sim_arena(sim, team="janteiros", app=None):
    """An Arena talking to `sim` in-process (no port), with a fresh sim key."""
    transport = InProcess(app or create_app(sim))
    key = transport.tc.post("/v1/register", json={"team_name": team, "event_code": sim.code}).json()["api_key"]
    return Arena("http://testserver", key, transport=transport)


def facts():
    import json
    import pathlib

    return json.loads((pathlib.Path(__file__).parents[1] / "data" / "package" / "facts.json").read_text())


@pytest.fixture
def kit_sim():
    return Sim(load_cases(), facts=facts(), clock=Clock())


@pytest.fixture
def arena(kit_sim):
    return sim_arena(kit_sim)


@pytest.fixture
def log(tmp_path):
    return EventLog(root=tmp_path)


class FakeTime:
    """One clock for the sim (its 60 s rule and waves), the runner and the wave loop. Every
    message to a supplier takes `latency` seconds; a sleep advances it."""

    def __init__(self, t=1_000_000.0, latency=20.0, other=0.05):
        import threading

        self.t, self.latency, self.other = t, latency, other
        self.lock = threading.Lock()

    def __call__(self):
        return self.t

    def advance(self, s):
        with self.lock:
            self.t += s

    def sleep(self, s):
        self.advance(s)


class Timed(InProcess):
    """The arena checks lateness when our message arrives; the supplier's reply comes `latency`
    later and the next 60 s count from that reply."""

    def __init__(self, app, fake, sim=None):
        super().__init__(app)
        self.fake, self.sim = fake, sim

    def handle_request(self, request):
        path = request.url.path
        if request.method == "POST" and path.endswith("/messages"):
            r = super().handle_request(request)
            self.fake.advance(self.fake.latency)
            if self.sim is not None and r.status_code == 200:
                parts = path.split("/")
                s = self.sim.sessions.get(parts[3])
                th = s and s["threads"].get(parts[5])
                if th and not th["closed"]:
                    th["last_at"] = self.fake.t
            return r
        self.fake.advance(self.fake.other)
        return super().handle_request(request)


def timed_arena(sim, fake, app=None, team="janteiros"):
    transport = Timed(app or create_app(sim), fake, sim)
    key = transport.tc.post("/v1/register", json={"team_name": team, "event_code": sim.code}).json()["api_key"]
    return Arena("http://testserver", key, transport=transport)


def all_case_files():
    import json
    import pathlib

    root = pathlib.Path(__file__).parents[1] / "data" / "cases"
    out = {}
    for f in ("practice", "sandbox"):
        for c in json.loads((root / f"{f}.json").read_text()):
            out[c["case_id"]] = c
    return out
