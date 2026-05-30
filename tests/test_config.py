import pytest

from config import load_config


def test_load_config_requires_token(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")
    with pytest.raises(RuntimeError, match="SLACK_BOT_TOKEN"):
        load_config()


def test_load_config_requires_channel(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_CHANNEL_ID", raising=False)
    with pytest.raises(RuntimeError, match="SLACK_CHANNEL_ID"):
        load_config()


def test_load_config_defaults(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")
    for k in (
        "MIN_COST_DELTA_USD", "MIN_PCT_CHANGE", "WINDOW_DAYS", "DRY_RUN",
        "MIN_RI_PCT_POINTS", "MAX_RI_LEAK_UTIL_PCT", "MIN_RI_LEAK_HOURS",
        "MAX_RI_LEAK_SUBSCRIPTIONS",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config()
    assert cfg.min_cost_delta_usd == 5.0
    assert cfg.min_pct_change == 10.0
    assert cfg.window_days == 30
    assert cfg.dry_run is False
    assert cfg.min_ri_pct_points == 5.0
    assert cfg.max_ri_leak_util_pct == 80.0
    assert cfg.min_ri_leak_hours == 100.0
    assert cfg.max_ri_leak_subscriptions == 5


def test_load_config_applies_overrides(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")
    monkeypatch.setenv("MIN_COST_DELTA_USD", "25")
    monkeypatch.setenv("MIN_PCT_CHANGE", "5")
    monkeypatch.setenv("WINDOW_DAYS", "7")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("MIN_RI_PCT_POINTS", "2.5")
    monkeypatch.setenv("MAX_RI_LEAK_UTIL_PCT", "95")
    monkeypatch.setenv("MIN_RI_LEAK_HOURS", "50")
    monkeypatch.setenv("MAX_RI_LEAK_SUBSCRIPTIONS", "10")
    cfg = load_config()
    assert cfg.min_cost_delta_usd == 25.0
    assert cfg.min_pct_change == 5.0
    assert cfg.window_days == 7
    assert cfg.dry_run is True
    assert cfg.min_ri_pct_points == 2.5
    assert cfg.max_ri_leak_util_pct == 95.0
    assert cfg.min_ri_leak_hours == 50.0
    assert cfg.max_ri_leak_subscriptions == 10


def test_load_config_ignores_bad_numeric_values(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")
    monkeypatch.setenv("MIN_COST_DELTA_USD", "garbage")
    monkeypatch.setenv("MAX_RI_LEAK_SUBSCRIPTIONS", "-3")
    cfg = load_config()
    assert cfg.min_cost_delta_usd == 5.0
    assert cfg.max_ri_leak_subscriptions == 5
