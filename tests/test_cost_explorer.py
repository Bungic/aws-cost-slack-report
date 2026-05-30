from datetime import date

from cost_explorer import (
    DateRange,
    RIUtilizationTotal,
    SPUtilization,
    build_windows,
    has_any_reservations,
    has_any_savings_plans,
)


def test_date_range_label_same_month():
    r = DateRange(start=date(2026, 5, 1), end=date(2026, 5, 16))
    assert r.label == "May 01-15, 2026"


def test_date_range_label_cross_month():
    r = DateRange(start=date(2026, 4, 20), end=date(2026, 5, 5))
    assert r.label == "Apr 20, 2026 – May 04, 2026"


def test_date_range_to_api_uses_iso():
    r = DateRange(start=date(2026, 5, 1), end=date(2026, 6, 1))
    assert r.to_api() == {"Start": "2026-05-01", "End": "2026-06-01"}


def test_build_windows_produces_equal_length_trailing_pair():
    today = date(2026, 5, 31)
    current, previous = build_windows(today=today, window_days=30)
    assert current.end == date(2026, 5, 31)
    assert current.start == date(2026, 5, 1)
    assert previous.end == date(2026, 5, 1)
    assert previous.start == date(2026, 4, 1)
    assert (current.end - current.start).days == 30
    assert (previous.end - previous.start).days == 30


def test_has_any_reservations_false_when_purchased_is_zero():
    util = RIUtilizationTotal.empty()
    assert has_any_reservations(util) is False


def test_has_any_reservations_true_when_purchased_positive():
    util = RIUtilizationTotal(
        purchased_hours=720.0,
        used_hours=600.0,
        unused_hours=120.0,
        utilization_pct=83.3,
        unrealized_savings_usd=12.0,
        net_savings_usd=120.0,
    )
    assert has_any_reservations(util) is True


def test_has_any_savings_plans_false_on_zero_commitment():
    assert has_any_savings_plans(SPUtilization.empty()) is False


def test_has_any_savings_plans_true_when_committed():
    sp = SPUtilization(
        utilization_pct=98.0,
        total_commitment_usd=1500.0,
        used_commitment_usd=1470.0,
        unused_commitment_usd=30.0,
        net_savings_usd=600.0,
    )
    assert has_any_savings_plans(sp) is True
