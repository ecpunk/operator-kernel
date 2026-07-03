# Compounding Architecture Method

Status: Active
Date: 2026-03-07
Scope: Operator Kernel development workflow

Purpose
This method keeps development progress compounding instead of resetting between sessions. Each change should increase future safety, speed, and clarity.

Core pattern
1. Build a bounded capability.
2. Add policy guardrails around it.
3. Add structured artifacts and schemas.
4. Add deterministic tests that include failure cases.
5. Record evidence and update roadmap state.
6. Use findings to choose the next smallest high-leverage improvement.

What compounding means in practice
- No one-off changes without policy and test coverage.
- Every new feature should make the next feature easier to build safely.
- Every incident or test finding becomes reusable signal in artifacts, rules, or docs.
- Work is only "done" when behavior is observable and repeatable.

Execution loop
- Implement small scoped change.
- Run focused test(s).
- Fix regressions immediately.
- Run broader suite.
- Record report artifact path.
- Update roadmap/testing plan deltas.

Required outputs for each iteration
- code/config change
- validation output
- machine-readable report artifact when applicable
- documentation delta in roadmap/testing plan/readme

Do not skip
- fail-closed behavior checks
- rollback and recovery verification
- schema validation on structured outputs
- guardrail checks for destructive or high-risk paths

Decision filter for next work item
Pick the next task that most improves one or more of:
- safety
- determinism
- observability
- operator trust
- cost predictability

Anti-patterns to avoid
- broad scope jumps without test closure
- hidden behavior changes without artifact updates
- adding lanes/features before guardrails and evidence are stable
- ad-hoc fixes that do not feed policy or test harness

Current evidence of compounding progression
- risk auto-gates
- state writer with trusted target allowlist
- archive soft deprecation lifecycle
- go/no-go deterministic suite
- broad-spectrum stress batches 1 through 4

Registration points
- local methodology source: `projects/operator-kernel/COMPOUNDING_ARCHITECTURE_METHOD.md`
- stage execution source: `projects/operator-kernel/ROADMAP.md`
- testing evidence source: `projects/operator-kernel/TESTING_PHASE_PLAN.md`
