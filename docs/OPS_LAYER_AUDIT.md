# Ops Layer Structural Audit
Date: 2026-03-11T05:29:44Z
Scope: src/operator_ops, src/operator_runtime, src/operator_tests, src/adapters

## Executive Summary
Ops-layer architecture is functional but structurally over-coupled through artifact files and orchestration scripts that share implicit contracts instead of typed interfaces.
The kernel boundary remains clean; recent incidents are consistent with late-failing ops assumptions (endpoint/probe mismatch, missing preflight guards, TLS warmup assumptions, scorecard denominator coupling).
No Python-level circular imports were found in the audited scope, but there is substantial runtime coupling through transitive command chains and latest-artifact reads.
Primary risk concentration is in large orchestrators and multi-responsibility scripts (`operator_runtime/main.py`, `operator_runtime/inspect_engine.py`, `operator_ops/review_stage5_lane_drafts.py`, `operator_ops/run_stage3_operations.py`).
Priority order: 1) formalize artifact contracts and validation, 2) standardize reusable preflight guards, 3) split orchestration vs policy/evaluation concerns, 4) retire duplicate/vestigial adapter and dispatcher paths.

## File Inventory
Audit notes: 50 source `.py` files audited. `__pycache__/*.pyc` excluded as compiled non-source artifacts.

