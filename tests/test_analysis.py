from analysis import (
    ServiceCostChange,
    compute_cost_changes,
    split_by_direction,
    summarise_group,
    total_cost,
)


def test_cost_change_delta_and_pct():
    change = ServiceCostChange("EC2", 200.0, 100.0)
    assert change.delta == 100.0
    assert change.pct_change == 100.0
    assert change.direction == "up"


def test_cost_change_zero_previous_uses_sentinel_percent():
    change = ServiceCostChange("Bedrock", 50.0, 0.0)
    assert change.delta == 50.0
    assert change.pct_change == 100.0
    assert change.direction == "up"


def test_cost_change_zero_previous_and_zero_current_is_flat():
    change = ServiceCostChange("Nothing", 0.0, 0.0)
    assert change.pct_change == 0.0
    assert change.direction == "flat"


def test_compute_skips_below_dollar_threshold():
    current = {"Tiny": 4.0, "Big": 200.0}
    previous = {"Tiny": 1.0, "Big": 100.0}
    changes = compute_cost_changes(current, previous, min_cost_delta_usd=5.0, min_pct_change=10.0)
    services = {c.service for c in changes}
    assert services == {"Big"}


def test_compute_skips_below_pct_threshold():
    current = {"Heavy": 1050.0}
    previous = {"Heavy": 1000.0}
    changes = compute_cost_changes(current, previous, min_cost_delta_usd=5.0, min_pct_change=10.0)
    assert changes == []


def test_compute_keeps_both_increases_and_decreases():
    current = {"A": 200.0, "B": 50.0}
    previous = {"A": 100.0, "B": 200.0}
    changes = compute_cost_changes(current, previous, min_cost_delta_usd=5.0, min_pct_change=10.0)
    services = {c.service for c in changes}
    assert services == {"A", "B"}


def test_compute_includes_dropped_services():
    current = {"A": 100.0}
    previous = {"A": 100.0, "Removed": 80.0}
    changes = compute_cost_changes(current, previous, min_cost_delta_usd=5.0, min_pct_change=10.0)
    assert len(changes) == 1
    removed = changes[0]
    assert removed.service == "Removed"
    assert removed.current == 0.0
    assert removed.previous == 80.0


def test_split_by_direction_sorts_largest_first():
    changes = [
        ServiceCostChange("A", 200, 100),  # +100
        ServiceCostChange("B", 300, 100),  # +200
        ServiceCostChange("C", 50, 200),   # -150
        ServiceCostChange("D", 0, 60),     # -60
    ]
    decreases, increases = split_by_direction(changes)
    assert [c.service for c in increases] == ["B", "A"]
    assert [c.service for c in decreases] == ["C", "D"]


def test_split_by_direction_handles_empty_input():
    assert split_by_direction([]) == ([], [])


def test_summarise_group_returns_count_total_abs_and_avg_pct():
    changes = [
        ServiceCostChange("A", 200, 100),  # +100, +100%
        ServiceCostChange("B", 110, 100),  # +10, +10%
    ]
    count, total_abs, avg_pct = summarise_group(changes)
    assert count == 2
    assert total_abs == 110.0
    assert avg_pct == 55.0


def test_summarise_group_handles_empty_input():
    assert summarise_group([]) == (0, 0.0, 0.0)


def test_total_cost_rounds_to_two_decimals():
    totals = {"A": 10.123, "B": 20.456}
    assert total_cost(totals) == 30.58
