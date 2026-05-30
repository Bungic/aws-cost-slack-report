"""Slack Block Kit formatter + WebClient with retry/backoff.

The main message is intentionally short: header, window labels, the four
totals, and a one-line filter footer. All per-service detail goes into
thread replies, so the channel view stays clean and Slack does not collapse
the main message behind a "Show more" link.

Each thread reply is one attachment with a colored side bar:
  green   for cost decreases
  red     for cost increases
  blue    for Reserved Instance status
  purple  for Savings Plans status

The first line of every thread reply lives in the message's `text` field
(rendered as mrkdwn above the attachment) so the same header is not also
printed inside the attachment.
"""
import logging
import random
import time
from typing import List, Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from analysis import (
    RICoverageFamilyDelta,
    RITotalDelta,
    SPCoverageDelta,
    SPPlanDelta,
    SPUtilizationDelta,
    ServiceCostChange,
)
from cost_explorer import RISubscriptionLeak

logger = logging.getLogger(__name__)


COLOR_DECREASE = "#36a64f"
COLOR_INCREASE = "#e01e5a"
COLOR_RI = "#4a90e2"
COLOR_SP = "#9b59b6"


# ---------- Number formatting ---------------------------------------------

def _money(amount: float) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def _pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def _pp(value: float) -> str:
    """Percentage points (used for percent-of-percent deltas)."""
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}pp"


def _hours(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}h"
    return f"{value:,.2f}h"


def _arrow(direction: str) -> str:
    return {"up": "↑", "down": "↓", "flat": "→"}.get(direction, "→")


def _delta_arrow(value: float) -> str:
    if value > 0:
        return "↑"
    if value < 0:
        return "↓"
    return "→"


# ---------- Cost-change row -----------------------------------------------

def _row(change: ServiceCostChange) -> str:
    arrow = _arrow(change.direction)
    return (
        f"*{change.service}*\n"
        f"{_money(change.previous)} → {_money(change.current)}  "
        f"{arrow} {_money(abs(change.delta))} ({_pct(change.pct_change)})"
    )


# ---------- Block helpers --------------------------------------------------

def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _attachment(color: str, text: str) -> dict:
    return {"color": color, "blocks": [_section(text)]}


# ---------- Main message ---------------------------------------------------

def build_main_blocks(
    current_label: str,
    previous_label: str,
    current_total: float,
    previous_total: float,
    has_reservations: bool,
    has_savings_plans: bool,
    min_cost_delta_usd: float,
    min_pct_change: float,
) -> List[dict]:
    delta = current_total - previous_total
    pct = (delta / previous_total * 100.0) if previous_total > 0 else 0.0
    direction = "up" if delta > 0 else ("down" if delta < 0 else "flat")
    arrow = _arrow(direction)

    blocks: List[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "AWS Cost Report", "emoji": False},
        },
        _section(
            f"*Window:* `{current_label}`\n"
            f"*Compared to:* `{previous_label}`"
        ),
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Current*\n{_money(current_total)}"},
                {"type": "mrkdwn", "text": f"*Previous*\n{_money(previous_total)}"},
                {"type": "mrkdwn", "text": f"*Change*\n{arrow} {_money(abs(delta))}"},
                {"type": "mrkdwn", "text": f"*Percent*\n{_pct(pct)}"},
            ],
        },
    ]

    footer_lines = [
        (
            f"Filter: services with at least {_money(min_cost_delta_usd)} "
            f"absolute change and {min_pct_change:.0f}% relative change. "
            "Per-service details are in the thread."
        ),
    ]
    if has_reservations or has_savings_plans:
        present = []
        if has_reservations:
            present.append("Reserved Instances")
        if has_savings_plans:
            present.append("Savings Plans")
        footer_lines.append(
            f"Commitments detected: {' + '.join(present)}. Details follow in the thread."
        )
    else:
        footer_lines.append(
            "No Reserved Instances or Savings Plans detected, commitment tracking skipped."
        )

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": line} for line in footer_lines],
    })

    return blocks


# ---------- Cost-change threads -------------------------------------------

def build_group_summary_text(
    direction: str, count: int, total_abs_delta: float, avg_pct: float
) -> str:
    if direction == "down":
        emoji = ":small_red_triangle_down:"
        verb = "decreased"
    else:
        emoji = ":small_red_triangle:"
        verb = "increased"
    return (
        f"{emoji}  *{count} services {verb}*  —  "
        f"{_money(total_abs_delta)} ({_pct(avg_pct)} avg)"
    )


def build_group_attachment(direction: str, changes: List[ServiceCostChange]) -> dict:
    color = COLOR_DECREASE if direction == "down" else COLOR_INCREASE
    body = "\n\n".join(_row(c) for c in changes)
    return _attachment(color, body)