| File | Purpose | Kernel Imports | Ops-Layer Imports | Config Reads | Artifact Reads/Writes | External Shell/Commands |
|---|---|---|---|---|---|---|
| `src/adapters/__init__.py` | Compatibility package forwarding to runtime adapters. | - | - | - | - | - |
| `src/adapters/mobile_adapter_stub.py` | Top-level import shim for mobile adapter stub. | - | operator_runtime.adapters.mobile_adapter_stub | - | - | - |
| `src/adapters/voice_adapter_stub.py` | Top-level import shim for voice adapter stub. | - | operator_runtime.adapters.voice_adapter_stub | - | - | - |
| `src/operator_ops/__init__.py` | Package marker for operator_ops tools. | - | - | - | - | - |
| `src/operator_ops/apply_learning_deltas.py` | Applies or dry-runs learning delta edits against allowlisted targets. | - | - | config | artifacts, artifacts/learning/learning_deltas_latest.json, artifacts/runbooks/runbook_, artifacts/runbooks/runbook_*.md | - |
| `src/operator_ops/evaluate_stage5_lane_drafts.py` | Runs open-ended inspect across task set and scores multi-strategy lane drafts. | operator_kernel.lane_catalog | operator_runtime.context_loader, operator_runtime.inspect_engine | config | artifacts | docker |
| `src/operator_ops/generate_runbooks.py` | Builds runbook markdown from lane specs and recent proof patterns. | - | - | config | artifacts, artifacts/runbooks | - |
| `src/operator_ops/manage_stage3_incident.py` | Creates/acks/resolves stage3 incident records. | - | - | - | artifacts | - |
| `src/operator_ops/repair_run_index.py` | Repairs duplicate/missing run IDs in run index via runtime helper. | - | operator_runtime.run_index | - | - | - |
| `src/operator_ops/report_archive_activity.py` | Reports archive/deprecation activity for artifacts lifecycle. | - | - | - | artifacts | - |
| `src/operator_ops/report_learning_deltas.py` | Transforms learning suggestions into review-ready delta targets and draft content. | operator_kernel.lane_catalog | - | config | artifacts, artifacts/learning/learning_suggestions_latest.json, artifacts/runbooks/runbook_ | - |
| `src/operator_ops/report_learning_suggestions.py` | Generates recurring-task/failure suggestions from run history. | - | - | config, config/guardrails.json, config/lanes | artifacts | - |
| `src/operator_ops/report_runs.py` | Consolidated run-index and archive summary reporting. | - | - | - | artifacts | - |
| `src/operator_ops/report_scorecard.py` | Computes stage confidence scorecard and fail-closed gate status. | - | - | config | artifacts | - |
| `src/operator_ops/report_service_scorecards.py` | Computes per-action service scorecards and lifecycle-aware gate. | - | - | config | artifacts | - |
| `src/operator_ops/report_stage3_readiness.py` | Rolls up readiness from latest gate artifacts and suites. | - | - | - | artifacts | - |
| `src/operator_ops/report_stage3_scheduler_health.py` | Checks scheduler/timer health and artifact staleness. | - | - | - | artifacts | systemctl |
| `src/operator_ops/report_stage4_inspect_metrics.py` | Computes Stage 4 inspect quality/cost metrics and gate. | - | - | - | artifacts | - |
| `src/operator_ops/report_stage5_draft_metrics.py` | Computes Stage 5 lane-draft quality metrics and sample progress. | - | - | config | artifacts | - |
| `src/operator_ops/report_trends.py` | Trend report over run-index for recurrence and timing proxies. | - | - | - | artifacts | - |
| `src/operator_ops/review_stage5_lane_drafts.py` | Operator review/promotion/rejection workflow for stage5 lane drafts. | - | - | config | artifacts | - |
| `src/operator_ops/run_lane_onboarding.py` | End-to-end lane onboarding runner (candidate->test/prod->gates). | - | - | config | artifacts | curl |
| `src/operator_ops/run_stage2_soak_snapshot.py` | Runs Stage 2 soak checks and writes exit snapshot artifact. | operator_kernel.schema_validator | operator_notify | config | artifacts | - |
| `src/operator_ops/run_stage3_operations.py` | Main stage3 orchestrator chaining learning/tests/gates/readiness/scheduler checks. | - | operator_notify | config | artifacts | - |
| `src/operator_ops/run_stage5_soak_ops.py` | Unattended stage5 orchestrator for evaluator+metrics(+optional stage3 check). | - | operator_notify | - | artifacts | - |
| `src/operator_runtime/__init__.py` | Package marker for runtime layer. | - | - | - | - | - |
| `src/operator_runtime/adapters/__init__.py` | Package marker for runtime adapter stubs. | - | - | - | - | - |
| `src/operator_runtime/adapters/mobile_adapter_stub.py` | Runtime mobile adapter stub implementation. | - | - | - | - | - |
| `src/operator_runtime/adapters/voice_adapter_stub.py` | Runtime voice adapter stub implementation. | - | - | - | - | - |
| `src/operator_runtime/artifact_gc.py` | Artifact retention/deprecation/purge engine. | operator_kernel.schema_validator | - | config | artifacts | - |
| `src/operator_runtime/context_loader.py` | Loads ordered context sources and reports preflight hydration status. | - | - | - | - | - |
| `src/operator_runtime/execution_lane.py` | HTTP service recovery execution lane runner and verification engine. | operator_kernel.lane_catalog | operator_runtime.observation_engine | - | - | curl, docker |
| `src/operator_runtime/inspect_engine.py` | Inspect report generator incl lane-match/open-ended branches and stage5 draft emit. | operator_kernel.lane_catalog, operator_kernel.schema_validator | - | config | artifacts | curl |
| `src/operator_runtime/main.py` | Primary runtime entrypoint for inspect/propose/execute contract flows. | operator_kernel.contract_engine, operator_kernel.lane_catalog, operator_kernel.schema_validator | operator_notify, operator_runtime.artifact_gc, operator_runtime.context_loader, operator_runtime.execution_lane, operator_runtime.inspect_engine, operator_runtime.plan_builder, operator_runtime.run_index, operator_runtime.state_writer | config | artifacts | - |
| `src/operator_runtime/observation_engine.py` | Maps observations/signatures into recommendation hints. | - | - | - | - | - |
| `src/operator_runtime/plan_builder.py` | Builds deterministic media incident propose plan records. | operator_kernel.contract_engine | - | - | - | - |
| `src/operator_runtime/run_index.py` | Appends and caps run-index records with schema validation. | operator_kernel.schema_validator | - | config | artifacts | - |
| `src/operator_runtime/state_writer.py` | Writes canonical state targets after execute. | operator_kernel.schema_validator | - | config | artifacts | - |
| `src/operator_tests/__init__.py` | Package marker for test suites. | - | - | - | - | - |
| `src/operator_tests/adapter_contract_tests.py` | Strict adapter payload contract/parity tests. | operator_kernel.adapter_contract, operator_kernel.lane_catalog, operator_kernel.schema_validator | operator_notify | config | artifacts | - |
| `src/operator_tests/adapter_parity_probe.py` | Quick parity probe across text/voice/mobile normalization paths. | operator_kernel.adapter_contract, operator_kernel.lane_catalog | adapters.mobile_adapter_stub, adapters.voice_adapter_stub | config | - | - |
| `src/operator_tests/broad_spectrum_tests.py` | Batch1 broad-spectrum deterministic suite. | operator_kernel.schema_validator | operator_runtime.artifact_gc | config | artifacts | - |
| `src/operator_tests/broad_spectrum_tests_batch2.py` | Batch2 OpenClaw-learned/speculative scenario regression suite. | - | operator_runtime.artifact_gc | config | artifacts | - |
| `src/operator_tests/broad_spectrum_tests_batch3.py` | Batch3 failure injection/degraded dependency suite. | - | - | config | artifacts | - |
| `src/operator_tests/broad_spectrum_tests_batch4.py` | Batch4 cost-policy/context-boundary stress suite. | - | operator_runtime.context_loader | config | artifacts | - |
| `src/operator_tests/broad_spectrum_tests_batch5.py` | Batch5 recurrence/lane-onboarding stress suite. | - | - | config | artifacts | - |
| `src/operator_tests/go_no_go_tests.py` | Acceptance gate test suite. | - | - | - | artifacts | - |
| `src/operator_tests/lifecycle_policy_regression_tests.py` | Candidate vs active lifecycle gate behavior tests. | - | - | config | artifacts | - |
| `src/operator_tests/scorecard_regression_tests.py` | Targeted regression suite for scorecard math/gating edge cases. | - | operator_ops.report_scorecard | config | artifacts | - |
| `src/operator_tests/stage3_bootstrap_tests.py` | Bootstrap lane-expansion and stage3 readiness tests. | - | operator_notify | - | artifacts | - |

