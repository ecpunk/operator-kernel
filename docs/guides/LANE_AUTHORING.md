# Lane Authoring Guide

This guide is for operators and Engine module consumers who need to add a new
executable lane to the kernel. Read this before touching any config files.

---

## What a Lane Is

A lane is a named, config-driven execution path the kernel can match, plan, and
execute against a specific service or concern. The kernel resolution code never
changes when you add a lane — you are only supplying data that the kernel reads.

Three files define a lane:

1. `config/lanes/<action_id>.json` — the lane spec (steps, probes, rollback).
2. `config/lane_catalog.json` — the keyword mapping that triggers the lane.
3. `config/lane_lifecycle.json` — the lifecycle state (`candidate` or `active`).

The onboarding runner manages files 2 and 3 automatically. You write file 1.

---

## Naming Convention

`action_id` must be lowercase, underscore-separated, and follow the pattern:

```
<service>_<intent>_v<version>
```

Examples: `plex_safe_recovery_v1`, `sonarr_safe_recovery_v1`.

- Use `safe_recovery` for standard web-service availability recovery.
- Increment `v<N>` rather than modifying an existing spec in place.
- The `action_id` in the JSON file must exactly match the filename (without `.json`).

---

## Lane Spec Fields

See `config/lanes/_template_lane.json` for a copy-paste starting point.

### Required fields

| Field | Type | Description |
|---|---|---|
| `action_id` | string | Matches filename. Must be unique across all lanes. |
| `lane` | string | Logical lane group. Use `media_incident` for standard media services. |
| `service` | string | Short service name used in log and container lookups. |
| `endpoint` | string | Primary HTTP endpoint the kernel probes for health. |
| `verify_probes` | array | At least one required probe (see below). |
| `rollback` | array of strings | Human-readable rollback steps shown in any plan. |
| `verify` | array of strings | Human-readable verification checklist shown in any plan. |

### Optional fields (with defaults)

| Field | Type | Default | Description |
|---|---|---|---|
| `log_tail` | int | 40 | Number of log lines pulled during execution. |
| `http_ok_prefixes` | array | `["HTTP_2", "HTTP_3"]` | Acceptable HTTP status prefixes. |
| `warmup_probe_retries` | int | 4 | Retry count for warmup healthcheck before declaring failure. |
| `warmup_probe_sleep_seconds` | int | 3 | Sleep between warmup retries in seconds. |

### verify_probes

Each entry in `verify_probes` is an active check run after execution steps complete.

```json
{
  "name": "unique_probe_name",
  "command": "curl -sSI -m 5 http://127.0.0.1:<port>/path | tr -d '\\r'",
  "expected_substring": "Expected-Header: value",
  "required": true
}
```

- `name`: unique within the lane, used in proof pack output.
- `command`: shell command whose stdout is checked for `expected_substring`.
- `expected_substring`: exact string that must appear in command output.
- `required`: if `true`, probe failure blocks the verification step and marks the run failed. If `false`, probe failure is advisory only.

At least one probe with `"required": true` is expected for any `active` lane.

For web UI services, a content probe (`curl -sS ... | head -c 12000`) checking
for an app-specific page marker is the preferred pattern. For API services, a
header probe (`curl -sSI ...`) checking for a known response header is preferred.

---

## Keyword Mapping

The kernel resolves a lane from operator task text using keyword matching in
`config/lane_catalog.json`. The resolution policy prefers:

1. Mapping priority (higher wins).
2. Whole-word match over substring match.
3. Earliest keyword mention in the task string.
4. Longest matching keyword.

Add one mapping entry per lane. Use the service name and any reliable trigger
phrases as keywords. Keep keywords specific — avoid generic words that could
match unintended tasks.

```json
{
  "keywords": ["myservice"],
  "priority": 100,
  "action_id": "myservice_safe_recovery_v1"
}
```

Do not edit `lane_catalog.json` directly when onboarding. The onboarding runner
adds the mapping for you once the spec passes validation.

---

## Lifecycle States

Lanes move through two states managed in `config/lane_lifecycle.json`:

- `candidate`: lane spec exists, onboarding is in progress. Service scorecard
  gates exempt this lane during the onboarding window.
- `active`: onboarding complete, lane is fully trusted and gate-enforced.

The onboarding runner sets these states automatically. Do not manually promote
a lane from `candidate` to `active` — let the runner do it after passing all checks.

---

## Onboarding Command

Once your lane spec is written at `config/lanes/<action_id>.json`, run:

```
PYTHONPATH=src python3 src/operator_ops/run_lane_onboarding.py \
  --action-id <action_id> \
  --task "<representative task phrase that should match this lane>" \
  --channel text \
  --days 7
```

The runner will:
1. Set lifecycle state to `candidate`.
2. Run propose mode against the representative task and confirm the lane resolves.
3. Run a dry-run execute cycle and confirm probes pass.
4. Run bootstrap and scorecard gate checks.
5. Add the keyword mapping to `lane_catalog.json`.
6. Promote lifecycle state to `active` if all steps pass.
7. Emit an onboarding artifact to `artifacts/onboarding/`.

If any step fails, the runner halts and leaves the lane in `candidate` state.
Inspect the artifact and fix the spec before re-running.

---

## Checklist Before Running Onboarding

- [ ] `action_id` in the JSON matches the filename exactly.
- [ ] At least one `verify_probe` with `"required": true` is present.
- [ ] The endpoint is reachable from the host where the kernel runs.
- [ ] The representative task phrase clearly and uniquely identifies this service.
- [ ] Keywords in the mapping do not collide with existing lane keywords.
- [ ] `rollback` and `verify` fields contain meaningful operator-readable steps.

---

## After Onboarding

- Confirm the onboarding artifact in `artifacts/onboarding/` shows `status: pass`.
- Run `PYTHONPATH=src python3 src/operator_tests/stage3_bootstrap_tests.py --strict` to confirm the new lane
  is covered by the bootstrap suite.
- On the next Stage 3 orchestrator run, the service scorecard will include the
  new lane and gate it normally.

---

## Engine Module Consumers

If you are adding lanes as part of an Engine module rather than operator-kernel
directly, the same process applies. The kernel is config-driven; your module
supplies `config/lanes/`, `lane_catalog.json` entries, and `lane_lifecycle.json`
entries. The kernel resolution and execution code does not change.

Lane configs are not packaged with the kernel. They are application-layer data
supplied by the operator or module consumer at runtime.
