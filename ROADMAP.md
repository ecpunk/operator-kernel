# Operator Kernel Roadmap

Status date: 2026-03-10
Project path: `/opt/stack/services/projects/operator-kernel`

This roadmap is the execution companion to `10-CONCEPT/OPERATOR_KERNEL_CONCEPT.md`.
Concept doc defines product intent and guardrails. This file defines delivery stages, concrete outcomes, active priorities, and evidence history.

## Index

1. Stage Snapshot
2. Goal Alignment
3. Active Priorities
4. Priority Build Tasks
5. Soak-Parallel Task Plan (P1B/P2/P3)
6. Active Task Checklist
7. Completed and Retired Task Archive (Rolling)
8. Stage Status and Definitions
9. Evidence Archive (Detailed History)
10. Scope Boundaries
11. Where Goals Live

## Stage Snapshot

- Current delivery stage: Stage 5 (lane drafting as kernel output) — implementation phase, entry gate defined and met.
- Last completed stage: Stage 4 (open-ended evaluation, closed 2026-03-10).
- Immediate focus: build operator review and promotion workflow for Stage 5 draft lanes.
- Expansion posture: Stage 5 implementation is active; multi-strategy drafting is live in production.
- Larger context: this kernel seeds The Engine — a modular application platform. Operator Kernel is the rules and contract primitive. Engine products assemble it alongside other modules.

## Goal Alignment

- Goal: Preserve deterministic, human-approved execution.
	- Alignment signal: execute approval gate, risk tiers, and fail-closed stage scorecard gates are live.
- Goal: Improve operational reliability before autonomy expansion.
	- Alignment signal: Stage 2 exit thresholds are passing and Stage 3 readiness/scheduler health artifacts are recurring.
- Goal: Expand capability without architectural reset.
	- Alignment signal: Stage 4 and Stage 5 are framed as extensions of existing context/planning and review-first lane growth.

## Active Priorities

- P1: Monitor Stage 4 inspect threshold behavior and tune caps only when sustained evidence shows false positives or drift.
- P1: Keep Stage 4 inspect sample and metrics cadence running with `report_stage4_inspect_metrics` for ongoing quality/cost visibility.
- P2: Shim-removal closeout complete — package-native entrypoints are now standard (`src/operator_runtime`, `src/operator_ops`, `src/operator_tests`) with `PYTHONPATH=src`.
- P2: [Required] Build notification module — ops runs, test suites, and Stage 4 inspect must emit structured notifications on start, completion, failure, and key milestones (e.g., cap block, threshold breach, soak target reached). Notification routing must be configurable (stdout, webhook, alert channel). This is unacceptable to run silently. Design as a dedicated `src/operator_notify/` package with a single dispatch interface consumed by all ops, test, and runtime callers.
- P3: Deferred — `operator_runtime`, `operator_ops`, `operator_tests` as separately installable packages with `operator-kernel` as a declared dependency; revisit if deployment topology changes.

## Priority Build Tasks

- P1A (Stage 3 soak stability)
	- Task: run recurring Stage 3 orchestrator cycles under unattended policy and confirm no unresolved readiness regressions.
	- Task: collect and review scheduler health + readiness artifacts over the full soak window.
	- Done when: soak window target is met with no open incident tickets at close.
- P1B (modality parity)
	- Task: replace adapter stubs with real voice/mobile ingress normalization into the shared task contract.
	- Task: run parity tests proving mode classification and plan semantics match terminal behavior.
	- Done when: modality parity test passes for representative inspect/propose/execute scenarios.
- P2 (review-first learning quality)
	- Task: review deferred learning suggestions/deltas for precision, noise rate, and operator usefulness.
	- Task: tune selection policy before any broader apply profile changes.
	- Done when: review sample shows acceptable precision and low-noise recommendations.
- P3 (Stage 4 gate drafting)
	- Task: define measurable Stage 4 entry checks (quality, cost, safety) based on Stage 3 steady-state evidence.
	- Task: document Stage 4 entry gate in roadmap once criteria are validated.
	- Done when: Stage 4 gate criteria are explicit, measurable, and approved.

## Soak-Parallel Task Plan (P1B/P2/P3)

This section is the "can start now" queue while the 24-hour soak is still running.

- P1B track (modality parity implementation)
	- [x] Implement concrete adapter input normalization in `src/adapters/voice_adapter_stub.py`.
	- [x] Implement concrete adapter input normalization in `src/adapters/mobile_adapter_stub.py`.
	- [x] Expand `src/operator_tests/adapter_contract_tests.py` with parity assertions against representative terminal tasks.
	- [x] Add one command/example each to verify inspect, propose, and execute classification parity.
- P2 track (review-first learning quality)
	- [x] Pull latest learning suggestion artifacts and classify recommendations as useful/noisy/unsafe.
	- [x] Calculate a quick precision sample from recent learning artifacts and capture findings.
	- [x] Draft policy tuning proposal (thresholds/filters/target selection) before changing apply profiles.
	- [x] Add acceptance criteria for recommendation quality to prevent broadening policy too early.