# ---------- Reserved Instances thread -------------------------------------

def build_ri_summary_text(delta: RITotalDelta) -> str:
    util_pct = delta.current.utilization_pct
    return (
        f":large_blue_diamond:  *Reserved Instances — {util_pct:.1f}% utilization*  "
        f"({_pp(delta.utilization_pp)} vs previous, "
        f"{_money(delta.current.unrealized_savings_usd)} left unused)"
    )


def _ri_row(label: str, current: str, previous: str, delta: str) -> str:
    return f"*{label}*\n{current}   (was {previous}, {delta})"


def build_ri_attachment(
    delta: RITotalDelta,
    family_deltas: List[RICoverageFamilyDelta],
    leakers: List[RISubscriptionLeak],
) -> dict:
    rows: List[str] = [
        _ri_row(
            "Purchased",
            _hours(delta.current.purchased_hours),
            _hours(delta.previous.purchased_hours),
            f"{_delta_arrow(delta.purchased_delta)} {_hours(abs(delta.purchased_delta))}",
        ),
        _ri_row(
            "Used",
            _hours(delta.current.used_hours),
            _hours(delta.previous.used_hours),
            f"{_delta_arrow(delta.used_delta)} {_hours(abs(delta.used_delta))}",
        ),
        _ri_row(
            "Unused",
            _hours(delta.current.unused_hours),
            _hours(delta.previous.unused_hours),
            f"{_delta_arrow(delta.unused_delta)} {_hours(abs(delta.unused_delta))}",
        ),
        _ri_row(
            "Unrealized savings",
            _money(delta.current.unrealized_savings_usd),
            _money(delta.previous.unrealized_savings_usd),
            f"{_delta_arrow(delta.unrealized_delta)} {_money(abs(delta.unrealized_delta))}",
        ),
        _ri_row(
            "Net savings",
            _money(delta.current.net_savings_usd),
            _money(delta.previous.net_savings_usd),
            f"{_delta_arrow(delta.net_savings_delta)} {_money(abs(delta.net_savings_delta))}",
        ),
    ]

    if family_deltas:
        rows.append("\n*Coverage by instance family*")
        for fd in family_deltas:
            rows.append(
                f"`{fd.family:<8}` {fd.current_pct:5.1f}%  (was {fd.previous_pct:5.1f}%, "
                f"{_delta_arrow(fd.pp_change)} {_pp(fd.pp_change)})"
            )

    if leakers:
        rows.append("\n*Underutilized subscriptions (top by unused hours)*")
        for leak in leakers:
            short = leak.subscription_id[-12:] if len(leak.subscription_id) > 12 else leak.subscription_id
            rows.append(
                f"`{short}`  {leak.utilization_pct:.1f}% util, "
                f"{_hours(leak.unused_hours)} unused, "
                f"{_money(leak.unrealized_savings_usd)} left on the table"
            )

    body = "\n\n".join(rows)
    return _attachment(COLOR_RI, body)


# ---------- Savings Plans thread ------------------------------------------

def build_sp_summary_text(
    coverage_delta: SPCoverageDelta, utilization_delta: SPUtilizationDelta
) -> str:
    return (
        f":large_purple_circle:  *Savings Plans — "
        f"{coverage_delta.current.coverage_pct:.1f}% coverage, "
        f"{utilization_delta.current.utilization_pct:.1f}% utilization*  "
        f"(net savings {_money(utilization_delta.current.net_savings_usd)})"
    )