## Dependency Map
### Import Graph Summary
- Source modules audited: 50
- Circular import cycles detected: 0
- Highest fan-out module: `src/operator_runtime/main.py` (runtime coordinator)
- Orchestrator fan-out: `src/operator_ops/run_stage3_operations.py` and `src/operator_ops/run_stage5_soak_ops.py` (subprocess chaining)

### Primary Internal Dependencies
- `src/operator_runtime/main.py` -> `context_loader`, `artifact_gc`, `execution_lane`, `inspect_engine`, `plan_builder`, `run_index`, `state_writer`, plus kernel `contract_engine`, `lane_catalog`, `schema_validator`.
- `src/operator_ops/run_stage3_operations.py` shells to learning reports/tests/scorecards/soak/readiness/scheduler; also incident opener and notify routes.
- `src/operator_ops/run_stage5_soak_ops.py` shells to evaluator + stage5 metrics + optional stage3 orchestrator.
- `src/operator_ops/evaluate_stage5_lane_drafts.py` depends on runtime `inspect_engine` and `context_loader` (ops invoking runtime behavior).
- `src/operator_tests/scorecard_regression_tests.py` imports private `_compute_scorecard` from ops report module (tight contract-to-implementation coupling).

### Shared-State Coupling Map (Artifact Bus)
- Run log hub: `artifacts/index/run_index.json` written by runtime, read by scorecard/trends/learning/service scorecards/soak/readiness.
- Stage scorecard hub: `artifacts/scorecards/stage_scorecard_latest.json` produced by scorecard report, consumed by stage2 soak and stage3 readiness.
- Service scorecard hub: `artifacts/service_scorecards/service_scorecards_latest.json` consumed by stage2 soak and stage3 readiness.
- Learning hubs: `artifacts/learning/*latest.json` consumed by stage3 readiness, deltas, apply tool, and stage2 soak.
- Stage3 alert/readiness artifacts (`artifacts/stage3/*latest.json`) used for dedupe logic, incident opening, and scheduler/readiness checks.

### Assumption Chains (Implicit Contracts)
- Chain A: runtime `append_run_entry` writes status/verification fields -> `report_scorecard.py` interprets `verification_status` emptiness as gate-blocked semantics.
- Chain B: scorecard/service_scorecard/soak/write latest snapshots -> `report_stage3_readiness.py` assumes latest files exist and represent coherent same-window state.
- Chain C: `inspect_engine.py` writes lane draft artifacts -> `review_stage5_lane_drafts.py` assumes strategy/stamp naming conventions and candidate spec structure.
- Chain D: onboarding runner writes candidate/active lifecycle + executes gates -> service scorecard auto-promotion logic may also mutate lifecycle state.