- P3 track (Stage 4 gate pre-draft)
	- [x] Draft Stage 4 gate dimensions: quality, safety, cost, and operator trust.
	- [x] Define measurable pass/fail checks and required evidence for each gate dimension.
	- [x] Identify which checks can be pre-defined now vs checks that must wait for soak completion.
	- [x] Prepare a Stage 4 gate draft block that can be finalized immediately after soak closeout.

## Active Task Checklist

Task list hygiene policy:
- Keep this as the active execution checklist.
- Keep all open tasks here.
- Keep completed/retired tasks in the archive section only.
- Keep at most 12 most-recent completed/retired tasks in the archive section before rolling older items forward by month.
- Run archive roll-off during weekly review or after any session that closes 3+ tasks.
- Documentation gate: any architecture/runtime/layout/policy change must update `ROADMAP.md`, `README.md`, and relevant docs under `docs/` before closeout.

- [x] Stage 4: Collect N >= 50 open-ended inspect samples with source-cited hypotheses recorded in inspect artifacts and run index. (Completed 2026-03-09, open-ended samples: 54)
- [x] Stage 4: Run `PYTHONPATH=src python3 src/operator_ops/report_stage4_inspect_metrics.py --days 7 --sample-min 50` and finalize quality/cost thresholds in guardrails once sample target is met. (Completed 2026-03-09)
- [x] P2: Remove root-level compatibility entrypoints and migrate systemd/docs/ops/tests to package-native script paths. (Completed 2026-03-09)
- [x] Stage 5: Run `PYTHONPATH=src python3 src/operator_ops/report_stage5_draft_metrics.py --days 7 --sample-min 30` and confirm sample target met; baseline: 72 runs, schema_valid_rate=1.0, evidence_driven service_specific_rate=0.7917, avg_score=84.25. (Completed 2026-03-10)
- [x] Stage 5: Build operator review and promotion workflow (`review_stage5_lane_drafts.py`): list, review, approve (dry-run + apply), and reject pending draft groups; promotion writes lane spec, catalog mapping, lifecycle state=candidate, and decision artifact. Smoke tested. (Completed 2026-03-10)
- [x] Stage 5: Build unattended soak ops orchestrator (`run_stage5_soak_ops.py`): chains evaluator sweep + draft metrics + optional Stage 3 health check; writes stage5_soak_ops artifacts; integrates with operator_notify. Real-world task list at `config/stage5_sampling_tasks.json`. Smoke tested, 84 production runs, schema_valid_rate=1.0, service_specific_rate=0.8214. (Completed 2026-03-10)
- [x] Stage 5: When back — use `review_stage5_lane_drafts.py --list` to review real-world draft results from unattended soak; promote candidates that match real services; run `run_lane_onboarding.py` to validate each promoted lane.
- [x] Incident 2026-03-11: Scorecard gate block causing Stage 3 Discord failure notifications. Root cause: (1) gate-blocked test runs (empty `verification_status`) included in execute denominator; (2) no-prod-data fallback used `execute_verification_success_pct` which was inflated by intentional test-env service failures. Fix: exclude gate-blocked runs from denominator; in no_prod_data mode gate on `test_pass_rate_pct` as execute proxy. Result: confidence=89.88, signal=green, gate=pass, Stage 3 orchestrator clean pass. (Resolved 2026-03-11)
- [x] Scorecard regression suite: 8 scenarios covering gate-blocked denominator exclusion, no-prod-data proxy gating, proof-pack gate waiver, prod path happy/sad, zero-execute window, and duplicate run id detection. Wired into Stage 3 orchestrator as `--strict` step. (Completed 2026-03-11)
- [x] Stage 5: Soak gate — endpoint_specific_rate at 0.3958 across 96 runs. Closed as expected-low by design: evaluator has no service registry so endpoint inference from task text will not exceed this baseline regardless of run count. Real endpoints are filled by operator during promotion review (`review_stage5_lane_drafts.py`). service_specific_rate=0.8438 is the meaningful quality signal and is healthy. (Resolved 2026-03-11)
- [x] Stage 5: First promotion pass — 2 lanes promoted from 96-run draft corpus. homeassistant_event_queue_recovery_v1 (HA event queue, endpoint 8123) and portainer_docker_volume_recovery_v1 (portainer docker mgmt, endpoint 9443 HTTPS). 18 drafts rejected (bad service inference, no real HTTP endpoint, or duplicates). Probe fix applied to both specs after bad probe port in generated draft caught during onboarding. Both lanes in candidate state pending onboarding re-run when services confirmed accessible. (Completed 2026-03-11)
- [x] Stage 5: Re-run onboarding for `homeassistant_event_queue_recovery_v1` when HA confirmed accessible at port 8123. (Completed 2026-03-11)
- [x] Stage 5: Re-run onboarding for `portainer_docker_volume_recovery_v1` when portainer confirmed accessible at port 9443. (Completed 2026-03-11)
- [x] Lane authoring quality gate (`validate_lane.py`): standalone validator enforcing 10 checklist rules beyond JSON schema — placeholder detection, filename/action_id match, probe safety (no destructive commands), sane tunables, catalog+lifecycle registration. Runs standalone (`--lane` or `--all`) and wired as first gate in `run_lane_onboarding.py`. All 8 production lanes pass clean. (Completed 2026-03-15)
- [x] Stage 5: Tooling hardening — three systemic fixes applied: (1) `review_stage5_lane_drafts.py` now propagates `--endpoint` into all `verify_probes` command strings (not just top-level spec field) via `_rewrite_probe_commands()`; (2) `run_lane_onboarding.py` curl pre-flight checks endpoint and every probe URL before any run-index or lifecycle writes; (3) `execution_lane.py` warmup probe curl adds `-k` automatically for `https://` endpoints to handle self-signed certs. All three smoke tested. (Completed 2026-03-11)

