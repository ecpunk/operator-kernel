# Operator Kernel End User Quickstart

Status: Quickstart for current production-style workflow
Last updated: 2026-03-07

## Five Minute Path

1) Move to project directory:
cd /opt/stack/services/projects/operator-kernel

2) Run inspect:
PYTHONPATH=src python3 src/operator_runtime/main.py --mode inspect --channel text --task "Plex playback failing on LAN"

Optional calibrated cost mode when provider usage is known:
PYTHONPATH=src python3 src/operator_runtime/main.py --mode inspect --channel text --task "Plex playback failing on LAN" --provider openai --model gpt-5-mini --input-tokens 1200 --output-tokens 300

Optional usage file mode:
PYTHONPATH=src python3 src/operator_runtime/main.py --mode inspect --channel text --task "Plex playback failing on LAN" --usage-json config/inspect_usage.example.json

`inspect_usage.example.json` is safe under current caps; `inspect_usage.blocked_example.json` intentionally exceeds caps for guardrail testing.

Inspect output now includes:
- ranked hypotheses
- source citations and evidence snippets
- token_estimate / estimated_cost_usd
- inspect artifact path under `artifacts/inspect/`

3) Run propose:
PYTHONPATH=src python3 src/operator_runtime/main.py --mode propose --channel voice --task "Fix Plex after recent restart"

4) Copy plan id and approval token from propose output.

5) Run execute dry-run first:
PYTHONPATH=src python3 src/operator_runtime/main.py --mode execute --task "Fix Plex after recent restart" --plan-id <plan_id> --approval-token <token>

6) If approved and safe, run execute with apply:
PYTHONPATH=src python3 src/operator_runtime/main.py --mode execute --task "Fix Plex after recent restart" --plan-id <plan_id> --approval-token <token> --apply

## Quick Health Checks

Stage 3 scheduler health:
PYTHONPATH=src python3 src/operator_ops/report_stage3_scheduler_health.py --export-dashboard --max-staleness-minutes 180

Stage 3 readiness:
PYTHONPATH=src python3 src/operator_ops/report_stage3_readiness.py --export-dashboard

Recent run quality:
PYTHONPATH=src python3 src/operator_ops/report_runs.py --days 1 --top 5 --recent 5

## What Good Looks Like

- Scheduler health status is healthy
- Readiness status is ready
- Batch 2 test suite reports full pass
- Incident registry count stays at zero or is actively resolved

## Fast Troubleshooting

If execute is blocked, verify:
- plan id is current
- approval token is correct
- action is allowlisted
- risk policy does not block this request

If scheduler health is unhealthy, verify:
- timer is enabled and active
- readiness artifact is fresh
- latest stage3_operations is pass

## Safety Reminder

Use --apply only after explicit operator approval. The normal and safe first pass is inspect -> propose -> execute dry-run.

Stage 4 note: open-ended inspect now enforces policy caps (traceability, token estimate, estimated cost) and will fail closed with `blocked_stage4_policy` when limits are exceeded.