## Failure Mode Analysis
Coverage note: every file in `src/operator_ops/` and `src/operator_runtime/` is covered below.

| File | External-State Assumptions | Validated | Not Validated / Late Fail | Failure Behavior | Preflight/Guard Pattern |
|---|---|---|---|---|---|
| `src/operator_ops/apply_learning_deltas.py` | learning delta artifact exists; target files exist; guardrails learning policy allows targets | policy/approval token length and allowlist checks | target semantic correctness beyond anchor/string heuristics | blocked/failed status per target, no crash; can produce partial apply | strong policy guard, no environment preflight |
| `src/operator_ops/evaluate_stage5_lane_drafts.py` | context sources load; inspect engine can run for all tasks | preflight_ok for context | task->service inference quality and endpoint realism | skips lane-matched tasks; may emit high-score wrong-service drafts | minimal preflight (context only) |
| `src/operator_ops/generate_runbooks.py` | lane specs and proof artifacts exist and parse | path existence defaults | proof representativeness and stale proof relevance | continues with reduced data; runbook quality degradation | no explicit preflight |
| `src/operator_ops/manage_stage3_incident.py` | stage3 readiness/alert artifacts exist | missing files handled with defaults | incident semantics consistency across producers | registry may drift or carry stale incidents | command-level guard only |
| `src/operator_ops/repair_run_index.py` | run_index format compatible with runtime helper | delegated helper validation | historical semantic correctness | rewrites index deterministically | n/a utility |
| `src/operator_ops/report_archive_activity.py` | archive index exists/parseable | exists + json fallback | archive policy coherence with guardrails | partial metrics if malformed | lightweight guards |
| `src/operator_ops/report_learning_deltas.py` | learning suggestions latest exists and shape valid | json defaults and path checks | quality of generated deltas, stale suggestion relevance | writes low-value draft targets without hard failure | no strict schema validation |
| `src/operator_ops/report_learning_suggestions.py` | run index and lane configs exist | json defaults, optional missing handling | recurrence interpretation correctness; lane probe adequacy assumptions | can emit noisy suggestions | policy loaded but no schema enforce |
| `src/operator_ops/report_runs.py` | run index and archive index exist | graceful defaults | semantic quality of statuses | report may be misleading if upstream semantics drift | none |
| `src/operator_ops/report_scorecard.py` | run index statuses have stable meaning; tests artifacts present | defaults + guarded denominators + no-prod branch | cross-script status semantics and run_type hygiene | gate block/pass may drift if producer status contract changes | math guards but no producer-contract validation |
| `src/operator_ops/report_service_scorecards.py` | lifecycle file and run index status fields aligned | defaults + lifecycle normalization | auto-promotion side effects from reporting context | silent lifecycle mutation during reporting | no explicit mutation preflight |
| `src/operator_ops/report_stage3_readiness.py` | latest artifacts from multiple producers exist and are current | missing files -> unknown/fail patterns | snapshot coherence/time alignment across artifacts | false not_ready from stale/missing latest files | no coherence preflight |
| `src/operator_ops/report_stage3_scheduler_health.py` | systemctl available; timer/service names valid | try/except around command failures | scheduler state meaning across hosts | degraded status if commands unavailable | local command guard only |
| `src/operator_ops/report_stage4_inspect_metrics.py` | inspect artifacts schema is stable | filtering + default parsing | branch/classification correctness from inspect payload evolution | metric skew if payload fields evolve | basic parse guards |
| `src/operator_ops/report_stage5_draft_metrics.py` | draft artifact naming/schema stable | existence and parse fallback | score meaning consistency across strategy versions | can over/under report quality | basic parse guards |
| `src/operator_ops/report_trends.py` | run index semantics stable | defaults + parse guards | trend validity if statuses repurposed | misleading trends rather than crashes | none |
| `src/operator_ops/review_stage5_lane_drafts.py` | draft artifacts shape valid; operator endpoint/service overrides are correct | approval token length, placeholder checks, stamp existence; probe command rewrite warnings | probe expected substrings/semantic validity and endpoint identity quality | bad promotions possible if warnings ignored | moderate input guards; no live reachability test |
| `src/operator_ops/run_lane_onboarding.py` | lane spec exists; runtime propose/execute commands available | action existence; endpoint/probe preflight before lifecycle/run writes | gate command semantics and downstream scorecard side effects | late gate failure after prod execute can still pollute without run_type hygiene | new preflight present, but downstream gate effects remain coupled |
| `src/operator_ops/run_stage2_soak_snapshot.py` | latest scorecards/learning artifacts exist and reflect current window | defaults and command-exit checks | cross-artifact timestamp coherence | blocks exit on stale latest snapshots | partial preflight via command execution |
| `src/operator_ops/run_stage3_operations.py` | all chained scripts available and contracts stable; notify routes available | policy gate for unattended delta apply; per-command exit checks | implicit inter-step data contracts and stale latest reads | fails mid-pipeline; alert dedupe may suppress repeated distinct root causes | limited preflight, mostly fail-late orchestration |
| `src/operator_ops/run_stage5_soak_ops.py` | evaluator/metrics/stage3 commands available; stdout JSON parseable | per-step exit checks | semantic validity of parsed stdout summaries | pass/fail reflects command exits, not output correctness | no preflight beyond command execution |
| `src/operator_runtime/artifact_gc.py` | guardrails artifact policy valid; archive index writable | schema validator + path guards | policy intent correctness across producers | over/under-retention possible | strong internal guards |
| `src/operator_runtime/context_loader.py` | context_sources.json format valid and file paths resolvable | required flags and load status | semantic freshness of context documents | preflight fail blocks runtime | explicit preflight module |
| `src/operator_runtime/execution_lane.py` | docker/curl available; service/container names map to endpoint; lane spec verify probes valid | lane spec load error blocked; https warmup `-k` handling; probe required tracking | service identity mismatch between endpoint and container; probe expected_substring correctness | verification failed while commands still execute; possible misleading recommendations | no independent preflight, executes then verifies |
| `src/operator_runtime/inspect_engine.py` | context rows loaded; lane schema exists; lane specs parseable; pricing config optional | schema validation for draft specs; guardrails policy load defaults | service inference correctness, endpoint/probe realism, recommendation eligibility quality | high-score but wrong draft can be emitted | no live endpoint preflight by design (inspect mode) |
| `src/operator_runtime/main.py` | all configs exist/valid; kernel/runtime helpers remain compatible; downstream write paths writable | runtime schema validation + context preflight + mode/approval/risk guards | cross-module status semantics consistency, execute_media_lane hardcoding despite dispatcher function | can return early with blocked status; heavy side-effect fanout in execute/propose | strong gate checks but high responsibility concentration |
| `src/operator_runtime/observation_engine.py` | signature file shape stable | defaults | quality of generated recommendations | advisory-only degradation | none |
| `src/operator_runtime/plan_builder.py` | contract_engine plan schema stable | delegates to kernel | domain-specific steps remain valid | plan drift risk | none |
| `src/operator_runtime/run_index.py` | run index schema stable and writable | schema validation + cap | producer-provided status values semantically valid | downstream reports inherit any bad status semantics | write-time validation only |
| `src/operator_runtime/state_writer.py` | state target config valid and writable | schema validation and target allowlist | target consumers expecting stronger invariants | state updates can silently skip failed targets | guarded writes but no external ack |