- [x] P1A: Complete 24h Stage 3 soak closeout with clean unattended runs. (Completed 2026-03-08)
- [x] P1A: Confirm no unresolved Stage 3 incidents at soak closeout. (Completed 2026-03-08)
- [x] Docs: Write lane authoring guide and template lane spec for operator/Engine module consumers. Covers required fields, keyword mapping structure, verify_probes, lifecycle states, and onboarding command invocation. Soak-safe. (Completed 2026-03-07)
- [x] Post-Stage 3: Package kernel as `operator-kernel` v0.1.0 (pyproject.toml + __init__.py, public API surface defined, operational tooling separated from kernel source tree). Gate: Stage 3 closeout complete. (Completed 2026-03-08)

## Completed and Retired Task Archive (Rolling)

### 2026-03
- [x] P1A: Complete 24h Stage 3 soak closeout with clean unattended runs. (Completed 2026-03-08)
- [x] P1A: Confirm no unresolved Stage 3 incidents at soak closeout. (Completed 2026-03-08)
- [x] Post-Stage 3: Package kernel as `operator-kernel` v0.1.0 and separate source into `src/operator_kernel`, `src/operator_runtime`, `src/operator_ops`, and `src/operator_tests`; compatibility entrypoints later removed during P2 closeout. (Completed 2026-03-08)
- [x] P2: Review learning suggestion/delta artifacts; classify precision/noise sample. (Completed 2026-03-07)
- [x] P2: Document policy tuning recommendation; hold review-only, propose lane_required filter. (Completed 2026-03-07)
- [x] P3: Draft Stage 4 entry gate pre-draft with four dimensions and finalization gates. (Completed 2026-03-07)
- [x] P1B: Implement real voice/mobile adapter normalization path. (Completed 2026-03-07)
- [x] P1B: Pass modality parity tests across representative scenarios. (Completed 2026-03-07)
- [x] Reframe roadmap structure to separate active decision view from history archive. (Completed 2026-03-07)
- [x] Reframe roadmap with index + stage snapshot + goal alignment + active priorities. (Completed 2026-03-07)
- [x] Add Stage 4 and Stage 5 direction-only sections. (Completed 2026-03-07)
- [x] Add portability-by-design principle to concept alignment. (Completed 2026-03-07)

## Stage Status and Definitions

## Stage 1: V1 Closure (Complete)

Status: complete (2026-03-07).

Goal: make the existing contract loop fully operationally safe and measurable.

Exit criteria:
- Risk tiers are attached to plans and enforced during execute.
- Auto-gates block unsafe execute paths by policy, not operator memory.
- Run and archive reporting provide recurring signal visibility.
- Canonical status/state writer updates trusted docs after approved execute runs.
- Acceptance tests in concept section 11 are runnable and passing for current lanes.

Current scope:
- inspect/propose/execute contract is live.
- approval token enforcement is live.
- proof pack generation is live.
- lane catalog and lane specs are live.
- recommendation confidence threshold policy is live.
- schema validation for runtime configs and artifacts is live.
- run index is live.
- artifact retention with soft deprecation archive is live.
- archive activity report utility is live.
- risk tier policy with execute auto-gates is live.
- consolidated run report utility is live.
- canonical state writer updates are live for approved execute runs.
- deterministic go/no-go test suite is live.
- lane keyword precedence policy is live (priority, whole-word preference, longest-keyword tie-break).
- rolling stage scorecard generation is live.
- dashboard scorecard export is live.
- run index id hygiene repair is live (duplicate id normalization + repair utility).
- stage confidence swing recorded as productive measurement hardening: 82.88 -> 96.38 after run-type separation and historical test-run reclassification.

Final Stage 1 closeout outcomes:
1. Stage-gate decision thresholds fail closed via scorecard policy and enforcement mode.
2. Operational verification metric reached green target after run-type separation (`prod` vs `test`).

## Stage 2: V1.1 Operationalization (Complete)

Status: complete (2026-03-07).

Goal: productionize reliability and operator ergonomics without expanding autonomy.

Target outcomes:
- Stable operator runbooks generated from lane specs and proof patterns.
- Trend metrics tracked over rolling windows (MTTT, MTTVF, recurrence, approval failures).
- Stronger schema strictness and migration versioning for config/artifact evolution.
- Service-level scorecards for each lane with explicit confidence and failure classes.

Entry gate:
- Stage 1 exit criteria complete.

Exit thresholds (all met):
- stage scorecard gate status must be `pass` under fail-closed policy.
- service scorecard gate status must be `pass` under fail-closed policy.
- migration policy validation must pass at runtime startup.
- soak evidence must include at least 3 snapshots within a 24-hour window.

## Stage 3: V2 Controlled Expansion (Complete)

Status: complete (closed 2026-03-08). P1A soak confirmed 113 clean snapshots. All deliverables met.

