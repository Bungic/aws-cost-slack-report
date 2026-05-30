"""Thin wrappers around the AWS Cost Explorer + Savings Plans APIs.

Returns plain dicts and dataclasses so the analysis layer can be unit-tested
without boto3.
"""
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import boto3

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date  # exclusive, matches Cost Explorer's TimePeriod semantics

    @property
    def label(self) -> str:
        last = self.end - timedelta(days=1)
        if self.start.year == last.year and self.start.month == last.month:
            return f"{self.start.strftime('%b %d')}-{last.day:02d}, {self.start.year}"
        return f"{self.start.strftime('%b %d, %Y')} – {last.strftime('%b %d, %Y')}"

    def to_api(self) -> Dict[str, str]:
        return {"Start": self.start.isoformat(), "End": self.end.isoformat()}


def build_windows(today: date, window_days: int) -> Tuple[DateRange, DateRange]:
    """Two equally sized trailing windows. `today` is the exclusive upper bound.

    Comparing equal-length windows avoids the partial-month gotcha that
    `relativedelta(months=1)` introduces around month boundaries.
    """
    current_end = today
    current_start = current_end - timedelta(days=window_days)
    previous_end = current_start
    previous_start = previous_end - timedelta(days=window_days)
    return (
        DateRange(start=current_start, end=current_end),
        DateRange(start=previous_start, end=previous_end),
    )


# ---------- Reserved Instances ---------------------------------------------