## Redundancy and Dead Code
### Duplicate Logic
- JSON load/write helpers are reimplemented across most ops scripts (`_load_json`, `_write_artifacts`, `_utc_now_iso`).
- Guardrails section parsing patterns are duplicated (`report_scorecard.py`, `report_service_scorecards.py`, `run_stage2_soak_snapshot.py`, `run_stage3_operations.py`, `report_learning_suggestions.py`).
- Repeated subprocess wrapper patterns in orchestrators (`_run` implementations in stage2/stage3/stage5/onboarding).
- Repeated dashboard export write pattern to `/opt/stack/services/apps/dashboard/status.json` in multiple reporters.

### Dead or Vestigial Candidates
- `src/operator_runtime/execution_lane.py`: dispatcher `execute_lane()` exists but runtime calls `execute_media_lane()` directly (`src/operator_runtime/main.py:654`), reducing value of dispatcher abstraction.
- Dual adapter paths (`src/adapters/*` shims and `src/operator_runtime/adapters/*`) appear redundant; top-level package is compatibility forwarding only.
- Stage-specific scripts from prior gates remain active but loosely coupled; several produce artifacts consumed by nothing except dashboards/rollups, increasing maintenance surface.

### Over-Instrumentation Signals
- Many tools emit both timestamped snapshot and `latest` copy for each run, multiplying state surfaces and stale-read risk.
- Readiness and soak rely on many latest artifacts from independent jobs; this creates brittle pass/fail based on freshness rather than semantic health.
- Notification milestones are emitted at many command boundaries; useful operationally but amplifies dedupe/alert-routing complexity.

