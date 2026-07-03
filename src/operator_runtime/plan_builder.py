import hashlib
from datetime import datetime, timezone

from operator_kernel.contract_engine import PlanRecord


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _hash_payload(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def classify_risk(task: str) -> str:
    lowered = task.lower()
    critical_terms = ["rm -rf", "destroy all", "factory reset", "nuke"]
    high_terms = ["delete", "drop", "format", "wipe", "destroy", "disable"]
    medium_terms = ["restart", "update", "patch", "apply", "change", "fix"]
    if any(term in lowered for term in critical_terms):
        return "critical"
    if any(term in lowered for term in high_terms):
        return "high"
    if any(term in lowered for term in medium_terms):
        return "medium"
    return "low"


def build_media_incident_plan(task: str, channel: str, mode: str) -> PlanRecord:
    created_at = _utc_now_iso()
    payload = f"{created_at}|{channel}|{mode}|{task}"
    plan_id = f"plan_{_hash_payload(payload)}"
    approval_token = f"appr_{_hash_payload(plan_id + task)}"

    steps = [
        "Collect service health and container status for media lane.",
        "Inspect recent service logs for explicit error signatures.",
        "Check mounts, network reachability, and auth/session state.",
        "Map symptom to one bounded fix path and expected effect.",
    ]

    rollback = [
        "Restore prior service config snapshot if a config mutation is applied.",
        "Restart service with known-good compose definition.",
        "Re-verify endpoint and recent log stability.",
    ]

    verify = [
        "Target endpoint returns expected HTTP status.",
        "Service/container status is healthy.",
        "Recent logs show no repeating fatal error.",
    ]

    lowered = task.lower()
    action_id = "plex_safe_recovery_v1" if "plex" in lowered else ""

    return PlanRecord(
        plan_id=plan_id,
        created_at=created_at,
        lane="media_incident",
        channel=channel,
        mode=mode,
        task=task,
        risk=classify_risk(task),
        steps=steps,
        rollback=rollback,
        verify=verify,
        action_id=action_id,
        recommendations=[],
        approval_token=approval_token,
        status="proposed",
    )
