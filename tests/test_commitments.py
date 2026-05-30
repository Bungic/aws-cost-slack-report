"""Tests for the RI and Savings Plans analysis + Slack rendering paths."""
from analysis import (
    RICoverageFamilyDelta,
    RITotalDelta,
    SPCoverageDelta,
    SPUtilizationDelta,
    compute_ri_coverage_family_deltas,
    compute_sp_plan_deltas,
    pick_ri_leakers,
)
from cost_explorer import (
    RICoverageByFamily,
    RISubscriptionLeak,
    RIUtilizationTotal,
    SPCoverage,
    SPPerPlan,
    SPUtilization,
)
from slack_publisher import (
    COLOR_RI,
    COLOR_SP,
    build_ri_attachment,
    build_ri_summary_text,
    build_sp_attachment,
    build_sp_summary_text,
)


# ---------- RI analysis ---------------------------------------------------

def _ri_total(util_pct: float, purchased: float = 1000.0, unrealized: float = 50.0):
    used = purchased * util_pct / 100.0
    return RIUtilizationTotal(
        purchased_hours=purchased,
        used_hours=used,
        unused_hours=purchased - used,
        utilization_pct=util_pct,
        unrealized_savings_usd=unrealized,
        net_savings_usd=300.0,
    )


def test_ri_total_delta_computes_pp_and_dollar_delta():
    delta = RITotalDelta(current=_ri_total(75.0, 1200, 40), previous=_ri_total(80.0, 1000, 30))
    assert delta.utilization_pp == -5.0
    assert delta.purchased_delta == 200.0
    assert delta.unrealized_delta == 10.0


def test_compute_ri_coverage_family_deltas_keeps_above_threshold():
    current = {
        "m6a": RICoverageByFamily("m6a", 72.0, 600.0, 250.0),
        "c6a": RICoverageByFamily("c6a", 100.0, 800.0, 0.0),
        "r7a": RICoverageByFamily("r7a", 100.0, 720.0, 0.0),
    }
    previous = {
        "m6a": RICoverageByFamily("m6a", 89.0, 740.0, 100.0),
        "c6a": RICoverageByFamily("c6a", 100.0, 780.0, 0.0),
        "r7a": RICoverageByFamily("r7a", 0.0, 0.0, 451.0),
    }
    deltas = compute_ri_coverage_family_deltas(current, previous, min_pp=5.0)
    families = [d.family for d in deltas]
    # m6a (-17pp) and r7a (+100pp) qualify, c6a (no change) is filtered out.
    assert families == ["r7a", "m6a"]
    assert deltas[0].pp_change == 100.0
    assert deltas[1].pp_change == -17.0


def test_pick_ri_leakers_filters_and_orders_by_unused_hours():
    leaks = [
        RISubscriptionLeak("good-sub", 100.0, 1000.0, 0.0, 0.0),
        RISubscriptionLeak("loud-sub", 46.9, 23664.0, 12568.0, 30.0),
        RISubscriptionLeak("quiet-sub", 46.8, 19488.0, 10371.0, 27.0),
        RISubscriptionLeak("tiny-sub", 78.4, 200.0, 5.0, 0.5),  # below min_unused_hours
        RISubscriptionLeak("good-but-busy", 90.0, 1500.0, 150.0, 1.0),  # above max_util
    ]
    chosen = pick_ri_leakers(leaks, max_leak_util_pct=80.0, min_unused_hours=100.0, limit=10)
    assert [l.subscription_id for l in chosen] == ["loud-sub", "quiet-sub"]


def test_pick_ri_leakers_respects_limit():
    leaks = [
        RISubscriptionLeak(f"sub-{i}", 50.0, 1000.0, 500.0 - i * 10, 5.0)
        for i in range(10)
    ]
    chosen = pick_ri_leakers(leaks, max_leak_util_pct=80.0, min_unused_hours=100.0, limit=3)
    assert len(chosen) == 3
    assert chosen[0].subscription_id == "sub-0"


# ---------- RI Slack rendering -------------------------------------------

def test_ri_summary_text_has_blue_diamond_and_unrealized():
    delta = RITotalDelta(current=_ri_total(73.6, 120408, 284.0), previous=_ri_total(74.0, 118000, 250.0))
    text = build_ri_summary_text(delta)
    assert ":large_blue_diamond:" in text
    assert "73.6%" in text
    assert "-0.4pp" in text
    assert "$284.00" in text