## Architectural Assessment
### 1) operator_ops Ownership Model
Yes. Current `operator_ops` acts as both orchestration control-plane and analytics/reporting layer. It should explicitly own: orchestration entrypoints, reporting generation, lifecycle administration, and alert routing. It should not own: kernel contract semantics, schema definitions for core runtime payloads, or mutable runtime state semantics without validated interfaces.

### 2) Natural Sub-Boundaries Inside operator_ops
- `ops.orchestrators`: `run_stage2_soak_snapshot.py`, `run_stage3_operations.py`, `run_stage5_soak_ops.py`, `run_lane_onboarding.py`
- `ops.reports`: `report_*` family (scorecard/service/trends/readiness/metrics/archive/runs)
- `ops.lifecycle`: `review_stage5_lane_drafts.py`, `manage_stage3_incident.py`, lifecycle promotion logic from service scorecards
- `ops.learning`: `report_learning_*`, `apply_learning_deltas.py`
- `ops.maintenance`: `repair_run_index.py`, `generate_runbooks.py`

### 3) Missing Validation Layer
Yes. Kernel has explicit schema validation primitives; ops layer mostly relies on ad-hoc `_load_json(..., default)` and implicit field interpretation. Shared artifacts (`run_index`, scorecards, stage snapshots, learning outputs, draft artifacts) need explicit versioned schemas and cross-tool validators to prevent semantic drift.

### 4) Right Preflight Pattern
Ad-hoc preflight has emerged in isolated scripts (runtime context preflight, onboarding endpoint/probe preflight). A common preflight contract should be standardized: input schema validation, required-file existence/freshness checks, external dependency checks (docker/curl/systemctl where used), and side-effect gating before lifecycle/run-index mutation.

### 5) Revoke/Consolidate Candidates
- Candidate revoke: duplicate adapter shim namespace (`src/adapters/*`) once compatibility requirement is retired.
- Candidate consolidate: repeated JSON/path/time/subprocess helpers across ops scripts into shared `operator_ops.common` module.
- Candidate refactor: `run_stage3_operations.py` command chain and alert logic into composable pipeline steps with typed results.
- Candidate harden: all latest-artifact consumers should check artifact age/window coherence before gate decisions.

## Recommendations
Priority order: P0 highest.

