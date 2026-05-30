"""Pure data-crunching layer. No AWS, no Slack. Easy to unit-test."""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from cost_explorer import (
    RICoverageByFamily,
    RISubscriptionLeak,
    RIUtilizationTotal,
    SPCoverage,
    SPPerPlan,
    SPUtilization,
)


# ---------- Cost-by-service deltas -----------------------------------------

@dataclass(frozen=True)
class ServiceCostChange:
    service: str
    current: float
    previous: float

    @property
    def delta(self) -> float:
        return self.current - self.previous

    @property
    def pct_change(self) -> float:
        if self.previous == 0:
            return 100.0 if self.current > 0 else 0.0
        return (self.current - self.previous) / self.previous * 100.0

    @property
    def direction(self) -> str:
        if self.delta > 0:
            return "up"
        if self.delta < 0:
            return "down"
        return "flat"


def compute_cost_changes(
    current: Dict[str, float],
    previous: Dict[str, float],
    min_cost_delta_usd: float,
    min_pct_change: float,
) -> List[ServiceCostChange]:
    services = set(current) | set(previous)
    changes: List[ServiceCostChange] = []
    for service in services:
        change = ServiceCostChange(
            service=service,
            current=current.get(service, 0.0),
            previous=previous.get(service, 0.0),
        )
        if abs(change.delta) < min_cost_delta_usd:
            continue
        if abs(change.pct_change) < min_pct_change:
            continue
        changes.append(change)
    return changes


def split_by_direction(
    changes: List[ServiceCostChange],
) -> Tuple[List[ServiceCostChange], List[ServiceCostChange]]:
    decreases = sorted((c for c in changes if c.delta < 0), key=lambda c: c.delta)
    increases = sorted((c for c in changes if c.delta > 0), key=lambda c: c.delta, reverse=True)
    return decreases, increases


def summarise_group(changes: List[ServiceCostChange]) -> Tuple[int, float, float]:
    if not changes:
        return 0, 0.0, 0.0
    total_abs = sum(abs(c.delta) for c in changes)
    avg_pct = sum(c.pct_change for c in changes) / len(changes)
    return len(changes), round(total_abs, 2), round(avg_pct, 2)


def total_cost(window_totals: Dict[str, float]) -> float:
    return round(sum(window_totals.values()), 2)


# ---------- Reserved Instance deltas ---------------------------------------

@dataclass(frozen=True)
class RITotalDelta:
    current: RIUtilizationTotal
    previous: RIUtilizationTotal

    @property
    def utilization_pp(self) -> float:
        return self.current.utilization_pct - self.previous.utilization_pct

    @property
    def purchased_delta(self) -> float:
        return self.current.purchased_hours - self.previous.purchased_hours

    @property
    def used_delta(self) -> float:
        return self.current.used_hours - self.previous.used_hours

    @property
    def unused_delta(self) -> float:
        return self.current.unused_hours - self.previous.unused_hours

    @property
    def unrealized_delta(self) -> float:
        return self.current.unrealized_savings_usd - self.previous.unrealized_savings_usd

    @property
    def net_savings_delta(self) -> float:
        return self.current.net_savings_usd - self.previous.net_savings_usd


@dataclass(frozen=True)
class RICoverageFamilyDelta:
    family: str
    current_pct: float
    previous_pct: float

    @property
    def pp_change(self) -> float:
        return self.current_pct - self.previous_pct


def compute_ri_coverage_family_deltas(
    current: Dict[str, RICoverageByFamily],
    previous: Dict[str, RICoverageByFamily],
    min_pp: float,
) -> List[RICoverageFamilyDelta]:
    """Families with |delta| at or above min_pp, sorted by absolute change desc."""
    families = set(current) | set(previous)
    deltas: List[RICoverageFamilyDelta] = []
    for family in families:
        cur = current.get(family)
        prev = previous.get(family)
        cur_pct = cur.coverage_pct if cur else 0.0
        prev_pct = prev.coverage_pct if prev else 0.0
        delta = RICoverageFamilyDelta(
            family=family,
            current_pct=cur_pct,
            previous_pct=prev_pct,
        )
        if abs(delta.pp_change) >= min_pp:
            deltas.append(delta)
    deltas.sort(key=lambda d: abs(d.pp_change), reverse=True)
    return deltas


