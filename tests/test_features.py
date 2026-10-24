from janteiros.features import enabled


def test_feature_switches(monkeypatch):
    monkeypatch.delenv("JT_ASK_TRADES", raising=False)
    assert enabled("JT_ASK_TRADES")
    monkeypatch.setenv("JT_ASK_TRADES", "0")
    assert not enabled("JT_ASK_TRADES")
    monkeypatch.setenv("JT_ASK_TRADES", "false")
    assert not enabled("JT_ASK_TRADES")
    monkeypatch.setenv("JT_ASK_TRADES", "1")
    assert enabled("JT_ASK_TRADES")
