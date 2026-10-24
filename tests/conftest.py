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


@pytest.fixture
def kit_sim():
    return Sim(load_cases(), clock=Clock())


@pytest.fixture
def arena(kit_sim):
    return sim_arena(kit_sim)


@pytest.fixture
def log(tmp_path):
    return EventLog(root=tmp_path)
