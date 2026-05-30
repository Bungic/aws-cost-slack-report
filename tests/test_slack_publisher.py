from unittest.mock import MagicMock

import pytest
from slack_sdk.errors import SlackApiError

from analysis import ServiceCostChange
from slack_publisher import (
    COLOR_DECREASE,
    COLOR_INCREASE,
    SlackPublisher,
    _money,
    _pct,
    _row,
    build_group_attachment,
    build_group_summary_text,
    build_main_blocks,
    fallback_text,
)


def test_money_formatting_handles_signs():
    assert _money(1234.5) == "$1,234.50"
    assert _money(-12.0) == "-$12.00"
    assert _money(0.0) == "$0.00"


def test_pct_formatting():
    assert _pct(0) == "0.0%"
    assert _pct(12.3) == "+12.3%"
    assert _pct(-7.5) == "-7.5%"


def test_row_renders_service_arrow_and_numbers():
    change = ServiceCostChange("Amazon EC2 - Compute", 250.0, 500.0)
    text = _row(change)
    assert "*Amazon EC2 - Compute*" in text
    assert "$500.00" in text
    assert "$250.00" in text
    assert "↓" in text
    assert "-50.0%" in text


def test_row_has_no_category_emoji():
    change = ServiceCostChange("Amazon S3", 100.0, 80.0)
    text = _row(change)
    forbidden = (
        ":desktop_computer:",
        ":package:",
        ":file_cabinet:",
        ":small_blue_diamond:",
    )
    for marker in forbidden:
        assert marker not in text


def _main_blocks(**overrides):
    defaults = dict(
        current_label="May 1-30, 2026",
        previous_label="Apr 1-30, 2026",
        current_total=100.0,
        previous_total=120.0,
        has_reservations=False,
        has_savings_plans=False,
        min_cost_delta_usd=5.0,
        min_pct_change=10.0,
    )
    defaults.update(overrides)
    return build_main_blocks(**defaults)


def test_main_blocks_have_no_per_service_sections():
    blocks = _main_blocks(has_reservations=True)
    assert len(blocks) == 4
    assert blocks[0]["type"] == "header"
    assert blocks[-1]["type"] == "context"


def test_main_blocks_include_window_and_totals():
    blocks = _main_blocks()
    sections_text = " ".join(
        b["text"]["text"] for b in blocks
        if b["type"] == "section" and isinstance(b.get("text"), dict)
    )
    fields_text = " ".join(
        f["text"]
        for b in blocks
        if b["type"] == "section" and "fields" in b
        for f in b["fields"]
    )
    context_text = " ".join(
        e["text"] for b in blocks if b["type"] == "context" for e in b["elements"]
    )

    assert "May 1-30, 2026" in sections_text
    assert "$100.00" in fields_text
    assert "$120.00" in fields_text
    assert "-16.7%" in fields_text
    assert "$20.00" in fields_text
    assert "Per-service details are in the thread" in context_text


def test_main_blocks_show_no_commitments_footer_when_missing():
    blocks = _main_blocks(has_reservations=False, has_savings_plans=False)
    context_text = " ".join(
        e["text"] for b in blocks if b["type"] == "context" for e in b["elements"]
    )
    assert "No Reserved Instances or Savings Plans detected" in context_text


def test_main_blocks_lists_only_present_commitments():
    only_ri = _main_blocks(has_reservations=True, has_savings_plans=False)
    only_sp = _main_blocks(has_reservations=False, has_savings_plans=True)
    both = _main_blocks(has_reservations=True, has_savings_plans=True)

    def context(blocks):
        return " ".join(
            e["text"] for b in blocks if b["type"] == "context" for e in b["elements"]
        )

    assert "Reserved Instances" in context(only_ri)
    assert "Savings Plans" not in context(only_ri)
    assert "Savings Plans" in context(only_sp)
    assert "Reserved Instances" not in context(only_sp)
    both_text = context(both)
    assert "Reserved Instances" in both_text and "Savings Plans" in both_text


