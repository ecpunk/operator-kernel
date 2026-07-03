# Operator Kernel End User Guide (v0.9 Draft)

Status: Draft for active Stage 3 operations
Last updated: 2026-03-07

## What This Product Is

Operator Kernel is a deterministic operations copilot for incident handling. It does not run hidden autonomous mutations. It uses strict contracts and explicit approvals.

You can use it in three modes:
- inspect: read-only diagnosis
- propose: generates a deterministic plan only
- execute: applies an approved plan when approval token is provided

## Who This Guide Is For

This guide is for operators who need fast incident diagnosis and safe recovery actions across the currently onboarded services.

## Current Service Coverage

The currently active lane set covers:
- Plex
- Sonarr
- Radarr
- Bazarr
- Home Assistant
- SABnzbd

Coverage expands over time through lane onboarding and policy-gated tests.

## Safety Model In Plain Language

1) Read-only by default.
Inspect and propose do not mutate service state.

2) Mutations are token-gated.
Execute mode requires plan id plus approval token.

3) Allowed actions are bounded.
Execution is constrained by lane specs and policy allowlists.

4) Every execute run emits evidence.
Proof artifacts are generated with precheck, command transcript, verification, and rollback path details.

5) Unsafe requests fail closed.
Critical destructive intents are blocked by policy.

## Typical Operator Flow

Step 1: Inspect
Run inspect to triage an incident and gather context-backed hypotheses.
Inspect now returns ranked hypotheses with required source citations and writes an inspect artifact to `artifacts/inspect/`.

Step 2: Propose
Run propose to create a deterministic plan and approval token.

Step 3: Review
Review the generated plan steps, risk classification, and verify probes.

Step 4: Execute
Run execute with plan id and approval token. Add --apply only when approved to mutate.

Step 5: Verify
Confirm post-verify outputs and review generated proof pack artifacts.

## Common Commands

Inspect:
PYTHONPATH=src python3 src/operator_runtime/main.py --mode inspect --channel text --task "Plex playback failing on LAN"

Propose:
PYTHONPATH=src python3 src/operator_runtime/main.py --mode propose --channel voice --task "Fix Plex after recent restart"

Execute dry-run (no mutation):
PYTHONPATH=src python3 src/operator_runtime/main.py --mode execute --task "Fix Plex after recent restart" --plan-id <plan_id> --approval-token <token>

Execute apply (mutating, approved):
PYTHONPATH=src python3 src/operator_runtime/main.py --mode execute --task "Fix Plex after recent restart" --plan-id <plan_id> --approval-token <token> --apply

## How To Read Outcomes

For each run, look for:
- mode and status
- action id and lane
- verification status
- blocked reason (if blocked)
- artifact path outputs

Inspect-specific signals:
- branch: `lane_matched` or `open_ended`
- token_estimate and estimated_cost_usd (Stage 4 baseline instrumentation)
- cited evidence lines in trusted context sources
- cost_source: `heuristic` (default) or `provider_usage` (when provider/model/input/output tokens are supplied)

Provider-calibrated inspect example:
PYTHONPATH=src python3 src/operator_runtime/main.py --mode inspect --channel text --task "Summarize current risk posture" --provider openai --model gpt-5-mini --input-tokens 1200 --output-tokens 300

Automatic usage-file example:
PYTHONPATH=src python3 src/operator_runtime/main.py --mode inspect --channel text --task "Summarize current risk posture" --usage-json config/inspect_usage.example.json

`config/inspect_usage.example.json` is a cap-safe sample.
Use `config/inspect_usage.blocked_example.json` when you intentionally want to validate `blocked_stage4_policy`.

Environment variable option:
export INSPECT_USAGE_JSON=/opt/stack/services/projects/operator-kernel/config/inspect_usage.example.json
PYTHONPATH=src python3 src/operator_runtime/main.py --mode inspect --channel text --task "Summarize current risk posture"

Stage 4 guardrail behavior:
- Open-ended inspect enforces quality/cost caps from `config/guardrails.json` (`stage4_inspect`).
- If a request exceeds policy limits, inspect is fail-closed and run status is `blocked_stage4_policy`.

Expected healthy behavior:
- inspect/propose complete without mutation
- execute without --apply remains dry-run
- execute with --apply only proceeds when policy and approval checks pass

## Artifacts You Will See

Common artifact areas:
- artifacts/plans/
- artifacts/proofs/
- artifacts/tests/
- artifacts/stage3/

Most tools also write *_latest.json snapshots for fast status checks.

## Stage 3 Operational Status Checks

These commands are the fastest user-facing health checks:

Scheduler health:
PYTHONPATH=src python3 src/operator_ops/report_stage3_scheduler_health.py --export-dashboard --max-staleness-minutes 180

Readiness rollup:
PYTHONPATH=src python3 src/operator_ops/report_stage3_readiness.py --export-dashboard

Run history summary:
PYTHONPATH=src python3 src/operator_ops/report_runs.py --days 1 --top 5 --recent 5

## When Something Is Blocked

If execute is blocked:
- confirm plan id and approval token match
- confirm risk tier is not blocked by policy
- confirm action id is allowlisted for apply mode
- confirm required verify probes are configured for the lane

If readiness is not ready:
- inspect latest stage3_operations and batch2 artifacts
- inspect scheduler health for staleness alerts
- rerun stage3 orchestrator once root cause is addressed

## Voice And Mobile Notes

Voice/mobile adapter contract scaffolding is in place. Full end-user parity workflows are still being finalized as Stage 3 scope continues. Current command contract remains the source of truth.

## Known Limits In This Draft

This guide documents current safe operation and user workflows. Long-duration reliability claims should be treated as provisional until full soak windows are complete.

## Support Pointers

Concept and constraints:
10-CONCEPT/OPERATOR_KERNEL_CONCEPT.md

Delivery and implementation status:
ROADMAP.md

Command reference and operator usage:
README.md