@dataclass(frozen=True)
class RIUtilizationTotal:
    purchased_hours: float
    used_hours: float
    unused_hours: float
    utilization_pct: float
    unrealized_savings_usd: float
    net_savings_usd: float

    @classmethod
    def empty(cls) -> "RIUtilizationTotal":
        return cls(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class RICoverageByFamily:
    family: str
    coverage_pct: float
    reserved_hours: float
    on_demand_hours: float


@dataclass(frozen=True)
class RISubscriptionLeak:
    subscription_id: str
    utilization_pct: float
    purchased_hours: float
    unused_hours: float
    unrealized_savings_usd: float


# ---------- Savings Plans ---------------------------------------------------

@dataclass(frozen=True)
class SPCoverage:
    coverage_pct: float
    covered_spend_usd: float
    on_demand_spend_usd: float
    total_cost_usd: float

    @classmethod
    def empty(cls) -> "SPCoverage":
        return cls(0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class SPUtilization:
    utilization_pct: float
    total_commitment_usd: float
    used_commitment_usd: float
    unused_commitment_usd: float
    net_savings_usd: float

    @classmethod
    def empty(cls) -> "SPUtilization":
        return cls(0.0, 0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class SPPerPlan:
    arn: str
    plan_type: str
    payment_option: str
    utilization_pct: float
    used_commitment_usd: float
    unused_commitment_usd: float
    net_savings_usd: float

    @property
    def short_id(self) -> str:
        return self.arn.rsplit("/", 1)[-1][:8] if "/" in self.arn else self.arn[:8]


# ---------- Boto wrappers ---------------------------------------------------

class CostExplorer:
    """Wraps the cost-side APIs. One instance per Lambda invocation."""

    def __init__(self, ce_client=None, sp_client=None):
        self._ce = ce_client or boto3.client("ce")
        self._sp = sp_client or boto3.client("savingsplans")

    # --- cost --------------------------------------------------------------

    def cost_by_service(self, window: DateRange) -> Dict[str, float]:
        response = self._ce.get_cost_and_usage(
            TimePeriod=window.to_api(),
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        totals: Dict[str, float] = {}
        for period in response.get("ResultsByTime", []):
            for group in period.get("Groups", []):
                service = group["Keys"][0]
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                totals[service] = totals.get(service, 0.0) + amount
        logger.info(
            "cost_by_service window=%s services=%d total=%.2f",
            window.label, len(totals), sum(totals.values()),
        )
        return totals

    # --- RIs ---------------------------------------------------------------

    def ri_utilization_total(self, window: DateRange) -> RIUtilizationTotal:
        response = self._ce.get_reservation_utilization(TimePeriod=window.to_api())
        total = response.get("Total", {}) or {}
        if not total:
            return RIUtilizationTotal.empty()
        return RIUtilizationTotal(
            purchased_hours=float(total.get("PurchasedHours", 0)),
            used_hours=float(total.get("TotalActualHours", 0)),
            unused_hours=float(total.get("UnusedHours", 0)),
            utilization_pct=float(total.get("UtilizationPercentage", 0)),
            unrealized_savings_usd=float(total.get("UnrealizedSavings", 0)),
            net_savings_usd=float(total.get("NetRISavings", 0)),
        )

    def ri_subscription_leaks(self, window: DateRange) -> List[RISubscriptionLeak]:
        response = self._ce.get_reservation_utilization(
            TimePeriod=window.to_api(),
            GroupBy=[{"Type": "DIMENSION", "Key": "SUBSCRIPTION_ID"}],
        )
        leaks: List[RISubscriptionLeak] = []
        for period in response.get("UtilizationsByTime", []):
            for group in period.get("Groups", []):
                util = group.get("Utilization", {}) or {}
                unused = float(util.get("UnusedHours", 0))
                if unused <= 0:
                    continue
                subscription_id = (
                    (group.get("Attributes") or {}).get("subscriptionId")
                    or group.get("Key")
                    or "Unknown"
                )
                leaks.append(RISubscriptionLeak(
                    subscription_id=subscription_id,
                    utilization_pct=float(util.get("UtilizationPercentage", 0)),
                    purchased_hours=float(util.get("PurchasedHours", 0)),
                    unused_hours=unused,
                    unrealized_savings_usd=float(util.get("UnrealizedSavings", 0)),
                ))
        return leaks

    def ri_coverage_by_family(self, window: DateRange) -> Dict[str, RICoverageByFamily]:
        response = self._ce.get_reservation_coverage(
            TimePeriod=window.to_api(),
            GroupBy=[{"Type": "DIMENSION", "Key": "INSTANCE_TYPE_FAMILY"}],
        )
        data: Dict[str, RICoverageByFamily] = {}
        for item in response.get("CoveragesByTime", []):
            for group in item.get("Groups", []):
                family = (group.get("Attributes") or {}).get("instanceTypeFamily", "Unknown")
                hours = group.get("Coverage", {}).get("CoverageHours", {}) or {}
                data[family] = RICoverageByFamily(
                    family=family,
                    coverage_pct=float(hours.get("CoverageHoursPercentage", 0)),
                    reserved_hours=float(hours.get("ReservedHours", 0)),
                    on_demand_hours=float(hours.get("OnDemandHours", 0)),
                )
        return data

    # --- Savings Plans -----------------------------------------------------

    def sp_coverage(self, window: DateRange) -> SPCoverage:
        response = self._ce.get_savings_plans_coverage(TimePeriod=window.to_api())
        rows = response.get("SavingsPlansCoverages", []) or []
        if not rows:
            return SPCoverage.empty()
        covered = sum(float(r.get("Coverage", {}).get("SpendCoveredBySavingsPlans", 0)) for r in rows)
        on_demand = sum(float(r.get("Coverage", {}).get("OnDemandCost", 0)) for r in rows)
        total = sum(float(r.get("Coverage", {}).get("TotalCost", 0)) for r in rows)
        pct = (covered / total * 100.0) if total > 0 else 0.0
        return SPCoverage(
            coverage_pct=pct,
            covered_spend_usd=covered,
            on_demand_spend_usd=on_demand,
            total_cost_usd=total,
        )

    def sp_utilization_total(self, window: DateRange) -> SPUtilization:
        response = self._ce.get_savings_plans_utilization(TimePeriod=window.to_api())
        total = response.get("Total", {}) or {}
        if not total:
            return SPUtilization.empty()
        util = total.get("Utilization", {}) or {}
        savings = total.get("Savings", {}) or {}
        return SPUtilization(
            utilization_pct=float(util.get("UtilizationPercentage", 0)),
            total_commitment_usd=float(util.get("TotalCommitment", 0)),
            used_commitment_usd=float(util.get("UsedCommitment", 0)),
            unused_commitment_usd=float(util.get("UnusedCommitment", 0)),
            net_savings_usd=float(savings.get("NetSavings", 0)),
        )

    def sp_per_plan(self, window: DateRange) -> List[SPPerPlan]:
        # Per-plan utilization details
        details_resp = self._ce.get_savings_plans_utilization_details(
            TimePeriod=window.to_api()
        )
        details = details_resp.get("SavingsPlansUtilizationDetails", []) or []

        # Plan metadata (type, payment option) — join on ARN
        try:
            describe_resp = self._sp.describe_savings_plans(states=["active"])
            plans = describe_resp.get("savingsPlans", []) or []
            metadata = {
                plan["savingsPlanArn"]: (
                    plan.get("savingsPlanType", "Unknown"),
                    plan.get("paymentOption", "Unknown"),
                )
                for plan in plans
                if plan.get("savingsPlanArn")
            }
        except Exception as exc:
            logger.warning("describe_savings_plans failed, plans will be labelled Unknown: %s", exc)
            metadata = {}

        result: List[SPPerPlan] = []
        for row in details:
            arn = row.get("SavingsPlanArn", "")
            util = row.get("Utilization", {}) or {}
            savings = row.get("Savings", {}) or {}
            plan_type, payment_option = metadata.get(arn, ("Unknown", "Unknown"))
            result.append(SPPerPlan(
                arn=arn,
                plan_type=plan_type,
                payment_option=payment_option,
                utilization_pct=float(util.get("UtilizationPercentage", 0)),
                used_commitment_usd=float(util.get("UsedCommitment", 0)),
                unused_commitment_usd=float(util.get("UnusedCommitment", 0)),
                net_savings_usd=float(savings.get("NetSavings", 0)),
            ))
        return result


# ---------- helpers --------------------------------------------------------

def has_any_reservations(utilization: RIUtilizationTotal) -> bool:
    """An account holds Reserved Instances iff it purchased commitment hours."""
    return utilization.purchased_hours > 0.0


def has_any_savings_plans(utilization: SPUtilization) -> bool:
    """An account has Savings Plans iff it made any dollar commitment."""
    return utilization.total_commitment_usd > 0.0