Goal: expand lane coverage and ingress channels while preserving deterministic behavior.

Target outcomes:
- Additional lanes onboarded through config-first process.
- Voice and mobile adapters reach modality parity with terminal contracts.
- Deferred learning loop activated in review-first mode for policy/doc suggestions.

Entry gate:
- Stage 2 metrics show stable safety and cost performance over a sustained period.

## Stage 4: Open-Ended Evaluation (Closed)

Status: closed 2026-03-10. Entered 2026-03-09, operator approval given 2026-03-09.

Goal: extend inspect mode beyond lane-matching so open-ended questions against real system context produce useful structured answers without requiring a pre-built lane.

This is an architectural extension of the existing context and planning layers, not a rewrite. Inspect is now functional with lane-matched/open-ended branches, source-cited hypotheses, and artifact/run-index instrumentation.

Implementation scope:
- Lane-matched inspect: when task resolves to a lane, use lane structure + loaded context sources to produce ranked hypotheses with service-specific evidence.
- Open-ended inspect: when task does not resolve to a lane, inspect all loaded context sources and produce ranked hypotheses with explicit source attribution. No action_id emitted. Strictly read-only.
- Cost instrumentation: record token count and estimated cost per inspect request in the run index.
- Source traceability: every claim in an inspect response must cite a context_sources id.
- Sample collection: log open-ended inspect artifacts for quality review; finalize thresholds after N >= 50 samples.

### Stage 4 Entry Gate

Status: entered. Two gate dimensions still have in-flight tasks; they will be resolved during Stage 4 execution.

Four gate dimensions:

- Safety (pre-definable now):
	- Open-ended inspect remains strictly read-only; no action_id emitted from open-ended responses.
	- No execution path transitions without an explicit operator propose + approve cycle.
	- Pass check: architecturally enforced by contract; validate with at least one staged inspect/propose/execute dry-run cycle before Stage 4 is declared active.
- Quality (threshold TBD after soak data):
	- Inspect responses cite only verified context_sources content; no fabricated service references.
	- Pass check: response traceability >= threshold (define after N >= 50 real open-ended inspect samples).
	- Pre-definable: response must include source attribution and contain no unverifiable claims.
- Cost (baseline TBD):
	- Token and cost per open-ended inspect within a defined per-request budget.
	- Pass check: cost-per-request <= cap (define after initial open-ended inspect samples produce cost data).
	- Pre-definable: a hard budget cap and fail-closed behavior must exist before Stage 4 is enabled.
- Operator trust (partially pre-definable):
	- Stage 3 P1A must be confirmed closed with no open incident tickets.
	- Stage 3 readiness status must be green with >= 100 soak snapshots in the active window.
	- Current state: 113 soak snapshots, all checks green. P1A confirmed closed 2026-03-08. Snapshot target met.
	- Pass check: P1A closed + 100+ clean soak snapshots + operator explicit approval to enter Stage 4.

Pre-definable now (locked gate checks today):
- Safety enforcement is architecturally in place.
- Operator trust snapshot target: >= 100 soak snapshots (met: 113 as of 2026-03-09).
- Stage 3 P1A closeout is a hard prerequisite (met: closed 2026-03-08).

Current Stage 4 metrics checkpoint (2026-03-09):
- Sample tracker command added: `PYTHONPATH=src python3 src/operator_ops/report_stage4_inspect_metrics.py --days 7 --sample-min 50`.
- Current baseline: inspect_total=57, open_ended_total=56, lane_matched_total=1.
- Traceability baseline: 1.0 (all open-ended hypotheses cite evidence sources).
- Cost baseline: p95 estimated_cost_usd=0.000272, p95 token_estimate=136.
- Cost calibration upgrade: inspect now supports provider-calculated cost when `--provider`, `--model`, `--input-tokens`, and `--output-tokens` are supplied; artifacts/run index record `cost_source` for audit.
- Cost calibration automation: inspect now supports `--usage-json` and `INSPECT_USAGE_JSON` for automatic provider/model/token usage ingestion without repeating CLI token flags each run.

Finalized Stage 4 thresholds (2026-03-10, enforced in `config/guardrails.json`):
- `min_traceability_ratio`: 0.98
- `max_open_ended_token_estimate`: 384 (p95 observed over 65 open-ended runs)
- `max_open_ended_estimated_cost_usd`: 0.000768 (p95 observed over 65 open-ended runs)
- Enforcement mode: `enforce_open_ended_thresholds=true`

Stage 4 closeout (2026-03-10):
- Sample target met: 65 open-ended runs, 1 lane-matched.
- Traceability: 1.0 ratio across all open-ended hypotheses.
- Cost calibration: 4 runs used provider_usage data; 61 used heuristic.
- Thresholds updated from initial estimates (138 tokens / $0.000276) to observed p95 (384 tokens / $0.000768).
- Notification module shipped: operator_notify with Discord webhook, failure-only routing, hard latency caps.
- Operator explicit approval: given 2026-03-09.

## Stage 5: Lane Drafting As Kernel Output (Active — Implementation Phase)

Status: implementation phase entered 2026-03-10. Entry gate defined and all checks met. Drafting is live in production.

