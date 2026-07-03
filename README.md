# Operator Kernel

Operator Kernel is a deterministic runtime for infrastructure operations
carried out with AI in the loop, built on one rule: nothing mutates a system
without explicit approval. It observes before it plans, plans only from a
bounded set of pre-authored actions, executes only against a tokenized
approval, and writes a verifiable evidence trail for every run.

Think of it as a change-control process compressed into a runtime. The change
request is generated from observed state rather than from memory, the
approval is an explicit token rather than a nod, the execution is limited to
runbooks that were reviewed before they were ever needed, and the post-change
report writes itself. A pilot's checklist bolted to a flight recorder.

## Where it came from

This kernel was built as the deliberate replacement for a broad autonomous
agent platform — one that worked in narrow ways but produced unpredictable
behavior, hidden costs, and eroded trust. Every design decision here is a
specific answer to something that went wrong there: no hidden autonomy,
explicit approval gates, bounded action sets, full proof output on every
execution.

It should be read as a working reference implementation of that governance
contract. In the environment it came from, the contract outlived the
process boundary: the live agent surfaces there now enforce the same
concepts — observe first, approve explicitly, log proof — as native parts of
their own runtime. This repository is the complete, runnable articulation of
the pattern they follow.

The kernel assumes one thing it does not provide: a canonical source of
truth to hydrate context from. The control-plane side of that pattern is
published separately as
[atlas-framework](https://github.com/ecpunk/atlas-framework).

## The contract

Every task moves through the same six steps, and the mode system makes it
impossible to skip ahead:

1. **Inspect.** Hydrate trusted context in a strict, configured order
   (`config/context_sources.json`) and produce ranked hypotheses with cited
   evidence — before any plan exists.
2. **Propose.** Generate a bounded, deterministic plan from the lane catalog,
   optionally enriched by a safe live-observation pass. The plan is an
   artifact, not an intention.
3. **Approve.** Execution requires the plan id and an approval token issued
   with the proposal. No token, no run.
4. **Execute.** Dry-run by default; `--apply` is a separate, deliberate step.
   Only allowlisted actions from the approved plan can run.
5. **Verify.** Lane-defined probes check that the system actually reached the
   intended state; required-probe failures fail the run.
6. **Proof.** Every run emits an evidence pack — facts observed, commands
   executed, verification results — to `artifacts/`, indexed and
   retention-managed by policy.

## Lanes: pre-authored, reviewable actions

The kernel never invents a remediation. Actions live as **lane specs**
(`config/lanes/*.json`) — schema-validated JSON runbooks declaring the steps,
the verification probes, and the rollback path for one well-understood
operation (a safe service recovery, for example). A quality gate
(`validate_lane.py`) enforces schema validity, non-empty verify and rollback
arrays, no mutating commands in probes, and catalog/lifecycle registration
before a lane can be onboarded at all.

This is the trade the kernel makes on purpose: it will never surprise you
with creativity, because the full action surface was reviewed before it was
ever needed.

## Guardrails

Policy lives in `config/guardrails.json` and is fail-closed throughout:
per-run LLM cost ceilings with provider pricing tables, confidence thresholds
for recommendation-driven plan changes, artifact retention and archival,
and gate enforcement that exits non-zero when readiness is not green. The
`src/operator_ops/` layer adds the operational reporting built around the
kernel — scorecards, trend reports, readiness rollups, incident lifecycle —
and `src/operator_tests/` carries the acceptance and failure-injection
suites.

## Try it

```bash
git clone <this repo>
cd operator-kernel

# Inspect: hydrate context, rank hypotheses, write an inspect artifact
PYTHONPATH=src python3 src/operator_runtime/main.py \
  --mode inspect --channel text --task "Plex playback failing on LAN"

# Propose: generate a bounded plan artifact (includes the approval token)
PYTHONPATH=src python3 src/operator_runtime/main.py \
  --mode propose --channel text --task "Fix Plex after recent restart"

# Execute: dry-run against the approved plan; add --apply to run for real
PYTHONPATH=src python3 src/operator_runtime/main.py \
  --mode execute --task "Fix Plex after recent restart" \
  --plan-id <plan_id> --approval-token <token>

# Validate every lane spec against the authoring quality gate
PYTHONPATH=src python3 src/operator_ops/validate_lane.py --all
```

## Repository layout

- `src/operator_kernel/` — kernel primitives: contract, schema validation,
  lane resolution, risk controls
- `src/operator_runtime/` — the inspect/propose/execute flow
- `src/operator_ops/` — orchestration, reporting, incident tooling
- `src/operator_tests/` — acceptance, regression, and failure-injection suites
- `config/` — lanes, catalogs, guardrails, schemas, pricing, notification routes
- `systemd/` — unit and timer assets for scheduled operation
- `docs/` — end-user guide and quickstart
- `artifacts/` — plans, proofs, reports (generated at runtime; not committed)

## Configuration and secrets

Nothing sensitive is committed. Notification webhooks are routed through
environment variables (`config/notify.json` names the variable, e.g.
`DISCORD_WEBHOOK_URL`; the URL itself never appears in config). Endpoints and
context-source paths ship as placeholders — point them at your own
environment before first run.

## Status

The core contract — inspect, propose, approve, execute, verify, proof — is
stable and runnable end to end. The surrounding ops and reporting layer is
extensive and reflects a period of rapid iteration; treat it as a toolbox
rather than a required surface. Active feature development is paused by
design: the kernel's governance patterns now live natively in the agent
environment it was built for, and this repository preserves the reference
implementation.
