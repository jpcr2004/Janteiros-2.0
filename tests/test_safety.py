import httpx
import pytest

from janteiros.arena.client import Arena, ForbiddenPath, is_forbidden

BAD = ["/v1/events", "/v1/events/stream", "/screen", "/v1/screen", "/leaderboard", "/v1/leaderboard",
       "/results", "/v1/results", "/v1/results/Janteiros", "https://arena.tacto.ai/v1/leaderboard?set=final",
       "//v1//events/", "/V1/Screen", "/events"]  # fmt: skip
GOOD = ["/v1/cases", "/v1/sessions", "/v1/sessions/abc/threads/sup:1/messages", "/v1/data", "/v1/me", "/health",
        "/v1/sessions/x/attachments/att:results-1"]  # fmt: skip


@pytest.mark.parametrize("path", BAD)
def test_forbidden_paths_are_recognised(path):
    assert is_forbidden(path)


@pytest.mark.parametrize("path", GOOD)
def test_allowed_paths_pass(path):
    assert not is_forbidden(path)


def test_client_refuses_forbidden_paths_before_any_request():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    a = Arena("https://arena.example", "k", transport=httpx.MockTransport(handler))
    for path in ["/events", "/events/stream", "/leaderboard", "/results", "/screen", "/results/x"]:
        with pytest.raises(ForbiddenPath):
            a._call("GET", path)
    with pytest.raises(ForbiddenPath):
        a.leaderboard("final")
    # even the raw httpx client under the wrapper refuses, at the root too
    for url in ["https://arena.example/screen", "https://arena.example/leaderboard", "/events"]:
        with pytest.raises(ForbiddenPath):
            a.http.get(url)
    assert seen == []
    a._call("GET", "/cases", params={"set": "final"})
    assert seen == ["/v1/cases"]


def test_secret_hook_exists_and_blocks_env():
    import pathlib

    hook = pathlib.Path(__file__).parents[1] / ".githooks" / "pre-commit"
    text = hook.read_text()
    assert "Blocked: .env file staged" in text and "API_KEY" in text