def pick_ri_leakers(
    leaks: List[RISubscriptionLeak],
    max_leak_util_pct: float,
    min_unused_hours: float,
    limit: int,
) -> List[RISubscriptionLeak]:
    """Subscriptions whose utilisation is below the threshold AND have meaningful waste.

    Returns the top `limit` leakers by unused hours.
    """
    qualifying = [
        leak for leak in leaks
        if leak.utilization_pct < max_leak_util_pct
        and leak.unused_hours >= min_unused_hours
    ]
    qualifying.sort(key=lambda l: l.unused_hours, reverse=True)
    return qualifying[:limit]


# ---------- Savings Plans deltas -------------------------------------------

@dataclass(frozen=True)
class SPCoverageDelta:
    current: SPCoverage
    previous: SPCoverage

    @property
    def coverage_pp(self) -> float:
        return self.current.coverage_pct - self.previous.coverage_pct

    @property
    def covered_spend_delta(self) -> float:
        return self.current.covered_spend_usd - self.previous.covered_spend_usd

    @property
    def on_demand_spend_delta(self) -> float:
        return self.current.on_demand_spend_usd - self.previous.on_demand_spend_usd


@dataclass(frozen=True)
class SPUtilizationDelta:
    current: SPUtilization
    previous: SPUtilization

    @property
    def utilization_pp(self) -> float:
        return self.current.utilization_pct - self.previous.utilization_pct

    @property
    def used_commitment_delta(self) -> float:
        return self.current.used_commitment_usd - self.previous.used_commitment_usd

    @property
    def unused_commitment_delta(self) -> float:
        return self.current.unused_commitment_usd - self.previous.unused_commitment_usd

    @property
    def total_commitment_delta(self) -> float:
        return self.current.total_commitment_usd - self.previous.total_commitment_usd

    @property
    def net_savings_delta(self) -> float:
        return self.current.net_savings_usd - self.previous.net_savings_usd


@dataclass(frozen=True)
class SPPlanDelta:
    arn: str
    plan_type: str
    payment_option: str
    short_id: str
    current_util_pct: float
    previous_util_pct: float
    current_used_usd: float
    previous_used_usd: float
    current_unused_usd: float
    previous_unused_usd: float
    current_net_savings_usd: float
    previous_net_savings_usd: float

    @property
    def util_pp(self) -> float:
        return self.current_util_pct - self.previous_util_pct

    @property
    def is_new(self) -> bool:
        return self.previous_used_usd == 0.0 and self.previous_unused_usd == 0.0


def compute_sp_plan_deltas(
    current: List[SPPerPlan],
    previous: List[SPPerPlan],
) -> List[SPPlanDelta]:
    """Join current and previous per-plan data on ARN. Returns one delta per plan
    present in either window. Sorted by current used commitment desc."""
    by_arn_prev = {plan.arn: plan for plan in previous}
    by_arn_cur = {plan.arn: plan for plan in current}
    all_arns = set(by_arn_cur) | set(by_arn_prev)

    deltas: List[SPPlanDelta] = []
    for arn in all_arns:
        cur = by_arn_cur.get(arn)
        prev = by_arn_prev.get(arn)
        ref = cur or prev
        deltas.append(SPPlanDelta(
            arn=arn,
            plan_type=ref.plan_type if ref else "Unknown",
            payment_option=ref.payment_option if ref else "Unknown",
            short_id=ref.short_id if ref else arn[:8],
            current_util_pct=cur.utilization_pct if cur else 0.0,
            previous_util_pct=prev.utilization_pct if prev else 0.0,
            current_used_usd=cur.used_commitment_usd if cur else 0.0,
            previous_used_usd=prev.used_commitment_usd if prev else 0.0,
            current_unused_usd=cur.unused_commitment_usd if cur else 0.0,
            previous_unused_usd=prev.unused_commitment_usd if prev else 0.0,
            current_net_savings_usd=cur.net_savings_usd if cur else 0.0,
            previous_net_savings_usd=prev.net_savings_usd if prev else 0.0,
        ))
    deltas.sort(key=lambda d: d.current_used_usd, reverse=True)
    return deltas