Goal: when a task does not match any existing lane, the kernel drafts a candidate lane spec as a proposal artifact for operator review and approval, rather than returning nothing. Humans still control what enters the trusted executable set.

### Stage 5 Entry Gate

Status: all checks met (2026-03-10).

- Safety (met — architecturally enforced):
	- Drafted lane specs are written as proposal artifacts only; no auto-promotion to the trusted executable set.
	- No execution path transitions without explicit operator propose + approve cycle.
	- Drafting runs only on open-ended inspect branch; no mutation permitted.
- Quality (met — thresholds enforced in `config/guardrails.json`):
	- Schema valid rate for primary strategy (evidence_driven): 1.0 (production baseline: 72/72 runs).
	- Malformed drafts (schema_errors present) are blocked from recommendation by `require_schema_valid=true`.
	- Minimum recommend score: 60 (enforced in guardrails; evaluator baseline avg_score=84.25).
	- Service-specific rate for primary strategy: 0.7917 (target: >= 0.75, met).
- Cost (met — within Stage 4 open-ended inspect budget):
	- Draft generation runs in parallel with open-ended inspect; no separate cost tier.
	- Drafting must not push per-request cost above Stage 4 p95 cap ($0.000768 / 384 tokens).
- Operator trust (met):
	- Stage 4 confirmed closed (2026-03-10).
	- 72 production inspect runs observed generating drafts; all schema valid.
	- Operator explicit approval: given ("go ahead" 2026-03-10).

### Stage 5 Implementation Scope

Delivered:
- Multi-strategy drafting live in `src/operator_runtime/inspect_engine.py`: generates `evidence_driven`, `template_first`, and `nearest_lane` candidate specs per open-ended inspect run.
- Draft artifacts written to `artifacts/lane_drafts/lane_draft_{stamp}_{strategy}.json`.
- Batch evaluator at `src/operator_ops/evaluate_stage5_lane_drafts.py`: scores strategies across representative task sets; writes to `artifacts/stage5/stage5_lane_draft_eval_*.json`.
- Draft quality guardrails enforced in `config/guardrails.json` under `stage5_drafts`: primary_strategy, fallback_strategy, min_recommend_score, require_schema_valid, require_non_placeholder_service.
- Ongoing metrics tool at `src/operator_ops/report_stage5_draft_metrics.py`: reads production draft artifacts; reports per-strategy quality rates, gate progress, and evaluator summary.
- Operator review and promotion tool at `src/operator_ops/review_stage5_lane_drafts.py`: list/review/approve/reject draft groups; promotion writes lane spec, catalog mapping, lifecycle state=candidate, and decision artifact. Token-gated apply with dry-run default.
- Unattended soak ops orchestrator at `src/operator_ops/run_stage5_soak_ops.py`: chains evaluator sweep + draft metrics + optional Stage 3 health check; integrates with operator_notify. Real-world task list at `config/stage5_sampling_tasks.json`.

Remaining:
- Re-run lane onboarding for `homeassistant_event_queue_recovery_v1` when HA is confirmed accessible at `http://<HOST>:8123`: `PYTHONPATH=src python3 src/operator_ops/run_lane_onboarding.py --action-id homeassistant_event_queue_recovery_v1 --task "analyze home automation event queue delays and suggest lane"`. Probe fix applied (was pointing at port 6767/bazarr; now correct at 8123 with expected_substring "Home Assistant").
- Re-run lane onboarding for `portainer_docker_volume_recovery_v1` when portainer is confirmed accessible at `https://<HOST>:9443`: `PYTHONPATH=src python3 src/operator_ops/run_lane_onboarding.py --action-id portainer_docker_volume_recovery_v1 --task "investigate docker volume permission errors and draft lane"`. Probe fix applied (added -k for self-signed cert).
- After first promoted lane completes a successful production execute run: auto-promote from candidate to active via existing lifecycle policy.
- Soak gate: closed — endpoint_specific_rate expected-low by design; operators fill real endpoints during promotion review.

### Stage 5 Unattended Plan (2026-03-10)

Commands to run unattended while operator is away:

Step 1 — Stage 5 soak ops with real-world task sampling:
  `PYTHONPATH=src python3 src/operator_ops/run_stage5_soak_ops.py --days 7 --sample-min 30`
  Runs: evaluator sweep (15 real-world tasks) + draft metrics + Stage 3 health check.
  Writes: `artifacts/stage5/stage5_soak_ops_latest.json`

Step 2 — (Optional) Second pass with custom tasks to expand corpus:
  `PYTHONPATH=src python3 src/operator_ops/evaluate_stage5_lane_drafts.py --tasks-file config/stage5_sampling_tasks.json`
  Writes: `artifacts/stage5/stage5_lane_draft_eval_*.json`

Step 3 — Refresh draft metrics after corpus growth:
  `PYTHONPATH=src python3 src/operator_ops/report_stage5_draft_metrics.py --days 7`

Systemd: Stage 3 orchestrator runs on its existing timer (no action needed).

When back, start here:
  1. Check soak ops status: `cat artifacts/stage5/stage5_soak_ops_latest.json | python3 -c "import json,sys; d=json.load(sys.stdin); print('status:', d['status']); print('metrics:', d['metrics_summary'])"`
  2. List pending draft candidates: `PYTHONPATH=src python3 src/operator_ops/review_stage5_lane_drafts.py --list`
  3. Review and promote any real-service candidates (plex, sonarr, radarr, sabnzbd, etc.).