def build_sp_attachment(
    coverage_delta: SPCoverageDelta,
    utilization_delta: SPUtilizationDelta,
    plan_deltas: List[SPPlanDelta],
) -> dict:
    rows: List[str] = ["*Coverage*"]
    rows.append(
        f"{coverage_delta.current.coverage_pct:.1f}%  "
        f"(was {coverage_delta.previous.coverage_pct:.1f}%, "
        f"{_delta_arrow(coverage_delta.coverage_pp)} {_pp(coverage_delta.coverage_pp)})"
    )
    rows.append(
        f"Covered: {_money(coverage_delta.current.covered_spend_usd)}  "
        f"(was {_money(coverage_delta.previous.covered_spend_usd)}, "
        f"{_delta_arrow(coverage_delta.covered_spend_delta)} {_money(abs(coverage_delta.covered_spend_delta))})"
    )
    rows.append(
        f"On-demand: {_money(coverage_delta.current.on_demand_spend_usd)}  "
        f"(was {_money(coverage_delta.previous.on_demand_spend_usd)}, "
        f"{_delta_arrow(coverage_delta.on_demand_spend_delta)} {_money(abs(coverage_delta.on_demand_spend_delta))})"
    )

    rows.append("\n*Utilization*")
    rows.append(
        f"{utilization_delta.current.utilization_pct:.1f}%  "
        f"(was {utilization_delta.previous.utilization_pct:.1f}%, "
        f"{_delta_arrow(utilization_delta.utilization_pp)} {_pp(utilization_delta.utilization_pp)})"
    )
    rows.append(
        f"Commitment used: {_money(utilization_delta.current.used_commitment_usd)}  "
        f"(was {_money(utilization_delta.previous.used_commitment_usd)})"
    )
    rows.append(
        f"Unused: {_money(utilization_delta.current.unused_commitment_usd)}  "
        f"(was {_money(utilization_delta.previous.unused_commitment_usd)}, "
        f"{_delta_arrow(utilization_delta.unused_commitment_delta)} "
        f"{_money(abs(utilization_delta.unused_commitment_delta))})"
    )
    rows.append(
        f"Net savings: {_money(utilization_delta.current.net_savings_usd)}  "
        f"(was {_money(utilization_delta.previous.net_savings_usd)}, "
        f"{_delta_arrow(utilization_delta.net_savings_delta)} "
        f"{_money(abs(utilization_delta.net_savings_delta))})"
    )

    if plan_deltas:
        rows.append("\n*Per plan*")
        for p in plan_deltas:
            new_tag = "  🆕" if p.is_new else ""
            rows.append(
                f"`{p.short_id}` *{p.plan_type}* ({p.payment_option}){new_tag}\n"
                f"        {p.current_util_pct:.1f}% util "
                f"(was {p.previous_util_pct:.1f}%, {_delta_arrow(p.util_pp)} {_pp(p.util_pp)})  ·  "
                f"used {_money(p.current_used_usd)}  ·  "
                f"net savings {_money(p.current_net_savings_usd)}"
            )

    body = "\n\n".join(rows)
    return _attachment(COLOR_SP, body)


# ---------- Fallback / notification text ----------------------------------

def fallback_text(current_total: float, previous_total: float) -> str:
    delta = current_total - previous_total
    return (
        f"AWS cost report: {_money(current_total)} this window "
        f"(was {_money(previous_total)}, change {_money(delta)})."
    )


# ---------- Posting client -------------------------------------------------

class SlackPublisher:
    """Posts a Block Kit message with retry on transient errors, plus thread replies."""

    MAX_ATTEMPTS = 5
    RETRYABLE = {"server_error", "fatal_error", "service_unavailable", "ratelimited"}

    def __init__(
        self,
        token: str,
        channel_id: str,
        dry_run: bool = False,
        client: Optional[WebClient] = None,
    ):
        self._channel = channel_id
        self._dry_run = dry_run
        self._client = client or WebClient(token=token)

    def publish_main(self, blocks: List[dict], text: str) -> Optional[str]:
        if self._dry_run:
            logger.info("DRY_RUN active, would post main: text=%r blocks=%d", text, len(blocks))
            return None
        response = self._post(text=text, blocks=blocks)
        return response.get("ts") if response else None

    def publish_thread(
        self, thread_ts: str, attachments: List[dict], text: str,
    ) -> None:
        if self._dry_run:
            logger.info(
                "DRY_RUN active, would post thread: ts=%s text=%r attachments=%d",
                thread_ts, text, len(attachments),
            )
            return
        self._post(text=text, attachments=attachments, thread_ts=thread_ts)

    def _post(self, **kwargs) -> Optional[dict]:
        last_error: Optional[BaseException] = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                response = self._client.chat_postMessage(channel=self._channel, **kwargs)
                logger.info("slack post ok ts=%s", response.get("ts"))
                return response.data
            except SlackApiError as exc:
                code = exc.response.get("error", "unknown")
                if code not in self.RETRYABLE or attempt == self.MAX_ATTEMPTS:
                    logger.error("slack post failed permanently: %s", code)
                    raise
                last_error = exc
                wait = min(2 ** attempt, 30) + random.uniform(0, 0.5)
                logger.warning(
                    "slack post failed (%s), retry %d/%d in %.1fs",
                    code, attempt, self.MAX_ATTEMPTS, wait,
                )
                time.sleep(wait)
            except Exception as exc:
                if attempt == self.MAX_ATTEMPTS:
                    raise
                last_error = exc
                wait = min(2 ** attempt, 30) + random.uniform(0, 0.5)
                logger.warning(
                    "slack post raised %s, retry %d/%d in %.1fs",
                    exc.__class__.__name__, attempt, self.MAX_ATTEMPTS, wait,
                )
                time.sleep(wait)

        if last_error:
            raise last_error
        return None
