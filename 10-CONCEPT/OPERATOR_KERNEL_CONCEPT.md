# Operator Kernel Concept

Status: Draft v1
Date: 2026-03-07
Project Path: `/opt/stack/services/projects/operator-kernel`

Execution roadmap and stage tracking: `ROADMAP.md`

Engine context: this kernel is the rules and contract primitive for The Engine — a modular application platform. See `/mnt/gdrive/Jon's Projects/Projects/The Engine/10-CONCEPT/THE_ENGINE_SPEC.md`.

## 1. Purpose

Build a deterministic operator copilot for local and self-hosted operations that is fast during incidents, low-cost to run, and always human-controlled for mutating actions.

This is not an autonomous agent platform. This is a thin execution kernel with strict contracts.

## 2. Why This Exists

This project came directly out of a failed attempt to train a broad autonomous agent platform (OpenClaw) into behaving like something more constrained and trustworthy.

OpenClaw worked in narrow ways. When a Plex incident happened, walking through diagnosis, proposing fixes, executing with verification — that loop was fast and genuinely useful. That was the behavior worth keeping.

But the platform itself was a poor fit for how operations actually need to work:

- Broad surface area meant unpredictable defaults and behavior that was never asked for.
- Costs were hard to predict or control.
- Hidden control planes (hooks, agents, skills, plugins) created drift and trust erosion.
- Training was fighting the platform rather than shaping it.

The training work was an attempt to constrain OpenClaw into the assistant that was actually wanted: one that would think with you about your systems rather than act on them unsupervised. When it became clear the training would never resolve the underlying shape mismatch, the right move was to build the thing from scratch.

Operator Kernel is that thing.

Every design decision in this project is a direct response to something that created anxiety or broke trust with the previous tool. No hidden autonomy, explicit approval gates, proof packs, bounded lanes, cost controls — none of these are abstract best practices. They are specific answers to specific failures.

The incident response use case was the starting point because it was the clearest validated win. But the original intent was always broader: an assistant you could think with about your systems, ask planning questions, evaluate gaps, and explore changes — with a firm rule that nothing happens on your infrastructure without your explicit say-so.

The intended product is the behavior seen in successful incident handling (for example Plex triage/fix), generalized:

- fast evaluation against real system context
- bounded, predictable action set
- explicit verification and proof
- human decision authority at every mutation point

## 3. Product Definition

Operator Kernel is a contract-driven control layer with three modes only:

1. Inspect (read-only)
2. Propose (plan only)
3. Execute (mutating, approval required)

Every mutating action must produce a proof pack.

## 4. Non-Goals (V1)

- No always-on autonomous loops that mutate production state.
- No plugin marketplace model.
- No hidden model retries with implicit provider escalation.
- No broad cross-service orchestration without explicit task contracts.
- No dependency on a single heavy agent runtime.

## 5. Design Principles

- Human is the decider for mutation.
- Read-only by default.
- Deterministic command paths over model improvisation.
- Explicit rollback and post-change verification.
- Cost predictability as a first-class requirement.
- Input modality independence: voice and keyboard map to the same contract.
- Portability by design: every decision should avoid creating unnecessary coupling to a specific environment, service set, or deployment context. The goal is not to build portability features now, but to never make decisions that would require reworking the foundation to achieve it later.

## 6. Measurable Guardrails (Hard Requirement)

Before any expansion beyond V1, all of the following must be enforced and measured:

1. Mode Enforcement
- 100% of actions are tagged `inspect`, `propose`, or `execute`.
- 0 untagged actions are allowed.

2. Mutation Approval Gate
- 100% of `execute` actions require an explicit approval token.
- 0 mutation commands run without approval token.

3. Proof Pack Completeness
- 100% of `execute` actions emit:
  - precheck output
  - command transcript
  - post-verify output
  - rollback command block

4. Cost Budget Controls
- Per-request token cap.
- Daily budget cap with fail-closed behavior.
- Provider routing policy with no silent premium fallback.

5. Context Boundaries
- Context hydration uses ordered trusted sources only.
- Max context payload size enforced.
- Context truncation is explicit and reported.

6. Blast Radius Constraints
- Execute lane command allowlist for V1 workflows.
- Blocklist for destructive patterns unless explicitly approved and whitelisted.

## 7. Capability Model

### 7.1 Inspect Mode

Intent: triage and diagnosis only.

Allowed:
- read files
- collect logs
- check service status
- run non-mutating probes

Output:
- ranked hypotheses
- supporting evidence
- confidence statement

### 7.2 Propose Mode

Intent: generate a deterministic action plan before any mutation.

Required output:
- exact commands
- expected impact
- risk level
- rollback steps
- verification checklist

No mutating command execution in this mode.

### 7.3 Execute Mode

Intent: run approved mutating plan safely.

Required inputs:
- approved plan id
- approval token
- target scope

Required behavior:
- run precheck
- run commands
- run verification
- output proof pack
- emit rollback path if verification fails

## 8. High-Level Architecture