Stage 5 method learning (2026-03-10):
- Multi-strategy drafting (template_first, evidence_driven, nearest_lane) produced higher-quality outcomes than single-strategy drafting.
- Practical pattern: run 3 distinct strategies in parallel, score all outputs, and keep alternates for operator review even when one strategy is recommended.
- Learning loop: track wins, schema-valid rate, service-specific rate, endpoint-specific rate, and retain both successes and failures as tuning data.
- Current baseline from evaluator: evidence_driven is strongest after service inference improvements; template_first remains the safe fallback.

Stage 5 production baseline (2026-03-10, post-soak-ops):
- Production draft runs: 84 unique inspect runs, all schema valid.
- evidence_driven: schema_valid_rate=1.0, service_specific_rate=0.8214.
- template_first: schema_valid_rate=1.0, service_specific_rate=0.8214.
- nearest_lane: schema_valid_rate=1.0, service_specific_rate=0.8333.
- Endpoint specificity: evidence_driven=0.3889 — expected low; endpoint values require operator input to replace placeholder on promotion.
- Evaluator latest (12 synthetic tasks): evidence_driven avg_score=84.25, win_rate=1.0.
- Soak ops artifact: `artifacts/stage5/stage5_soak_ops_latest.json`.

## Evidence Archive (Detailed History)

This section keeps complete milestone and artifact history for traceability.
Operational decision-making should prioritize `Stage Snapshot`, `Goal Alignment`, and `Active Priorities` first.

### Stage 1 Evidence

Next testing pause:
- now active.
- uses broad-spectrum matrix in `TESTING_PHASE_PLAN.md`.
- latest execution evidence: `artifacts/tests/broad_spectrum_batch1_20260307T043433Z.json`.
- latest execution evidence: `artifacts/tests/broad_spectrum_batch2_20260307T043837Z.json`.
- latest execution evidence: `artifacts/tests/broad_spectrum_batch3_20260307T044427Z.json`.
- latest execution evidence: `artifacts/tests/broad_spectrum_batch4_20260307T044630Z.json`.
- latest execution evidence: `artifacts/tests/broad_spectrum_batch5_20260307T045212Z.json`.
- latest doc-delta simulation: `artifacts/tests/doc_delta_suggestions_batch5_20260307T045211Z.json`.
- latest scorecard snapshot: `artifacts/scorecards/stage_scorecard_20260307T050703Z.json`.
- latest scorecard snapshot: `artifacts/scorecards/stage_scorecard_20260307T051056Z.json`.
- latest scorecard snapshot: `artifacts/scorecards/stage_scorecard_20260307T051922Z.json`.
- confidence swing note: score moved from 82.88 (`artifacts/scorecards/stage_scorecard_20260307T051056Z.json`) to 96.38 (`artifacts/scorecards/stage_scorecard_20260307T051922Z.json`) after isolating operational metrics from synthetic test runs.

### Stage 2 Evidence

Current Stage 2 progress:
- runbook generator implemented at `src/operator_ops/generate_runbooks.py`.
- generated runbook artifacts are written under `artifacts/runbooks/`.
- rolling trend report implemented at `src/operator_ops/report_trends.py` with artifacts under `artifacts/trends/`.
- latest runbook index: `artifacts/runbooks/RUNBOOKS.md`.
- latest trend snapshot: `artifacts/trends/trend_report_20260307T053040Z.json`.
- migration/version policy is fail-closed via `config/migration_policy.json` validation at runtime.
- service-level scorecards are generated by `src/operator_ops/report_service_scorecards.py` into `artifacts/service_scorecards/`.
- service-level scorecards support dashboard export into `apps/dashboard/status.json`.

Stage 2 soak workflow:
- run `PYTHONPATH=src python3 src/operator_ops/run_stage2_soak_snapshot.py --days 7` at each checkpoint.
- artifacts are written to `artifacts/soak/` and include linked scorecard/trend evidence.
- use `--enforce-exit` to fail closed when Stage 2 exit requirements are not satisfied.
- service scorecard gate ignores non-lane placeholder action ids via policy (`ignore_action_ids: ["none"]`).
- latest Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T053834Z.json`.
- latest Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T054130Z.json`.

