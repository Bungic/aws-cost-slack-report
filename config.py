"""Environment-driven configuration for billing-bot."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    slack_bot_token: str
    slack_channel_id: str
    min_cost_delta_usd: float
    min_pct_change: float
    min_ri_pct_points: float
    max_ri_leak_util_pct: float
    min_ri_leak_hours: float
    max_ri_leak_subscriptions: int
    window_days: int
    dry_run: bool
    log_level: str


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is required but not set. Refusing to run without it."
        )
    return value


def _optional_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _optional_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def _optional_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def load_config() -> Config:
    return Config(
        slack_bot_token=_required("SLACK_BOT_TOKEN"),
        slack_channel_id=_required("SLACK_CHANNEL_ID"),
        min_cost_delta_usd=_optional_float("MIN_COST_DELTA_USD", 5.0),
        min_pct_change=_optional_float("MIN_PCT_CHANGE", 10.0),
        min_ri_pct_points=_optional_float("MIN_RI_PCT_POINTS", 5.0),
        max_ri_leak_util_pct=_optional_float("MAX_RI_LEAK_UTIL_PCT", 80.0),
        min_ri_leak_hours=_optional_float("MIN_RI_LEAK_HOURS", 100.0),
        max_ri_leak_subscriptions=_optional_int("MAX_RI_LEAK_SUBSCRIPTIONS", 5),
        window_days=_optional_int("WINDOW_DAYS", 30),
        dry_run=_optional_bool("DRY_RUN", False),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
