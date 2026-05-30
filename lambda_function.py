"""AWS Lambda entry point. Glues config, Cost Explorer, analysis, and Slack."""
import json
import logging
import sys
from datetime import date

from analysis import (
    RITotalDelta,
    SPCoverageDelta,
    SPUtilizationDelta,
    compute_cost_changes,
    compute_ri_coverage_family_deltas,
    compute_sp_plan_deltas,
    pick_ri_leakers,
    split_by_direction,
    summarise_group,
    total_cost,
)
from config import load_config
from cost_explorer import (
    CostExplorer,
    build_windows,
    has_any_reservations,
    has_any_savings_plans,
)
from slack_publisher import (
    SlackPublisher,
    build_group_attachment,
    build_group_summary_text,
    build_main_blocks,
    build_ri_attachment,
    build_ri_summary_text,
    build_sp_attachment,
    build_sp_summary_text,
    fallback_text,
)


def _setup_logging(level: str) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    try:
        root.setLevel(level)
    except ValueError:
        root.setLevel(logging.INFO)


def _post_cost_threads(publisher, ts, changes, logger):
    decreases, increases = split_by_direction(changes)
    if ts and decreases:
        count, total_abs, avg_pct = summarise_group(decreases)
        logger.info("posting decreases thread count=%d total_abs=%.2f", count, total_abs)
        publisher.publish_thread(
            thread_ts=ts,
            attachments=[build_group_attachment("down", decreases)],
            text=build_group_summary_text("down", count, total_abs, avg_pct),
        )
    if ts and increases:
        count, total_abs, avg_pct = summarise_group(increases)
        logger.info("posting increases thread count=%d total_abs=%.2f", count, total_abs)
        publisher.publish_thread(
            thread_ts=ts,
            attachments=[build_group_attachment("up", increases)],
            text=build_group_summary_text("up", count, total_abs, avg_pct),
        )


def _post_ri_thread(publisher, ts, ce, cfg, current_window, previous_window, logger):
    util_current = ce.ri_utilization_total(current_window)
    util_previous = ce.ri_utilization_total(previous_window)
    coverage_current = ce.ri_coverage_by_family(current_window)
    coverage_previous = ce.ri_coverage_by_family(previous_window)
    leakers_current = ce.ri_subscription_leaks(current_window)

    delta = RITotalDelta(current=util_current, previous=util_previous)
    family_deltas = compute_ri_coverage_family_deltas(
        coverage_current, coverage_previous, cfg.min_ri_pct_points,
    )
    leakers = pick_ri_leakers(
        leakers_current,
        max_leak_util_pct=cfg.max_ri_leak_util_pct,
        min_unused_hours=cfg.min_ri_leak_hours,
        limit=cfg.max_ri_leak_subscriptions,
    )

    logger.info(
        "ri thread current_util=%.1f%% prev_util=%.1f%% family_deltas=%d leakers=%d",
        util_current.utilization_pct, util_previous.utilization_pct,
        len(family_deltas), len(leakers),
    )

    publisher.publish_thread(
        thread_ts=ts,
        attachments=[build_ri_attachment(delta, family_deltas, leakers)],
        text=build_ri_summary_text(delta),
    )


def _post_sp_thread(publisher, ts, ce, current_window, previous_window, logger):
    coverage_current = ce.sp_coverage(current_window)
    coverage_previous = ce.sp_coverage(previous_window)
    util_current = ce.sp_utilization_total(current_window)
    util_previous = ce.sp_utilization_total(previous_window)
    plans_current = ce.sp_per_plan(current_window)
    plans_previous = ce.sp_per_plan(previous_window)

    coverage_delta = SPCoverageDelta(current=coverage_current, previous=coverage_previous)
    util_delta = SPUtilizationDelta(current=util_current, previous=util_previous)
    plan_deltas = compute_sp_plan_deltas(plans_current, plans_previous)

    logger.info(
        "sp thread coverage=%.1f%% (was %.1f%%) util=%.1f%% (was %.1f%%) plans=%d",
        coverage_current.coverage_pct, coverage_previous.coverage_pct,
        util_current.utilization_pct, util_previous.utilization_pct,
        len(plan_deltas),
    )

    publisher.publish_thread(
        thread_ts=ts,
        attachments=[build_sp_attachment(coverage_delta, util_delta, plan_deltas)],
        text=build_sp_summary_text(coverage_delta, util_delta),
    )


def _run(today: date) -> dict:
    cfg = load_config()
    _setup_logging(cfg.log_level)
    logger = logging.getLogger(__name__)

    ce = CostExplorer()
    current_window, previous_window = build_windows(today, cfg.window_days)
    logger.info(
        "windows current=%s previous=%s",
        current_window.label, previous_window.label,
    )

    current_cost = ce.cost_by_service(current_window)
    previous_cost = ce.cost_by_service(previous_window)

    current_ri_total = ce.ri_utilization_total(current_window)
    current_sp_total = ce.sp_utilization_total(current_window)
    reservations_present = has_any_reservations(current_ri_total)
    savings_plans_present = has_any_savings_plans(current_sp_total)
    logger.info(
        "reservations=%s savings_plans=%s",
        reservations_present, savings_plans_present,
    )

    changes = compute_cost_changes(
        current=current_cost,
        previous=previous_cost,
        min_cost_delta_usd=cfg.min_cost_delta_usd,
        min_pct_change=cfg.min_pct_change,
    )
    decreases, increases = split_by_direction(changes)
    current_total = total_cost(current_cost)
    previous_total = total_cost(previous_cost)
    logger.info(
        "changes total=%d decreases=%d increases=%d current_total=%.2f previous_total=%.2f",
        len(changes), len(decreases), len(increases), current_total, previous_total,
    )

    main_blocks = build_main_blocks(
        current_label=current_window.label,
        previous_label=previous_window.label,
        current_total=current_total,
        previous_total=previous_total,
        has_reservations=reservations_present,
        has_savings_plans=savings_plans_present,
        min_cost_delta_usd=cfg.min_cost_delta_usd,
        min_pct_change=cfg.min_pct_change,
    )

    publisher = SlackPublisher(
        token=cfg.slack_bot_token,
        channel_id=cfg.slack_channel_id,
        dry_run=cfg.dry_run,
    )
    fallback = fallback_text(current_total, previous_total)
    ts = publisher.publish_main(blocks=main_blocks, text=fallback)

    if ts:
        _post_cost_threads(publisher, ts, changes, logger)
        if reservations_present:
            _post_ri_thread(publisher, ts, ce, cfg, current_window, previous_window, logger)
        if savings_plans_present:
            _post_sp_thread(publisher, ts, ce, current_window, previous_window, logger)

    return {
        "current_total": current_total,
        "previous_total": previous_total,
        "service_changes": len(changes),
        "decreases": len(decreases),
        "increases": len(increases),
        "reservations_present": reservations_present,
        "savings_plans_present": savings_plans_present,
        "dry_run": cfg.dry_run,
        "main_ts": ts,
    }


def lambda_handler(event, context):  # noqa: ARG001 — Lambda signature
    result = _run(today=date.today())
    return {"statusCode": 200, "body": json.dumps(result)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(_run(today=date.today()))