Stage 3 bootstrap and expansion history:
- new lane onboarded: `radarr_safe_recovery_v1` via `config/lanes/radarr_safe_recovery_v1.json` and `config/lane_catalog.json` mapping.
- bootstrap verification suite added: `src/operator_tests/stage3_bootstrap_tests.py`.
- new lane onboarded: `bazarr_safe_recovery_v1` via `config/lanes/bazarr_safe_recovery_v1.json` and `config/lane_catalog.json` mapping.
- new lane onboarded: `homeassistant_safe_recovery_v1` via `config/lanes/homeassistant_safe_recovery_v1.json` and `config/lane_catalog.json` mapping.
- new lane onboarded: `sabnzbd_safe_recovery_v1` via `config/lanes/sabnzbd_safe_recovery_v1.json` and `config/lane_catalog.json` mapping.
- latest Stage 3 bootstrap report: `artifacts/tests/stage3_bootstrap_20260307T054354Z.json`.
- latest Stage 3 bootstrap report: `artifacts/tests/stage3_bootstrap_20260307T054729Z.json`.
- latest Stage 3 bootstrap report: `artifacts/tests/stage3_bootstrap_20260307T055859Z.json`.
- latest Stage 3 bootstrap report: `artifacts/tests/stage3_bootstrap_20260307T060346Z.json`.
- latest Stage 3 bootstrap report: `artifacts/tests/stage3_bootstrap_20260307T061054Z.json`.
- latest Stage 3 bootstrap report: `artifacts/tests/stage3_bootstrap_20260307T061557Z.json`.
- latest Stage 3 bootstrap report: `artifacts/tests/stage3_bootstrap_20260307T062148Z.json`.
- latest Stage 3 bootstrap report: `artifacts/tests/stage3_bootstrap_20260307T062508Z.json`.
- latest Stage 3 bootstrap report: `artifacts/tests/stage3_bootstrap_20260307T062633Z.json`.
- latest Stage 3 bootstrap report: `artifacts/tests/stage3_bootstrap_20260307T063642Z.json`.
- latest post-expansion Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T054405Z.json`.
- latest post-expansion Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T054738Z.json`.
- latest post-expansion Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T055859Z.json`.
- latest post-expansion Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T060346Z.json`.
- latest post-expansion Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T061047Z.json`.
- latest post-expansion Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T061557Z.json`.
- latest post-expansion Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T062148Z.json`.
- latest post-expansion Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T062508Z.json`.
- latest post-expansion Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T062634Z.json`.
- latest post-expansion Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T063642Z.json`.
- latest broad spectrum batch 2 report: `artifacts/tests/broad_spectrum_batch2_20260307T063631Z.json`.
- lane lifecycle policy added at `config/lane_lifecycle.json` with states `candidate` and `active`.
- service scorecard gate supports candidate exemption policy for onboarding flow.
- one-command onboarding runner added: `src/operator_ops/run_lane_onboarding.py`.
- lifecycle gate regression suite added: `src/operator_tests/lifecycle_policy_regression_tests.py`.
- latest onboarding runner artifact: `artifacts/onboarding/lane_onboarding_sabnzbd_safe_recovery_v1_20260307T055852Z.json`.
- lane specs now support service-specific `verify_probes` with required probe enforcement in execute verification.
- deferred learning loop is now active in review-only mode via `src/operator_ops/report_learning_suggestions.py` and is included in Stage 2 soak snapshots.
- latest deferred learning artifact: `artifacts/learning/learning_suggestions_20260307T061047Z.json`.
- active-lane required probe coverage expanded to `plex_safe_recovery_v1`, `sonarr_safe_recovery_v1`, `radarr_safe_recovery_v1`, and `bazarr_safe_recovery_v1`.
- latest deferred learning artifact: `artifacts/learning/learning_suggestions_20260307T061557Z.json`.
- latest deferred learning artifact: `artifacts/learning/learning_suggestions_20260307T062148Z.json`.
- review-ready concept/runbook delta drafting is now implemented via `src/operator_ops/report_learning_deltas.py` and included in Stage 2 soak snapshots.
- latest learning delta artifact: `artifacts/learning/learning_deltas_20260307T062141Z.json`.
- latest learning delta artifact: `artifacts/learning/learning_deltas_20260307T062148Z.json`.
- controlled approval-based application workflow for selected delta targets is now implemented via `src/operator_ops/apply_learning_deltas.py` (dry-run default, allowlisted targets, token-gated apply mode).
- apply workflow now includes `--list-targets` for index/path discovery before selection.
- lane keyword resolution now prefers earliest matched keyword mention before longest-keyword tie-break to keep mixed-service phrases deterministic across wording variants.
- broad-spectrum batch 2 deterministic phrase coverage now validates mixed-service order and priority interactions across multiple phrasing variants.
- latest learning delta apply dry-run artifact: `artifacts/learning/learning_delta_apply_20260307T062501Z.json`.
- latest learning delta apply dry-run artifact: `artifacts/learning/learning_delta_apply_20260307T062627Z.json`.
- latest broad spectrum batch 2 report: `artifacts/tests/broad_spectrum_batch2_20260307T063801Z.json`.
- latest Stage 3 bootstrap report: `artifacts/tests/stage3_bootstrap_20260307T063808Z.json`.
- latest post-expansion Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T063808Z.json`.
- single-command Stage 3 orchestrator implemented at `src/operator_ops/run_stage3_operations.py`.
- delta-apply policy profiles implemented (`concept_only`, `runbooks_only`, `top3`, `top5`, `all`) in `src/operator_ops/apply_learning_deltas.py`.
- scheduling assets added under `systemd/` for recurring Stage 3 runs.
- Stage 3 readiness rollup added at `src/operator_ops/report_stage3_readiness.py` with dashboard export key `operator_kernel_stage3`.
- orchestrator now emits failure alert artifacts and supports optional webhook notification (`STAGE3_ALERT_WEBHOOK_URL`).
- readiness now emits incident ticket artifacts on not-ready runs (`stage3_incident_*.json` and `.md`).
- Stage 3 service now loads default unattended args from `systemd/operator-kernel-stage3-ops.env`.
- scheduler health reporter added at `src/operator_ops/report_stage3_scheduler_health.py` and enforced inside Stage 3 orchestrator.
- Stage 3 alert transport now supports policy-driven retry/backoff/dedupe and unattended-apply protection via `stage3_operations` guardrails.
- incident lifecycle manager added at `src/operator_ops/manage_stage3_incident.py` with open/ack/resolve workflow and registry tracking.
- voice/mobile adapter contract scaffolding added (`src/operator_kernel/adapter_contract.py`, `src/operator_tests/adapter_contract_tests.py`, `src/adapters/*_adapter_stub.py`).
- artifact GC now fails closed without process crash on archive permission-denied moves and records explicit `archive_move_failed` errors for operator visibility.
- broad-spectrum Batch 2 includes a regression test for non-fatal GC permission failures (`openclaw_learned.gc_permission_denied_nonfatal`).
- latest Stage 3 orchestrator artifact: `artifacts/stage3/stage3_operations_20260307T064309Z.json`.
- latest Stage 3 orchestrator artifact: `artifacts/stage3/stage3_operations_20260307T064942Z.json`.
- latest Stage 3 orchestrator artifact: `artifacts/stage3/stage3_operations_20260307T065516Z.json`.
- latest Stage 3 orchestrator artifact: `artifacts/stage3/stage3_operations_20260307T065706Z.json`.
- latest Stage 3 orchestrator artifact: `artifacts/stage3/stage3_operations_20260307T065935Z.json`.
- latest Stage 3 orchestrator artifact: `artifacts/stage3/stage3_operations_20260307T070850Z.json`.
- latest Stage 3 readiness artifact: `artifacts/stage3/stage3_readiness_20260307T064309Z.json`.
- latest Stage 3 readiness artifact: `artifacts/stage3/stage3_readiness_20260307T065516Z.json`.
- latest Stage 3 readiness artifact: `artifacts/stage3/stage3_readiness_20260307T065706Z.json`.
- latest Stage 3 alert artifact (failure-path validation): `artifacts/stage3/stage3_alert_20260307T065453Z.json`.
- latest Stage 3 alert artifact (webhook 200 OK validation): `artifacts/stage3/stage3_alert_20260307T065645Z.json`.
- latest Stage 3 scheduler health artifact: `artifacts/stage3/stage3_scheduler_health_20260307T065935Z.json`.
- latest Stage 3 scheduler health artifact: `artifacts/stage3/stage3_scheduler_health_20260307T070850Z.json`.
- latest broad spectrum batch 2 report: `artifacts/tests/broad_spectrum_batch2_20260307T064302Z.json`.
- latest broad spectrum batch 2 report: `artifacts/tests/broad_spectrum_batch2_20260307T070835Z.json`.
- latest Stage 3 bootstrap report: `artifacts/tests/stage3_bootstrap_20260307T064308Z.json`.
- latest post-expansion Stage 2 soak pass snapshot: `artifacts/soak/stage2_soak_snapshot_20260307T064309Z.json`.

### Stage 3 Learning Quality Findings (P2 Review — 2026-03-07)

- sample: 7-day window, 73 prod runs (include_tests: false).
- suggestions generated: 7 (min_task_recurrence=3).
- delta targets selected: 5 (requires valid lane mapping).
- classification:
	- high-signal (4): `Fix Plex after recent restart` (22x), `Sonarr not responding on web` (21x), `Home Assistant dashboard unavailable` (4x), `Radarr web UI unavailable after update` (3x).
	- borderline (1): `clawplexalt web ui unavailable` (6x) — maps to `plex_safe_recovery_v1`; task name looks synthetic but lane assignment is correct.
	- likely noise/synthetic (2): `oaklane42 web ui unavailable` (3x), `plexalt web ui unavailable` (3x) — no lane mapping; correctly not promoted to delta targets.
- precision at delta-selection level: 5/5 (100%). All selected items have valid action_id and lane.
- noise rate at suggestion level: 2/7 (29%). Both noise items filtered before delta application.
- policy finding: review-only posture is correct for active soak period; do not broaden apply policy now.
- tuning proposal:
	- add `lane_required` filter at suggestion generation level to clean the suggestion list upstream of deltas.
	- raise `min_task_recurrence` from 3 to 5 at next cycle review to reduce synthetic long-tail noise.
	- hold review-only mode until Stage 3 soak closeout confirms sustained low noise.
- acceptance criteria for broadening apply policy:
	- precision (selected with valid lane / total selected) >= 95%.
	- noise rate at suggestion level < 20%.
	- Stage 3 soak closeout confirmed with no open incidents.

## Scope Boundaries

Out of scope for current delivery stages unless explicitly approved by future stage policy:
- autonomous mutating loops
- hidden provider escalation
- broad unsupervised orchestration

## Where Goals Live

Long-range goals and product constraints:
- `10-CONCEPT/OPERATOR_KERNEL_CONCEPT.md`

Delivery stages, current status, and task sequencing:
- `ROADMAP.md` (this file)

Near-term operator commands and local run instructions:
- `README.md`

Testing phase timing and broad-spectrum matrix:
- `TESTING_PHASE_PLAN.md`