1. Ingress Layer
- terminal input
- mobile/voice input adapters
- normalize to one task contract

2. Policy Layer
- mode validator
- approval gate
- command allowlist/blocklist
- budget policy

3. Context Layer
- load ordered trusted files
- hash and timestamp artifacts
- bounded context assembly

4. Planning Layer
- deterministic plan template generation
- no execution side effects

5. Execution Layer
- host script runner and command runner
- strict logging and timeout controls

6. Evidence Layer
- proof pack builder
- machine-readable run record
- operator-readable summary

## 9. Reuse From Current Work

Keep and reuse:

- Existing source-of-truth discipline and docs.
- Existing operational scripts under `automations/scripts`.
- Existing dashboard/status artifacts where useful.
- Existing Operator Kernel seed:
  - `src/operator_runtime/context_loader.py`
  - `src/operator_runtime/main.py`
  - `config/context_sources.json`
- Existing contract and scorecard lessons from training.

Do not carry forward as runtime dependency:

- OpenClaw as a production execution platform for this phase.

## 10. V1 Scope

V1 includes:

1. Contract Engine
- parse mode
- enforce mode and policy

2. Context Hydration
- strict source order
- bounded load
- hydration report

3. Plan/Execute Framework
- propose contract output template
- execute path with approval token

4. Proof Pack
- local artifact with precheck, commands, verify, rollback, status

5. One End-to-End Incident Workflow
- start with media/service incident lane (Plex-like troubleshooting flow)

V1 excludes:

- broad multi-service autonomous orchestration
- plugin ecosystem
- unsupervised scheduling of mutating actions

## 11. Acceptance Tests (Go/No-Go)

1. Mode Safety Test
- attempt mutation in inspect mode
- expected result: blocked

2. Approval Enforcement Test
- attempt execute without approval token
- expected result: blocked

3. Proof Pack Test
- run approved execute flow
- expected result: complete proof pack artifact created

4. Cost Guardrail Test
- force budget threshold
- expected result: request denied with clear budget message

5. Incident Speed Test
- run standard incident scenario from alert to verified fix
- success metric: cycle time within defined SLA target

6. Modality Parity Test
- run same task via keyboard and voice adapter
- expected result: same mode classification and same plan semantics

## 12. Initial Metrics

- Mean time to triage (MTTT)
- Mean time to verified fix (MTTVF)
- Percentage of execute actions with complete proof packs
- Mutation-without-approval incidents (target: 0)
- Daily model spend versus budget
- Repeated incident recurrence rate

## 13. Risks and Mitigations

Risk: mode drift into hidden autonomy.
Mitigation: strict mode gate plus audit logs.

Risk: cost creep from context bloat or provider fallback.
Mitigation: hard caps, bounded context, explicit provider policy.

Risk: operational trust erosion from opaque outputs.
Mitigation: mandatory proof packs and deterministic templates.

Risk: voice ingress causes ambiguity.
Mitigation: normalize voice to structured contract before planning.

## 14. Build Sequence

Phase 0: Finalize contract schema and guardrail policy.
Phase 1: Implement inspect/propose/execute enforcement and proof pack output.
Phase 2: Wire one production incident lane and validate with scorecard.
Phase 3: Add voice/mobile ingress adapters with modality parity tests.

## 15. Decision Record

Decision: prioritize deterministic operator kernel over autonomous agent runtime.
Reason: best fit for speed, control, safety, and cost predictability in current phase.
Revisit Trigger: reconsider broader autonomous runtime only after V1 guardrails are stable and KPIs are consistently met.

## 16. Deferred Learning Loop (Post-V1)

The kernel should learn from mistakes in a controlled, review-first way:

- Capture recurring failure signatures from proof packs.
- Generate recommended doc/spec updates.
- Require explicit operator approval before any persistent policy/doc change.

This is deferred until core V1 lanes and guardrail metrics are stable.

## 17. Kernel Boundary

### What Is the Kernel

The kernel is a small, stable set of primitives that define contracts, enforce rules, and resolve intent. It does not execute system commands, manage state, or make recommendations. It has no always-on process. It has no infrastructure footprint.

The kernel is currently four modules:

- `adapter_contract` — normalizes voice, mobile, and future adapter payloads to a shared contract shape
- `lane_catalog` — resolves a task utterance to an action ID and loads the lane spec
- `contract_engine` — loads guardrails, validates mode and channel, classifies risk, evaluates execution controls, and manages the plan lifecycle contract
- `schema_validator` — validates any data payload against a JSON schema

These modules have stable, settled interfaces. The kernel should rarely change its rules. When rules change, that is a version bump, not a routine update.

### What Is Not the Kernel

Code that uses kernel primitives to do work is not the kernel. Two current examples:

- `execution_lane` — calls `lane_catalog` and `subprocess`. It is an operator that speaks the kernel's contract. It lives outside the kernel boundary.
- `observation_engine` — pattern-matches observations and generates recommendations. It consumes kernel-adjacent data but has no kernel primitive role. It lives outside the kernel boundary.

