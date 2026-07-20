# billing-bot

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) ![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white) ![AWS Lambda](https://img.shields.io/badge/AWS%20Lambda-FF9900?logo=awslambda&logoColor=white)

An AWS Lambda that compares your last 30 days of spend against the 30 days before that and drops a clean monthly report into a Slack channel. A short summary message at the top of the channel, plus threaded replies that go deep on each category: per-service cost changes, Reserved Instance health, and Savings Plans coverage.

Works the same on a hobby account with a $50 bill or a multi-million-dollar production org. No agents, no daemons, no databases. One Lambda, one cron, one Slack channel.

## What the report looks like

The summary message is intentionally short. Window, four totals, one filter footer. Nothing else.

```
AWS Cost Report
Window:       Apr 30, 2026 – May 29, 2026
Compared to:  Mar 31, 2026 – Apr 29, 2026

Current        Previous       Change            Percent
$24,077.03     $17,063.13     ↑ $7,013.90       +41.1%

Filter: services with at least $5.00 absolute change and 10% relative change.
Per-service details are in the thread.
Commitments detected: Reserved Instances + Savings Plans. Details follow in the thread.
```

Then come the thread replies. Each one is a single attachment with a colored side bar so the category is obvious before you read it: green for cost decreases, red for cost increases, blue for Reserved Instances, purple for Savings Plans.

```
🔻 4 services decreased — $396.61 (-22.5% avg)
[green bar]
Amazon Bedrock
$1,820.10 → $1,420.30  ↓ $399.80 (-22.0%)
...

🔺 8 services increased — $7,709.39 (+89.4% avg)
[red bar]
Amazon Elastic Compute Cloud - Compute
$244.60 → $4,047.00  ↑ $3,802.40 (+1554.5%)
...

🔷 Reserved Instances — 73.6% utilization (-2.4pp vs previous, $284.29 left unused)
[blue bar]
Purchased        120,408h   (was 118,200h, ↑ 2,208h)
Used              88,658h   (was 89,628h, ↓ 970h)
Unused            31,749h   (was 28,572h, ↑ 3,177h)
Unrealized savings  $284    (was $215, ↑ $69)
Net savings       $1,809    (was $1,723, ↑ $86)

Coverage by instance family
`m6a   `  89.4%  (was 72.3%, ↑ +17.0pp)
`t3    `  54.8%  (was 48.1%, ↑ +6.7pp)
`r7a   `   0.0%  (was 100.0%, ↓ -100.0pp)

Underutilized subscriptions (top by unused hours)
`abc123def456`  46.9% util, 12,568h unused, $30 left on the table
`...`
...

🟣 Savings Plans — 67.6% coverage, 98.2% utilization (net savings $1,309.00)
[purple bar]
Coverage
67.6%  (was 49.0%, ↑ +18.6pp)
Covered: $2,851.32  (was $1,420.30, ↑ $1,431.02)
On-demand: $1,368.27  (was $1,478.91, ↓ $110.64)

Utilization
98.2%  (was 95.7%, ↑ +2.5pp)
Commitment used: $1,515.19  (was $1,470.40)
Unused: $27.15  (was $66.20, ↓ $39.05)
Net savings: $1,309.00  (was $1,200.50, ↑ $108.50)

Per plan
`abcdef12` Compute (No Upfront)
        99.1% util (was 99.8%, ↓ -0.7pp)  ·  used $1,390.69  ·  net savings $1,256.65
`db000001` Database (No Upfront) 🆕
        89.4% util (was 0.0%, ↑ +89.4pp)  ·  used $124.49  ·  net savings $52.33
```

If the account has no Reserved Instances, that thread is skipped. Same for Savings Plans. The footer note tells you which commitments were detected.

## Setup

The Lambda needs two environment variables. Everything else has a default.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `SLACK_BOT_TOKEN` | yes | — | Slack bot user OAuth token, starts with `xoxb-`. |
| `SLACK_CHANNEL_ID` | yes | — | Channel ID like `C0XXXXXX`, not the channel name. |
| `MIN_COST_DELTA_USD` | no | `5.0` | Skip services whose absolute change is below this. |
| `MIN_PCT_CHANGE` | no | `10.0` | Skip services whose percent change is below this. |
| `MIN_RI_PCT_POINTS` | no | `5.0` | Show instance families whose coverage changed by at least this many percentage points. |
| `MAX_RI_LEAK_UTIL_PCT` | no | `80.0` | Subscriptions running below this utilization show up in the "underutilized" list. |
| `MIN_RI_LEAK_HOURS` | no | `100.0` | Ignore subscriptions with fewer unused hours than this. Avoids reporting trivial waste. |
| `MAX_RI_LEAK_SUBSCRIPTIONS` | no | `5` | Cap on how many leaking subscriptions to list. |
| `WINDOW_DAYS` | no | `30` | Length of each comparison window in days. |
| `DRY_RUN` | no | `false` | When `true`, run end-to-end without posting to Slack (logs only). |
| `LOG_LEVEL` | no | `INFO` | Standard Python log levels. |

If `SLACK_BOT_TOKEN` or `SLACK_CHANNEL_ID` is missing the Lambda fails fast at import time with a `RuntimeError` and a clear message.

Your Slack app needs `chat:write` (and `chat:write.public` if you don't want to invite the bot to every channel manually). Invite it to the target channel.

The thresholds matter more than they look. On a hobby account with a $40 monthly bill, the defaults will report basically every service. On a $2M production org, you probably want `MIN_COST_DELTA_USD=500` and `MIN_PCT_CHANGE=25`. Tune to taste.

## Deploy

The Slack SDK is not in the AWS Lambda Python runtime, so package it with the function:

```bash
pip install -r requirements.txt -t build/
cp *.py build/
(cd build && zip -r ../function.zip .)

aws lambda update-function-code \
  --function-name billing-bot \
  --zip-file fileb://function.zip
```

First-time deploy needs the function, role, and environment too:

```bash
aws iam create-role \
  --role-name billing-bot-execution-role \
  --assume-role-policy-document file://trust-policy.json

aws iam put-role-policy \
  --role-name billing-bot-execution-role \
  --policy-name billing-bot-inline \
  --policy-document file://iam-policy.json

aws lambda create-function \
  --function-name billing-bot \
  --runtime python3.12 \
  --handler lambda_function.lambda_handler \
  --role "arn:aws:iam::<ACCOUNT_ID>:role/billing-bot-execution-role" \
  --zip-file fileb://function.zip \
  --timeout 60 \
  --memory-size 256 \
  --environment "Variables={SLACK_BOT_TOKEN=xoxb-...,SLACK_CHANNEL_ID=C0...}"
```

`trust-policy.json` is the standard Lambda trust document. The execution permissions are in [`iam-policy.json`](iam-policy.json): Cost Explorer read for cost/RIs/Savings Plans, plus `savingsplans:DescribeSavingsPlans` so the per-plan section can label each plan by type and payment option, plus CloudWatch Logs.

Schedule it monthly with EventBridge Scheduler, for example on the first of every month at 09:00 Istanbul time:

```bash
aws scheduler create-schedule \
  --name billing-bot-monthly \
  --schedule-expression "cron(0 9 1 * ? *)" \
  --schedule-expression-timezone "Europe/Istanbul" \
  --flexible-time-window "Mode=OFF" \
  --target '{"Arn":"arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:billing-bot","RoleArn":"arn:aws:iam::<ACCOUNT_ID>:role/billing-bot-scheduler-role"}'
```

Cost Explorer is a `us-east-1` service. The Lambda itself works anywhere, but pinning it to `us-east-1` keeps the API calls in-region.

Recommended sizing:

- Runtime: `python3.12`
- Memory: `256 MB` (real-world peak around 95 MB)
- Timeout: `60 s` (real-world runs in 2-3 s on a small account, 10-15 s on accounts with both RIs and SPs because there are more Cost Explorer calls)

## Tests

The non-AWS code paths are covered by unit tests under `tests/`. They use `pytest` and don't touch boto3:

```bash
pip install -r requirements-dev.txt
pytest
```

## Local invocation

For a dry run without posting to Slack, set `DRY_RUN=true` and run the file directly. You still need real AWS credentials with the same Cost Explorer + Savings Plans read permissions:

```bash
export SLACK_BOT_TOKEN=xoxb-dummy
export SLACK_CHANNEL_ID=C00000000
export DRY_RUN=true
python lambda_function.py
```

The logs will show the windows, the per-service counts, the totals, and what the bot would have posted.

## How the code is laid out

`lambda_function.py` is just orchestration. The real work is split across small modules so each piece can be tested in isolation:

- `config.py` reads and validates the environment.
- `cost_explorer.py` is the only file that talks to boto3.
- `analysis.py` does pure deltas, filtering, ranking, and the per-direction split. No I/O.
- `slack_publisher.py` builds Block Kit payloads and posts with retry on transient errors.

## Caveats

Cost Explorer is delayed. New charges usually show up within 24 hours, sometimes longer. Running on the first of the month means the last day or two of the prior month may not be fully reflected.

Cost Explorer also costs money. Each `GetCostAndUsage`-style call is $0.01. A run on an account with both RIs and Savings Plans makes roughly 12 calls (cost current + previous, RI utilization total + previous, RI utilization by subscription, RI coverage by family for both windows, SP coverage for both, SP utilization total for both, SP utilization details for both). That's about $0.12 per run, or $1.50 a year on a monthly schedule. Trivial unless you're scheduling it hourly.

Each thread reply is one Slack message. If your account has so many qualifying services or so many leaking subscriptions that the body exceeds the Slack message limit (~4000 characters), the tail of the list will be truncated. The fix is to raise the relevant threshold (`MIN_COST_DELTA_USD`, `MIN_RI_PCT_POINTS`, `MAX_RI_LEAK_SUBSCRIPTIONS`).

There is no persistence. Every run recomputes from scratch. If a Slack 5xx ate the post and the retry budget was exhausted, the report is gone for that month. Next month's run still works.

The `Database` Savings Plan type covers RDS. It is grouped with other Savings Plans in the SP thread, not with the EC2-style RIs in the RI thread, because the underlying API treats it as a Savings Plan.

## Files

| File | What it is |
|---|---|
| `lambda_function.py` | Lambda entry point and orchestration |
| `config.py` | Env-var loading with defaults and validation |
| `cost_explorer.py` | Cost Explorer + Savings Plans API wrappers |
| `analysis.py` | Pure delta computation, direction split, and RI/SP delta types |
| `slack_publisher.py` | Block Kit formatter and posting client with retry |
| `iam-policy.json` | Execution role permissions |
| `requirements.txt` | Runtime deps (Lambda needs `slack_sdk`; boto3 is provided) |
| `requirements-dev.txt` | Adds pytest for local testing |
| `tests/` | Unit tests |

## License

MIT. See [LICENSE](LICENSE).