def test_group_attachment_has_green_bar_and_no_internal_header():
    changes = [
        ServiceCostChange("EC2 - Other", 200.0, 400.0),
        ServiceCostChange("ElastiCache", 100.0, 200.0),
    ]
    attachment = build_group_attachment("down", changes)
    assert attachment["color"] == COLOR_DECREASE
    text = attachment["blocks"][0]["text"]["text"]
    # The summary header now lives in the message `text` field, not inside.
    assert ":small_red_triangle_down:" not in text
    assert "services decreased" not in text
    # The per-service rows are still here.
    assert "*EC2 - Other*" in text
    assert "*ElastiCache*" in text


def test_group_attachment_increases_uses_red_bar():
    changes = [ServiceCostChange("Amazon S3", 150.0, 80.0)]
    attachment = build_group_attachment("up", changes)
    assert attachment["color"] == COLOR_INCREASE


def test_group_summary_text_renders_decreases():
    summary = build_group_summary_text("down", count=8, total_abs_delta=21461.88, avg_pct=-37.6)
    assert ":small_red_triangle_down:" in summary
    assert "*8 services decreased*" in summary
    assert "$21,461.88" in summary
    assert "-37.6%" in summary


def test_group_summary_text_renders_increases():
    summary = build_group_summary_text("up", count=6, total_abs_delta=5809.55, avg_pct=15.2)
    assert ":small_red_triangle:" in summary
    assert ":small_red_triangle_down:" not in summary
    assert "*6 services increased*" in summary
    assert "+15.2%" in summary


def test_fallback_text_includes_both_totals():
    text = fallback_text(100.0, 120.0)
    assert "$100.00" in text
    assert "$120.00" in text


def test_publisher_dry_run_does_not_call_slack():
    fake = MagicMock()
    publisher = SlackPublisher(
        token="xoxb-test", channel_id="C123", dry_run=True, client=fake
    )
    publisher.publish_main(blocks=[], text="hello")
    publisher.publish_thread(thread_ts="1.000", attachments=[], text="hello")
    fake.chat_postMessage.assert_not_called()


def test_publisher_returns_ts_from_main_post():
    fake = MagicMock()
    success = MagicMock()
    success.get.side_effect = lambda key, default=None: "12345.000" if key == "ts" else default
    success.data = {"ts": "12345.000", "ok": True}
    fake.chat_postMessage.return_value = success
    publisher = SlackPublisher(token="xoxb-test", channel_id="C123", client=fake)
    ts = publisher.publish_main(blocks=[{"type": "section"}], text="hi")
    assert ts == "12345.000"


def test_publisher_retries_on_retryable_error():
    fake = MagicMock()
    err_response = MagicMock()
    err_response.get.side_effect = lambda key, default=None: "server_error" if key == "error" else default
    err_response.data = {"error": "server_error"}
    error = SlackApiError(message="oops", response=err_response)

    success_response = MagicMock()
    success_response.get.side_effect = lambda key, default=None: "12345.000" if key == "ts" else default
    success_response.data = {"ts": "12345.000", "ok": True}

    fake.chat_postMessage.side_effect = [error, success_response]
    publisher = SlackPublisher(token="xoxb-test", channel_id="C123", client=fake)
    import slack_publisher
    real_sleep = slack_publisher.time.sleep
    slack_publisher.time.sleep = lambda _: None
    try:
        ts = publisher.publish_main(blocks=[], text="hi")
    finally:
        slack_publisher.time.sleep = real_sleep
    assert fake.chat_postMessage.call_count == 2
    assert ts == "12345.000"


def test_publisher_does_not_retry_permanent_error():
    fake = MagicMock()
    err_response = MagicMock()
    err_response.get.side_effect = lambda key, default=None: "channel_not_found" if key == "error" else default
    err_response.data = {"error": "channel_not_found"}
    error = SlackApiError(message="nope", response=err_response)
    fake.chat_postMessage.side_effect = error
    publisher = SlackPublisher(token="xoxb-test", channel_id="C123", client=fake)
    with pytest.raises(SlackApiError):
        publisher.publish_main(blocks=[], text="hi")
    fake.chat_postMessage.assert_called_once()