Voice and mobile adapter stubs are also outside the kernel boundary. They are examples of modules that implement the adapter contract — they speak the kernel's language but are not part of it. Future input modalities follow the same pattern.

### Why This Boundary Matters

Keeping the boundary explicit does two things. First, it keeps the kernel stable. If execution logic, recommendation logic, or adapter logic lived inside the kernel, every new use case would create pressure to change the kernel's rules. With the boundary enforced, those things grow freely in their own modules without touching the kernel. Second, it enables the Engine model: the kernel is a library that any project in the Engine namespace can import and build on, while each project manages its own operational concerns.

### Plan Contract vs Plan Assembly

`contract_engine` currently contains both kernel concerns and one application concern. The distinction:

Kernel owns:
- `PlanRecord` dataclass — defines what a plan must contain
- `load_guardrails`, `validate_mode_and_channel`, `classify_risk`, `evaluate_execute_risk_controls`, `verify_approval` — rule enforcement
- `write_proof_pack` — enforces the proof contract
- `load_plan`, `save_plan` — reference persistence contract (thin enough to belong in the kernel as the canonical plan I/O shape)

Application owns:
- `build_media_incident_plan` — assembles a plan for a specific domain (media operations). The word "media" in a kernel function is the tell. The kernel defines what a plan looks like. The application builds one.

At packaging time, `build_media_incident_plan` moves out of `contract_engine` into the application layer (`main.py` or a domain-specific plan builder). The kernel exports `PlanRecord` and the caller populates it.

This keeps the kernel domain-agnostic. Any application in the Engine namespace can build a plan against the kernel's contract without importing media-specific logic.

### Packaging Direction

The kernel will be packaged as a proper Python package (`operator-kernel`) at Stage 3 closeout. The interface is settled. Stage 3 graduation is the event that justifies a version number. At v0.1.0:

- The kernel becomes importable as a package
- `build_media_incident_plan` is extracted from `contract_engine` into the application layer
- Voice, mobile, and future adapter modules will declare a kernel dependency
- Operational tooling (execution, observation, reporting, stage runners) will be explicitly separated from the kernel source tree

The always-on component of the Engine (whatever service wraps the kernel for runtime use) will import the kernel as a dependency, not contain it.

---

## 18. The Engine Relationship

### What The Engine Is

The Engine is a modular application platform. Capabilities are built once as modules and assembled into products via a manifest. The operator kernel is the rules and contract primitive that Engine modules dealing with operations, intent resolution, or gated execution will import. The kernel does not become The Engine. The Engine assembles the kernel alongside other modules into products.

### Autonomy and Rules Are Distinct Modules

Autonomy and rules are separate concerns. The kernel owns rules: what is allowed, what shape is required, what gate must be passed. Autonomy lives in a separate module with its own trust tier and development lifecycle. That autonomy module calls kernel primitives when it needs to gate a decision — it does not modify them. This keeps autonomy development free from kernel stability requirements, and keeps kernel rules independent of how autonomous any consumer wants to be.

### Engine-Core (Dev Name)

The always-on component that wraps the kernel for runtime use is tracked under the working name `engine-core`. It is the first Engine service — the thing that imports the kernel as a package, maintains a runtime process, and exposes kernel capabilities to other consumers. It is not part of the kernel. It is not yet scoped or prioritized. The name exists to anchor the concept without forcing premature design decisions. Revisit after kernel v0.1.0 is packaged.

---

## Suggested Recurring Incident Patterns (Review Draft)

These patterns were extracted from recent operator runs and are proposed for documentation review only.

- Trigger pattern: `Fix Plex after recent restart` (22 recent occurrences)
  - Suggested lane mapping check: `plex_safe_recovery_v1`
  - Suggested runbook reference: `artifacts/runbooks/runbook_plex_safe_recovery_v1.md`
- Trigger pattern: `Sonarr not responding on web` (21 recent occurrences)
  - Suggested lane mapping check: `sonarr_safe_recovery_v1`
  - Suggested runbook reference: `artifacts/runbooks/runbook_sonarr_safe_recovery_v1.md`
- Trigger pattern: `clawplexalt web ui unavailable` (6 recent occurrences)
  - Suggested lane mapping check: `plex_safe_recovery_v1`
  - Suggested runbook reference: `artifacts/runbooks/runbook_plex_safe_recovery_v1.md`
- Trigger pattern: `Home Assistant dashboard unavailable` (4 recent occurrences)
  - Suggested lane mapping check: `homeassistant_safe_recovery_v1`
  - Suggested runbook reference: `artifacts/runbooks/runbook_homeassistant_safe_recovery_v1.md`
- Trigger pattern: `Radarr web UI unavailable after update` (3 recent occurrences)
  - Suggested lane mapping check: `radarr_safe_recovery_v1`
  - Suggested runbook reference: `artifacts/runbooks/runbook_radarr_safe_recovery_v1.md`
