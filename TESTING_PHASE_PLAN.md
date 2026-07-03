# Operator Kernel Testing Phase Plan

Status date: 2026-03-07

This file defines the next structured testing pause, with a broad test spectrum based on current kernel behavior, OpenClaw lessons, and speculative future scenarios.

## Next Testing Phase Timing

The next testing pause starts immediately after the deterministic go/no-go test script is implemented.

Current state:
- deterministic go/no-go script is implemented at `src/operator_tests/go_no_go_tests.py`.
- first run completed with full pass (6/6) and report in `artifacts/tests/`.
- broad-spectrum batch 1 completed with full pass (8/8) via `src/operator_tests/broad_spectrum_tests.py`.
- latest report: `artifacts/tests/broad_spectrum_batch1_20260307T043433Z.json`.
- broad-spectrum batch 2 completed with full pass (7/7) via `src/operator_tests/broad_spectrum_tests_batch2.py`.
- latest report: `artifacts/tests/broad_spectrum_batch2_20260307T043837Z.json`.
- broad-spectrum batch 3 completed with full pass (5/5) via `src/operator_tests/broad_spectrum_tests_batch3.py`.
- latest report: `artifacts/tests/broad_spectrum_batch3_20260307T044427Z.json`.
- broad-spectrum batch 4 completed with full pass (6/6) via `src/operator_tests/broad_spectrum_tests_batch4.py`.
- latest report: `artifacts/tests/broad_spectrum_batch4_20260307T044630Z.json`.
- broad-spectrum batch 5 completed with full pass (4/4) via `src/operator_tests/broad_spectrum_tests_batch5.py`.
- latest report: `artifacts/tests/broad_spectrum_batch5_20260307T045212Z.json`.
- doc-delta simulation artifact: `artifacts/tests/doc_delta_suggestions_batch5_20260307T045211Z.json`.
- notable finding: lane keyword mapping is substring-first, so onboarding keywords should avoid overlap with existing keywords.
- stage scorecard loop is live via `src/operator_ops/report_scorecard.py` with dashboard export.
- latest scorecard: `artifacts/scorecards/stage_scorecard_20260307T050703Z.json`.
- notable finding: run index currently contains duplicate run ids and should be hardened before stage close.
- productive change recorded: confidence score increased from 82.88 to 96.38 after scorecard metric hardening that separated operational (`prod`) runs from synthetic (`test`) runs.

Trigger to start testing pause:
- Stage 1 task "deterministic go/no-go test script" is complete.

Duration target:
- one focused test cycle (same day or next available window), then review findings before more feature expansion.

## Testing Objectives For This Pause

1. Validate all concept acceptance tests with deterministic pass/fail output.
2. Stress the policy surface (mode gate, approval gate, risk gate, plan age gate, apply gate).
3. Validate evidence integrity (proof packs, run index, archive index, state writer outputs).
4. Validate behavior under realistic incident use cases and malformed requests.
5. Capture follow-on lane and policy backlog items from observed friction.

## Broad Spectrum Test Matrix

### A. Contract and Guardrail Tests

- inspect mutation attempt must block.
- execute without plan id must block.
- execute without approval token must block.
- execute with mismatched approval token must block.
- execute with stale plan timestamp beyond policy must block.
- apply on blocked risk tier must block.
- apply without action_id when required must block.

### B. Artifact Integrity and Lifecycle Tests

- propose writes valid plan matching plan schema.
- execute writes valid proof matching proof schema.
- run index appends correctly and respects max entries.
- soft deprecation archives age/count candidates and writes archive index.
- archive purge transitions archived records to purged when retention exceeded.

### C. State Writer and Canonical Output Tests

- execute writes operator state artifact with valid schema.
- execute updates dashboard status only in operator_kernel section.
- state writer honors target allowlist and refuses untrusted targets.
- summary and recommendation text are bounded and sanitized.

### D. Lane and Incident Workflow Tests

- plex safe recovery plan and execute path complete with verification.
- sonarr no-indexer scenario produces expected recommendation ranking.
- dry-run execute path performs no mutating action but still produces proof.

### E. Regression and Drift Tests

- schema validation fails closed when config keys are missing.
- malformed lane spec fails before execution.
- unknown lane keyword results in no action_id and safe behavior.
- high-volume propose/execute history preserves index cap behavior.

### F. OpenClaw-Learned Use Cases (Ported To Kernel)

- source-of-truth mismatch handling under context hydration limits.
- provider budget exhaustion handling remains explicit and non-silent.
- fallback ambiguity is surfaced as explicit status, not hidden behavior.
- localhost/service reachability issues map to deterministic triage steps.

### G. Speculative Use Cases (Future-Ready)

- multi-service incident where two lanes are suggested but only one is executed.
- repeated incident recurrence triggers recommendation for doc/spec update.
- voice phrasing ambiguity still resolves to same contract as keyboard request.
- operator requests unsafe destructive action and receives policy-first refusal.

## Output Artifacts Required Per Test Cycle

- test run log with pass/fail per case.
- failures with short root-cause and fix proposal.
- updated roadmap deltas for accepted follow-up work.

## Exit Criteria For This Testing Pause

- all Stage 1 acceptance tests pass or have explicit accepted exceptions.
- no mutation-without-approval paths observed.
- no schema-invalid artifact writes observed.
- no untrusted target writes observed by state writer.

## Stage 2 Soak Evidence Cadence

- Stage 2 close requires a short soak evidence trail, not a single snapshot.
- Generate soak snapshots with `src/operator_ops/run_stage2_soak_snapshot.py`.
- Minimum evidence target: 3 snapshots within 24 hours.
- Each soak snapshot should show stage gate pass, service gate pass, and migration-policy validation pass.