def test_ri_attachment_renders_totals_family_and_leakers():
    delta = RITotalDelta(current=_ri_total(73.6, 120408, 284.0), previous=_ri_total(74.0, 118000, 250.0))
    families = [
        RICoverageFamilyDelta("m6a", 72.0, 89.0),
        RICoverageFamilyDelta("r7a", 100.0, 0.0),
    ]
    leakers = [
        RISubscriptionLeak("subscription-abc12345", 46.9, 23664.0, 12568.0, 30.0),
    ]
    attachment = build_ri_attachment(delta, families, leakers)
    assert attachment["color"] == COLOR_RI
    text = attachment["blocks"][0]["text"]["text"]
    assert "Purchased" in text
    assert "Unused" in text
    assert "Net savings" in text
    assert "Coverage by instance family" in text
    assert "m6a" in text
    assert "r7a" in text
    assert "Underutilized subscriptions" in text
    assert "abc12345" in text or "scription" in text


# ---------- SP analysis --------------------------------------------------

def test_sp_coverage_delta_pp_and_dollar():
    current = SPCoverage(67.5, 2851.0, 1369.0, 4220.0)
    previous = SPCoverage(71.2, 3012.0, 1221.0, 4233.0)
    d = SPCoverageDelta(current=current, previous=previous)
    assert round(d.coverage_pp, 1) == -3.7
    assert d.covered_spend_delta == -161.0
    assert d.on_demand_spend_delta == 148.0


def test_sp_utilization_delta_pp_and_dollar():
    current = SPUtilization(98.2, 1542.0, 1515.0, 27.0, 1309.0)
    previous = SPUtilization(99.4, 1542.0, 1533.0, 9.0, 1344.0)
    d = SPUtilizationDelta(current=current, previous=previous)
    assert round(d.utilization_pp, 1) == -1.2
    assert d.unused_commitment_delta == 18.0


def test_compute_sp_plan_deltas_joins_on_arn_and_flags_new():
    current = [
        SPPerPlan("arn:aws:savingsplans::1:savingsplan/abcdef12-...-...", "Compute", "No Upfront", 99.1, 1390.0, 12.0, 1256.0),
        SPPerPlan("arn:aws:savingsplans::1:savingsplan/db000001-...-...", "Database", "No Upfront", 89.4, 124.0, 14.0, 52.0),
    ]
    previous = [
        SPPerPlan("arn:aws:savingsplans::1:savingsplan/abcdef12-...-...", "Compute", "No Upfront", 99.8, 1395.0, 3.0, 1280.0),
    ]
    deltas = compute_sp_plan_deltas(current, previous)
    by_short = {d.short_id: d for d in deltas}
    assert "abcdef12" in by_short
    assert "db000001" in by_short
    assert by_short["db000001"].is_new is True
    assert by_short["abcdef12"].is_new is False
    assert round(by_short["abcdef12"].util_pp, 1) == -0.7


# ---------- SP Slack rendering -------------------------------------------

def test_sp_summary_text_has_purple_circle():
    coverage = SPCoverageDelta(SPCoverage(67.5, 2851.0, 1369.0, 4220.0), SPCoverage(71.2, 3012.0, 1221.0, 4233.0))
    util = SPUtilizationDelta(SPUtilization(98.2, 1542.0, 1515.0, 27.0, 1309.0), SPUtilization(99.4, 1542.0, 1533.0, 9.0, 1344.0))
    text = build_sp_summary_text(coverage, util)
    assert ":large_purple_circle:" in text
    assert "67.5% coverage" in text
    assert "98.2% utilization" in text
    assert "$1,309.00" in text


def test_sp_attachment_renders_coverage_util_and_plans():
    coverage = SPCoverageDelta(SPCoverage(67.5, 2851.0, 1369.0, 4220.0), SPCoverage(71.2, 3012.0, 1221.0, 4233.0))
    util = SPUtilizationDelta(SPUtilization(98.2, 1542.0, 1515.0, 27.0, 1309.0), SPUtilization(99.4, 1542.0, 1533.0, 9.0, 1344.0))
    from analysis import SPPlanDelta
    plans = [
        SPPlanDelta(
            arn="arn:aws:savingsplans::1:savingsplan/abcdef12-...",
            plan_type="Compute",
            payment_option="No Upfront",
            short_id="abcdef12",
            current_util_pct=99.1, previous_util_pct=99.8,
            current_used_usd=1390.0, previous_used_usd=1395.0,
            current_unused_usd=12.0, previous_unused_usd=3.0,
            current_net_savings_usd=1256.0, previous_net_savings_usd=1280.0,
        ),
    ]
    attachment = build_sp_attachment(coverage, util, plans)
    assert attachment["color"] == COLOR_SP
    text = attachment["blocks"][0]["text"]["text"]
    assert "*Coverage*" in text
    assert "*Utilization*" in text
    assert "*Per plan*" in text
    assert "Compute" in text
    assert "abcdef12" in text
    assert "-3.7pp" in text or "-3.7" in text
