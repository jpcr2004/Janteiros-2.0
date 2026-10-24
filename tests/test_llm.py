from janteiros.llm import client
from janteiros.llm.reader import clamp
from janteiros.llm.writer import clean_input, ok


def test_writer_paragraph_guard():
    assert ok(
        "Thank you for explaining the pressure on your energy costs. We understand it and hope our proposal below helps."
    )
    assert not ok("We can pay 5.20 per unit.")
    assert not ok("Given our deadline we need an answer.")
    assert not ok("Your index idx:LCI-C@2026-06 rose.")
    assert not ok("Dear Sir, thanks. Kind regards")
    assert not ok("short")


def test_inputs_lose_their_figures():
    assert "2022" not in clean_input("Since 2022 our costs rose by 9.5 %")


def test_reader_output_is_clamped():
    r = clamp({"asks": ["faster_payment", "send money", "none"], "mood": "angry", "tactic": "x",
               "price_frozen": "yes", "cash_short": True, "capacity_tight": False})  # fmt: skip
    assert r == {"asks": ["faster_payment"], "mood": "neutral", "tactic": "unknown", "price_frozen": False,
                 "cash_short": True, "capacity_tight": False}  # fmt: skip
    assert clamp("nope") is None


def test_no_key_no_call(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert client.ask_json("writer", "s", "u", {"type": "object"}, 10) is None
    assert client.writer_from_env() is None
