# Changelog

## 1.0.0 - 2026-05-30

Initial public release.

How it works:

- Compares the last `WINDOW_DAYS` (default 30) of `UnblendedCost` against the preceding window of the same length, grouped by AWS service.
- Posts a short summary message to Slack: window labels, current and previous totals, absolute change, percent change.
- Posts threaded replies, each one a single attachment with a colored side bar. Green for cost decreases. Red for cost increases. Blue for Reserved Instances. Purple for Savings Plans. The header line in every thread gives the headline numbers; the body lists the per-item detail.
- Reserved Instances are tracked only when the account holds at least one RI. Coverage is broken down by instance family (only families whose coverage moved by `MIN_RI_PCT_POINTS` or more are shown). The thread also lists the top leaking subscriptions, capped at `MAX_RI_LEAK_SUBSCRIPTIONS`.
- Savings Plans are tracked only when the account holds at least one SP. Both coverage and utilization deltas are reported, plus a per-plan breakdown joined against `DescribeSavingsPlans` so each plan is labelled with its type (Compute, EC2, SageMaker, Database) and payment option.
- Accounts without RIs and without SPs get a single footer note on the summary message and no extra threads.

Configuration:

- Required: `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`. Missing either one fails fast at import time.
- Optional thresholds: `MIN_COST_DELTA_USD` (5.0), `MIN_PCT_CHANGE` (10.0), `MIN_RI_PCT_POINTS` (5.0), `MAX_RI_LEAK_UTIL_PCT` (80.0), `MIN_RI_LEAK_HOURS` (100.0), `MAX_RI_LEAK_SUBSCRIPTIONS` (5).
- Optional behavior: `WINDOW_DAYS` (30), `DRY_RUN` (false), `LOG_LEVEL` (INFO).

Reliability:

- `slack_sdk` posts retry with exponential backoff on `server_error`, `service_unavailable`, `fatal_error`, `ratelimited`. Permanent errors (`channel_not_found`, `invalid_auth`, etc.) raise immediately.
- Structured logging via `logging`. The startup log emits the windows and the running counts so a Lambda invoke without Slack output is still debuggable from CloudWatch.
- `DRY_RUN=true` runs the full pipeline without posting to Slack.
- `DescribeSavingsPlans` is treated as best-effort. If the call fails (for example because the IAM policy is missing `savingsplans:DescribeSavingsPlans`), plans still appear in the per-plan section but get labelled `Unknown`. The bot keeps running.

Code layout:

- `lambda_function.py` orchestrates. The logic lives in `config.py`, `cost_explorer.py`, `analysis.py`, `slack_publisher.py`.
- 53 unit tests under `tests/` cover the analysis layer, the Slack formatter, the config loader, the date-range helpers, and the new RI/SP delta computation. They run without AWS or Slack credentials.

Permissions:

- `ce:GetCostAndUsage` for cost.
- `ce:GetReservationUtilization`, `ce:GetReservationCoverage` for Reserved Instances.
- `ce:GetSavingsPlansCoverage`, `ce:GetSavingsPlansUtilization`, `ce:GetSavingsPlansUtilizationDetails` for Savings Plans.
- `savingsplans:DescribeSavingsPlans` for the per-plan type/payment labels.
- `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` for CloudWatch.