| Priority | Classification | Target | Recommendation | Why |
|---|---|---|---|---|
| P0 | Harden | Artifact contract layer | Add versioned schemas + validator entrypoints for `run_index`, scorecards, stage snapshots, stage5 drafts, learning artifacts. Enforce read-time validation in ops reporters/orchestrators. | Current implicit contracts are the main coupling mechanism and recurrent failure source. |
| P0 | Refactor | `src/operator_ops/run_stage3_operations.py` | Split into: command planner, command runner, result evaluator, notifier/alert dedupe, artifact writer. Keep CLI surface, reduce responsibility concentration. | Single script currently mixes orchestration, policy, alerting, dedupe, artifact writes, and incident side effects. |
| P0 | Harden | `src/operator_ops/run_lane_onboarding.py` + gate chain | Add shared preflight suite (endpoint/probes/schema/freshness) and explicit rollback/cleanup semantics for partial runs. | Onboarding still couples prod execute with immediate gate outcomes and can create polluted state if assumptions fail mid-chain. |
| P1 | Consolidate | Shared ops utilities | Consolidate `_load_json`, `_write_artifacts`, `_utc_now_iso`, subprocess wrappers, dashboard export helpers. | Duplicate helper logic creates drift and inconsistent failure handling. |
| P1 | Refactor | `src/operator_runtime/main.py` | Separate CLI contract handling from mode handlers (`inspect`, `propose`, `execute`) and from side-effect integrations (notify/run_index/gc/state_writer). | 744-line coordinator is a high-change hotspot and fault amplifier. |
| P1 | Harden | Latest-artifact consumers (`report_stage3_readiness.py`, `run_stage2_soak_snapshot.py`) | Enforce timestamp coherence checks across all required latest artifacts before gate decisions. | Prevent false negatives caused by stale/misaligned snapshots. |
| P2 | Consolidate | Lifecycle mutation ownership | Move lifecycle mutation out of reporting path (`report_service_scorecards.py` auto-promotion) into explicit lifecycle tool. | Reporting side effects obscure causality and violate single-responsibility. |
| P2 | Revoke | `src/adapters/*` compatibility shims | Remove once imports are migrated to `operator_runtime.adapters.*`. | Reduces namespace duplication and adapter boundary ambiguity. |
| P2 | Keep as-is | Kernel boundary integration | Keep kernel import usage pattern (contract/schema/lane resolution in kernel; execution/orchestration outside). | Boundary discipline is working and not source of recent incidents. |

## Raw Evidence
- Scorecard gate-blocked denominator exclusion and no-prod proxy logic: `src/operator_ops/report_scorecard.py:157`, `src/operator_ops/report_scorecard.py:168`, `src/operator_ops/report_scorecard.py:221`, `src/operator_ops/report_scorecard.py:107`.
- Onboarding preflight added before lifecycle/run writes: `src/operator_ops/run_lane_onboarding.py:70`, `src/operator_ops/run_lane_onboarding.py:177`, `src/operator_ops/run_lane_onboarding.py:192`, `src/operator_ops/run_lane_onboarding.py:211`.
- Stage5 endpoint propagation into probe commands with warnings/dry-run visibility: `src/operator_ops/review_stage5_lane_drafts.py:363`, `src/operator_ops/review_stage5_lane_drafts.py:403`, `src/operator_ops/review_stage5_lane_drafts.py:498`, `src/operator_ops/review_stage5_lane_drafts.py:512`.
- HTTPS warmup probe handling in execution lane: `src/operator_runtime/execution_lane.py:93`, `src/operator_runtime/execution_lane.py:109`, `src/operator_runtime/execution_lane.py:131`, `src/operator_runtime/execution_lane.py:194`.
- Stage3 orchestrator command-chain coupling: `src/operator_ops/run_stage3_operations.py:181`, `src/operator_ops/run_stage3_operations.py:206`, `src/operator_ops/run_stage3_operations.py:212`, `src/operator_ops/run_stage3_operations.py:215`, `src/operator_ops/run_stage3_operations.py:222`.
- Stage5 soak orchestrator transitive dependency on stage3 orchestrator: `src/operator_ops/run_stage5_soak_ops.py:182`, `src/operator_ops/run_stage5_soak_ops.py:191`, `src/operator_ops/run_stage5_soak_ops.py:202`.
- Runtime coordinator concentration (multi-module fan-out + side effects): `src/operator_runtime/main.py:7`, `src/operator_runtime/main.py:239`, `src/operator_runtime/main.py:297`, `src/operator_runtime/main.py:511`, `src/operator_runtime/main.py:654`, `src/operator_runtime/main.py:664`.
- Readiness relies on latest artifacts from multiple producers: `src/operator_ops/report_stage3_readiness.py:38`, `src/operator_ops/report_stage3_readiness.py:39`, `src/operator_ops/report_stage3_readiness.py:40`, `src/operator_ops/report_stage3_readiness.py:41`, `src/operator_ops/report_stage3_readiness.py:47`.
- Guardrails policy hub sections consumed across ops: `config/guardrails.json` (`scorecard`, `service_scorecards`, `stage2`, `stage3_operations`, `stage5_drafts`, `learning`).
- Run-index shared-state bus evidence: `artifacts/index/run_index.json` contains mixed run types, statuses, and verification fields consumed by many reports.
