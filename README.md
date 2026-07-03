# Operator Kernel (New Product Seed)

An operations framework built around one rule: nothing happens on your infrastructure without your explicit approval.

You bring a question or a situation — an outage, a planned change, a gap you want to understand — and it loads your real system context, evaluates it, and returns a structured answer before anything runs. When action is needed and you approve it, it executes and records every decision and command. When no action is needed, the evaluation itself is the output.

It was built as a direct replacement for a broad autonomous agent platform that worked in narrow ways but created unpredictable behavior, hidden costs, and eroded trust. Every design decision here is a specific answer to something that went wrong there: no hidden autonomy, explicit approval gates, bounded action sets, full proof output on every execution.

What it manages is defined by configuration. It is not tied to any specific service, platform, or environment.

Table of contents:
- [What You Should Read First](#what-you-should-read-first)
- [Documentation Gate](#documentation-gate)
- [Source Layout (Post-Refactor)](#source-layout-post-refactor)
- [Where Source of Truth Is Loaded](#where-source-of-truth-is-loaded)
- [MVP Flow](#mvp-flow)
- [Quick Test](#quick-test)
- [Artifact Retention And GC](#artifact-retention-and-gc)
- [Stage 3 Operations Orchestrator](#stage-3-operations-orchestrator)
- [Stage 3 Readiness Rollup](#stage-3-readiness-rollup)
- [Stage 3 Scheduler Health](#stage-3-scheduler-health)
- [Service Scorecards](#service-scorecards)
- [Runbook Generator](#runbook-generator)
- [Broad Spectrum Test Batch 2](#broad-spectrum-test-batch-2)
- [Lane Authoring Quality Gate](#lane-authoring-quality-gate)
- [Lane Onboarding Runner](#lane-onboarding-runner)

## What You Should Read First

Concept doc:
- `10-CONCEPT/OPERATOR_KERNEL_CONCEPT.md`

Roadmap:
- `ROADMAP.md`

Purpose: build a deterministic operator runtime that can run short prompts safely by loading trusted context before planning or execution.

End user documentation:
- `docs/END_USER_GUIDE.md`
- `docs/END_USER_QUICKSTART.md`

GitHub publishing plan:
- `docs/GITHUB_REPO_PLAN.md`

Core idea:
- Voice or keyboard should produce the same result.
- The runtime always runs a preflight context load step first.
- Mutations require explicit approval gates and proof output.
- Action selection is keyword-mapped via `config/lane_catalog.json` and executed by lane specs in `config/lanes/`.
- Propose mode performs a safe live observation pass and can attach recommendation hints to the plan artifact.
- Recommendation-driven step reordering is gated by confidence threshold policy in `config/guardrails.json`.

## Documentation Gate

Documentation updates are required for any change that affects architecture, runtime behavior, entrypoints, packaging, policy, or operator workflow.

Minimum required updates per qualifying change:
- `ROADMAP.md`: update active/completed task status and record any stage/gate-impacting decisions.
- `README.md`: update source layout, command paths, and behavior notes if user/operator workflow changes.
- Relevant guide(s) in `docs/`: update end-user and operator instructions when command usage or expected outputs change.
- Commit message: include a short rationale for why the change was made, not only what changed.

PR/merge should be blocked until this checklist is satisfied.

## Source Layout (Post-Refactor)

The repository was physically separated to remove remnants of the old combined layout and enforce kernel boundaries by directory:

- `src/operator_kernel/`: kernel primitives only (contract, schema validation, lane resolution, risk controls).
- `src/operator_runtime/`: runtime behavior and execution flow.
- `src/operator_ops/`: operational orchestration, reporting, and incident tooling.
- `src/operator_tests/`: test suites and validation harnesses.

Why this was done:
- prevent kernel/application boundary drift.
- make packaging precise (`operator-kernel` publishes only `operator_kernel`).
- make ownership explicit so runtime/ops changes do not silently expand kernel scope.

Entrypoint note:
- root-level `src/*.py` compatibility shims have been removed.
- commands should target package-native paths such as `src/operator_runtime/main.py`, `src/operator_ops/*.py`, and `src/operator_tests/*.py`.
- when running these scripts directly, use `PYTHONPATH=src` so package imports resolve consistently.

## Where Source of Truth Is Loaded

Source of truth is loaded in `Preflight -> Context Hydration` before any task planning.
Implementation entrypoint:
- `src/operator_runtime/main.py`
- delegates to `src/operator_runtime/main.py`
- calls `build_preflight_context()` from `src/operator_runtime/context_loader.py`
- uses ordered docs from `config/context_sources.json`

## MVP Flow

1. Preflight context hydration (read trusted docs in strict order).
2. Task classification (read-only vs mutating).
3. Plan generation (bounded, deterministic).
4. Optional execution (only when approved).
5. Evidence pack output (facts, commands, verification).

## Quick Test

Run:

`PYTHONPATH=src python3 src/operator_runtime/main.py --mode inspect --channel text --task "Plex playback failing on LAN"`

Optional provider-calibrated inspect cost (when real usage tokens are known):

`PYTHONPATH=src python3 src/operator_runtime/main.py --mode inspect --channel text --task "Plex playback failing on LAN" --provider openai --model gpt-5-mini --input-tokens 1200 --output-tokens 300`

Automatic usage ingestion from JSON file:

`PYTHONPATH=src python3 src/operator_runtime/main.py --mode inspect --channel text --task "Plex playback failing on LAN" --usage-json config/inspect_usage.example.json`

`config/inspect_usage.example.json` is a cap-safe sample profile.
For policy-block testing, use `config/inspect_usage.blocked_example.json`.

Or set an environment variable once and reuse it:

`export INSPECT_USAGE_JSON=/opt/stack/services/projects/operator-kernel/config/inspect_usage.example.json`

`PYTHONPATH=src python3 src/operator_runtime/main.py --mode inspect --channel text --task "Plex playback failing on LAN"`

Then propose:

`PYTHONPATH=src python3 src/operator_runtime/main.py --mode propose --channel voice --task "Fix Plex after recent restart"`

For synthetic test harness runs, tag entries to keep scorecard operational metrics clean:

`PYTHONPATH=src python3 src/operator_runtime/main.py --run-type test --mode propose --channel voice --task "Fix Plex after recent restart"`

Then execute with plan id + approval token from the proposal artifact:

`PYTHONPATH=src python3 src/operator_runtime/main.py --mode execute --task "Fix Plex after recent restart" --plan-id <plan_id> --approval-token <token>`

To run the allowlisted mutating step for Plex recovery, add `--apply`:

`PYTHONPATH=src python3 src/operator_runtime/main.py --mode execute --task "Fix Plex after recent restart" --plan-id <plan_id> --approval-token <token> --apply`

Expected:
- prints loaded context files and status
- enforces inspect/propose/execute mode contract
- inspect writes ranked hypotheses, cited evidence lines, and an inspect artifact under `artifacts/inspect/`
- writes plan and proof artifacts under `artifacts/` for propose/execute flows
- execute defaults to dry-run unless `--apply` is provided

## Artifact Retention And GC

Artifact lifecycle is policy-driven in `config/guardrails.json` under `artifacts`:
- `max_plan_files`
- `max_plan_age_days`
- `max_proof_files`
- `max_proof_age_days`
- `max_run_index_entries`
- `soft_deprecate_enabled`
- `archive_retention_days`

Behavior:
- After each propose/execute run, the runtime trims `artifacts/plans` and `artifacts/proofs` by age first, then by count.
- When `soft_deprecate_enabled=true`, matching files are moved to `artifacts/archive/plans` or `artifacts/archive/proofs` and tracked in `artifacts/archive/archive_index.json`.
- Archived files are purged only after `archive_retention_days`.
- `artifacts/index/run_index.json` is capped to `max_run_index_entries` during append.
- Inspect runs now include Stage 4 instrumentation in the run index: `inspect_branch`, `inspect_token_estimate`, `inspect_estimated_cost_usd`, and `inspect_cited_sources`.
- Inspect runs also include calibration metadata: `inspect_cost_source`, `inspect_provider`, `inspect_model`, `inspect_input_tokens`, `inspect_output_tokens`.
- Provider pricing tables are configured in `config/pricing_models.json`.
- Example usage-hint payload: `config/inspect_usage.example.json`.
- High-usage blocked sample payload: `config/inspect_usage.blocked_example.json`.
- Runtime output includes an `artifact_gc` summary so retention actions are auditable.

## Planning Source

Execution stage, current status, and next implementation tasks now live in `ROADMAP.md`.
Use this README for runtime usage and command references.
Testing pause timing and broad-spectrum test matrix live in `TESTING_PHASE_PLAN.md`.
Compounding development methodology lives in `COMPOUNDING_ARCHITECTURE_METHOD.md`.

Lane keyword resolution uses precedence policy from `config/lane_catalog.json`:
- optional mapping `priority`
- whole-word preference
- earliest-keyword mention preference (for mixed-service phrases)
- longest-keyword tie-break
- deterministic fallback to mapping order

## Archive Reporting Task

Run archive lifecycle summary:

`PYTHONPATH=src python3 src/operator_ops/report_archive_activity.py --days 30 --recent 10 --archive-retention-days 120`

Use `--days 0` to include full history.

## Consolidated Run Report

Run combined run-index and archive summary:

`PYTHONPATH=src python3 src/operator_ops/report_runs.py --days 7 --top 5 --recent 10`

## Stage Scorecard

Generate rolling stage confidence scorecard:

`PYTHONPATH=src python3 src/operator_ops/report_scorecard.py --days 7`

Generate and export scorecard to dashboard status:

`PYTHONPATH=src python3 src/operator_ops/report_scorecard.py --days 7 --export-dashboard`

Enforce fail-closed stage gate policy (non-zero exit on block):

`PYTHONPATH=src python3 src/operator_ops/report_scorecard.py --enforce-gate`

Scorecard policy and thresholds are configured in:

`config/guardrails.json` under `scorecard`

Scorecard artifacts are written to:

`artifacts/scorecards/stage_scorecard_<timestamp>.json`
`artifacts/scorecards/stage_scorecard_latest.json`

## Trend Report

Generate rolling operational trend metrics (MTTT/MTTVF proxies, recurrence, approval and risk blocks):

`PYTHONPATH=src python3 src/operator_ops/report_trends.py --days 7`

Include synthetic test runs if needed:

`PYTHONPATH=src python3 src/operator_ops/report_trends.py --days 7 --include-tests`

Trend artifacts are written to:

`artifacts/trends/trend_report_<timestamp>.json`
`artifacts/trends/trend_report_latest.json`

## Deferred Learning Suggestions

Generate review-only deferred learning suggestions from recurring tasks, failure recurrence, and lane probe coverage:

`PYTHONPATH=src python3 src/operator_ops/report_learning_suggestions.py --days 7`

Include synthetic test runs when auditing onboarding/test behavior:

`PYTHONPATH=src python3 src/operator_ops/report_learning_suggestions.py --days 7 --include-tests`

Learning policy controls are configured in:

`config/guardrails.json` under `learning`

Artifacts are written to:

`artifacts/learning/learning_suggestions_<timestamp>.json`
`artifacts/learning/learning_suggestions_latest.json`

Generate review-ready concept/runbook delta drafts from latest learning suggestions:

`PYTHONPATH=src python3 src/operator_ops/report_learning_deltas.py`

Limit to top recurring tasks:

`PYTHONPATH=src python3 src/operator_ops/report_learning_deltas.py --max-tasks 3`

Delta artifacts are written to:

`artifacts/learning/learning_deltas_<timestamp>.json`
`artifacts/learning/learning_deltas_latest.json`
`artifacts/learning/learning_deltas_review_<timestamp>.md`

Apply selected learning delta targets in controlled mode (dry-run by default):

`PYTHONPATH=src python3 src/operator_ops/apply_learning_deltas.py --select-index 1 --select-index 2`

List available target indexes before selecting:

`PYTHONPATH=src python3 src/operator_ops/apply_learning_deltas.py --list-targets`

Use policy-driven selection profiles:

`PYTHONPATH=src python3 src/operator_ops/apply_learning_deltas.py --profile concept_only`
`PYTHONPATH=src python3 src/operator_ops/apply_learning_deltas.py --profile runbooks_only`
`PYTHONPATH=src python3 src/operator_ops/apply_learning_deltas.py --profile top3`

Apply selected targets with explicit approval token:

`PYTHONPATH=src python3 src/operator_ops/apply_learning_deltas.py --apply --approval-token <token> --select-index 1`

You can also select by exact target path:

`PYTHONPATH=src python3 src/operator_ops/apply_learning_deltas.py --select-target "artifacts/runbooks/runbook_plex_safe_recovery_v1.md"`

Learning apply policy controls are configured in:

`config/guardrails.json` under `learning`

Apply run artifacts are written to:

`artifacts/learning/learning_delta_apply_<timestamp>.json`
`artifacts/learning/learning_delta_apply_latest.json`

## Stage 3 Operations Orchestrator

Run the Stage 3 operations pipeline in one command (learning reports, deterministic regression, bootstrap strict, service gate, soak exit gate, readiness rollup):

`PYTHONPATH=src python3 src/operator_ops/run_stage3_operations.py --days 7`

Run with a dry-run delta profile selection:

`PYTHONPATH=src python3 src/operator_ops/run_stage3_operations.py --days 7 --delta-profile top3`

Run with approved delta apply mode:

`PYTHONPATH=src python3 src/operator_ops/run_stage3_operations.py --days 7 --delta-profile concept_only --apply-deltas --approval-token <token>`

Notification routing (Discord-ready and generic webhook) is configured in:

`config/notify.json` (see `config/notify.example.json` for a documented starting point)

Webhook URLs are never stored in config — set `url_env` on a route to the name of an
environment variable (e.g. `DISCORD_WEBHOOK_URL`) and export it before the runtime starts.

The orchestrator emits notifications for start, command milestones, failure, and completion.
`--alert-webhook-url` remains for compatibility only and is deprecated.

Orchestrator artifacts are written to:

`artifacts/stage3/stage3_operations_<timestamp>.json`
`artifacts/stage3/stage3_operations_latest.json`

When orchestration fails or readiness is not-ready, alert artifacts are written to:

`artifacts/stage3/stage3_alert_<timestamp>.json`
`artifacts/stage3/stage3_alert_latest.json`

Alert transport behavior (retry/backoff/dedupe and unattended apply policy) is configured in:

`config/guardrails.json` under `stage3_operations`

## Stage 3 Readiness Rollup

Generate a one-glance Stage 3 readiness rollup from latest gates and regression suites:

`PYTHONPATH=src python3 src/operator_ops/report_stage3_readiness.py --export-dashboard`

Emit incident ticket artifacts when readiness is not green:

`PYTHONPATH=src python3 src/operator_ops/report_stage3_readiness.py --export-dashboard --emit-ticket`

Fail closed when readiness is not green:

`PYTHONPATH=src python3 src/operator_ops/report_stage3_readiness.py --export-dashboard --enforce-ready`

## Stage 4 Inspect Metrics

Generate Stage 4 inspect sample quality/cost metrics and threshold recommendations:

`PYTHONPATH=src python3 src/operator_ops/report_stage4_inspect_metrics.py --days 7 --sample-min 50`

Fail closed until sample target is met:

`PYTHONPATH=src python3 src/operator_ops/report_stage4_inspect_metrics.py --days 7 --sample-min 50 --enforce-sample-min`

Stage 4 metrics artifacts are written to:

`artifacts/stage4/stage4_inspect_metrics_<timestamp>.json`
`artifacts/stage4/stage4_inspect_metrics_latest.json`

Finalized Stage 4 inspect guardrail thresholds (configured in `config/guardrails.json` under `stage4_inspect`):
- `enforce_open_ended_thresholds=true`
- `min_traceability_ratio=0.98`
- `max_open_ended_token_estimate=138`
- `max_open_ended_estimated_cost_usd=0.000276`

When an open-ended inspect exceeds these thresholds, runtime records status `blocked_stage4_policy` in the run index.

Readiness artifacts are written to:

`artifacts/stage3/stage3_readiness_<timestamp>.json`
`artifacts/stage3/stage3_readiness_latest.json`
`artifacts/stage3/stage3_incident_<timestamp>.json`
`artifacts/stage3/stage3_incident_<timestamp>.md`
`artifacts/stage3/stage3_incident_latest.json`
`artifacts/stage3/stage3_incident_latest.md`

Manage incident lifecycle state:

`PYTHONPATH=src python3 src/operator_ops/manage_stage3_incident.py --action list`
`PYTHONPATH=src python3 src/operator_ops/manage_stage3_incident.py --action open_from_latest`
`PYTHONPATH=src python3 src/operator_ops/manage_stage3_incident.py --action ack --incident-id <id> --note "triage started"`
`PYTHONPATH=src python3 src/operator_ops/manage_stage3_incident.py --action resolve --incident-id <id> --note "fixed"`

Incident registry path:

`artifacts/stage3/stage3_incident_registry.json`

Dashboard export key:

`operator_kernel_stage3` in `apps/dashboard/status.json`

## Stage 3 Scheduler Health

Generate timer/scheduler health status and artifact freshness checks:

`PYTHONPATH=src python3 src/operator_ops/report_stage3_scheduler_health.py --export-dashboard`

Fail closed when scheduler health is degraded:

`PYTHONPATH=src python3 src/operator_ops/report_stage3_scheduler_health.py --export-dashboard --enforce-healthy --max-staleness-minutes 90`

Scheduler health artifacts are written to:

`artifacts/stage3/stage3_scheduler_health_<timestamp>.json`
`artifacts/stage3/stage3_scheduler_health_latest.json`

Dashboard export key:

`operator_kernel_stage3_scheduler` in `apps/dashboard/status.json`

## Stage 3 Scheduling

Systemd scheduling assets are available at:

`systemd/operator-kernel-stage3-ops.service`
`systemd/operator-kernel-stage3-ops.timer`
`systemd/operator-kernel-stage3-ops.env`
`systemd/operator-kernel-stage3-ops.env.example`

Install example:

`sudo cp systemd/operator-kernel-stage3-ops.service /etc/systemd/system/`
`sudo cp systemd/operator-kernel-stage3-ops.timer /etc/systemd/system/`
`sudo systemctl daemon-reload`
`sudo systemctl enable --now operator-kernel-stage3-ops.timer`

Optional: create local runtime overrides from the example file:

`cp systemd/operator-kernel-stage3-ops.env.example systemd/operator-kernel-stage3-ops.env`

Customize unattended defaults in `systemd/operator-kernel-stage3-ops.env` via:

- `STAGE3_OPS_ARGS` (for example: `--days 7 --delta-profile none`)
- `STAGE3_ALERT_WEBHOOK_URL` (deprecated compatibility only; prefer `config/notify.json`)

## Adapter Contract Scaffolding

Validate voice/mobile adapter payload contract and parity semantics:

`PYTHONPATH=src python3 src/operator_tests/adapter_contract_tests.py --strict`

Normalize a single adapter payload through the contract:

`PYTHONPATH=src python3 src/operator_kernel/adapter_contract.py --payload '{"adapter":"voice","channel":"voice","utterance":"Plex playback failing on LAN","timestamp":"2026-03-07T00:00:00+00:00"}'`

Adapter payload schema:

`config/schemas/adapter_payload.schema.json`

Adapter stubs:

`src/adapters/voice_adapter_stub.py`
`src/adapters/mobile_adapter_stub.py`

## Service Scorecards

Generate per-action service scorecards over a rolling window:

`PYTHONPATH=src python3 src/operator_ops/report_service_scorecards.py --days 7`

Generate and export service scorecards to dashboard status:

`PYTHONPATH=src python3 src/operator_ops/report_service_scorecards.py --days 7 --export-dashboard`

Enforce fail-closed service scorecard gate policy:

`PYTHONPATH=src python3 src/operator_ops/report_service_scorecards.py --enforce-gate`

Lane lifecycle state (`candidate`/`active`) is defined in:

`config/lane_lifecycle.json`

Candidate lanes can be tracked without blocking the global gate when onboarding exemption policy is enabled.

Artifacts:

`artifacts/service_scorecards/service_scorecards_<timestamp>.json`
`artifacts/service_scorecards/service_scorecards_latest.json`

## Migration Policy

Runtime config version policy is fail-closed via:

`config/migration_policy.json`

The runtime validates this policy during startup through `src/operator_kernel/schema_validator.py`.

## Stage 2 Soak Snapshot

Generate Stage 2 close evidence snapshot (stage gate, service gate, migration validation, trend):

`PYTHONPATH=src python3 src/operator_ops/run_stage2_soak_snapshot.py --days 7`

Enforce Stage 2 exit requirements (non-zero exit on block):

`PYTHONPATH=src python3 src/operator_ops/run_stage2_soak_snapshot.py --days 7 --enforce-exit`

Soak artifacts:

`artifacts/soak/stage2_soak_snapshot_<timestamp>.json`
`artifacts/soak/stage2_soak_snapshot_latest.json`

Soak snapshots also run deferred learning suggestions and link latest learning artifacts.
Soak snapshots now also generate review delta drafts from the latest learning suggestions.

## Run Index Repair

Repair duplicate or missing run ids in `artifacts/index/run_index.json`:

`PYTHONPATH=src python3 src/operator_ops/repair_run_index.py --max-entries 1200`

## Runbook Generator

Generate stable operator runbooks from lane specs plus historical proof patterns:

`PYTHONPATH=src python3 src/operator_ops/generate_runbooks.py`

Generated outputs:

`artifacts/runbooks/RUNBOOKS.md`
`artifacts/runbooks/runbook_<action_id>.md`

## Go No-Go Test Suite

Run deterministic acceptance suite:

`PYTHONPATH=src python3 src/operator_tests/go_no_go_tests.py`

Reports are written to:

`artifacts/tests/go_no_go_<timestamp>.json`

## Broad Spectrum Test Batch 1

Run broad-spectrum batch 1 suite:

`PYTHONPATH=src python3 src/operator_tests/broad_spectrum_tests.py`

Reports are written to:

`artifacts/tests/broad_spectrum_batch1_<timestamp>.json`

## Broad Spectrum Test Batch 2

Run OpenClaw-learned and speculative scenario suite:

`PYTHONPATH=src python3 src/operator_tests/broad_spectrum_tests_batch2.py`

Reports are written to:

`artifacts/tests/broad_spectrum_batch2_<timestamp>.json`

## Broad Spectrum Test Batch 3

Run failure-injection and degraded-dependency suite:

`PYTHONPATH=src python3 src/operator_tests/broad_spectrum_tests_batch3.py`

Reports are written to:

`artifacts/tests/broad_spectrum_batch3_<timestamp>.json`

## Broad Spectrum Test Batch 4

Run cost-policy and context-boundary stress suite:

`PYTHONPATH=src python3 src/operator_tests/broad_spectrum_tests_batch4.py`

Reports are written to:

`artifacts/tests/broad_spectrum_batch4_<timestamp>.json`

## Broad Spectrum Test Batch 5

Run recurrence doc-delta simulation and lane onboarding stress suite:

`PYTHONPATH=src python3 src/operator_tests/broad_spectrum_tests_batch5.py`

Reports are written to:

`artifacts/tests/broad_spectrum_batch5_<timestamp>.json`

## Stage 3 Bootstrap Tests

Run Stage 3 lane-expansion bootstrap checks:

`PYTHONPATH=src python3 src/operator_tests/stage3_bootstrap_tests.py --strict`

Reports are written to:

`artifacts/tests/stage3_bootstrap_<timestamp>.json`

## Lifecycle Policy Regression Tests

Run candidate-vs-active gate behavior regression checks:

`PYTHONPATH=src python3 src/operator_tests/lifecycle_policy_regression_tests.py --strict`

## Lane Authoring Quality Gate

Validate lane specs against schema rules *and* higher-level authoring checks before merge or onboarding.

Validate a single lane:

`PYTHONPATH=src python3 src/operator_ops/validate_lane.py --lane config/lanes/plex_safe_recovery_v1.json`

Validate all lanes (skips `_template`):

`PYTHONPATH=src python3 src/operator_ops/validate_lane.py --all`

Checks enforced:
- JSON schema validity
- `action_id` matches filename
- No template placeholder values (service, action_id, endpoint, description)
- Endpoint is a well-formed http/https URL
- `verify` and `rollback` arrays are non-empty
- No dangerous/mutating commands in `verify_probes`
- At least one `verify_probe` is `required: true`
- Sane tunables (retries >= 1, sleep >= 1, log_tail >= 10)
- `action_id` registered in `lane_catalog.json`
- `action_id` registered in `lane_lifecycle.json`

Exit code 0 = all passed, 1 = failures found.

The quality gate also runs automatically as the first step of `run_lane_onboarding.py` — lanes that fail validation will not proceed to onboarding.

## Lane Onboarding Runner

Run full onboarding sequence for a lane in one command (candidate -> test propose/execute -> prod dry-run propose/execute -> active -> gates):

`PYTHONPATH=src python3 src/operator_ops/run_lane_onboarding.py --action-id homeassistant_safe_recovery_v1 --task "Home Assistant dashboard unavailable" --days 7`

Example for SABnzbd onboarding:

`PYTHONPATH=src python3 src/operator_ops/run_lane_onboarding.py --action-id sabnzbd_safe_recovery_v1 --task "SABnzbd queue UI unavailable" --days 7`

Artifacts are written to:

`artifacts/onboarding/lane_onboarding_<action_id>_<timestamp>.json`

## Canonical State Writer

After successful approved execute runs, the runtime updates trusted canonical state targets.

Policy config:
- `config/state_writer_targets.json`

Trusted targets currently:
- `/opt/stack/services/apps/dashboard/status.json` (writes `operator_kernel` section only)
- `/opt/stack/services/projects/operator-kernel/artifacts/state/operator_state.json`

Review-safe rules:
- strict target allowlist
- bounded field lengths for summary/recommendation text
- schema-validated operator state artifact before write

Lane specs can define optional `verify_probes` for service-specific checks.
Each probe can include `name`, `command`, `expected_substring`, and `required`.
Required probe failures are folded into execute verification status.
