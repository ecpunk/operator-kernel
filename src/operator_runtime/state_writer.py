import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from operator_kernel.schema_validator import validate_data_against_schema


@dataclass
class StateWriterPolicy:
    enabled: bool
    max_history_entries: int
    max_summary_length: int
    max_recommendation_length: int
    trusted_targets: List[str]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sanitize_text(value: str, max_len: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned[:max_len]


def load_state_writer_policy(config_path: Path) -> StateWriterPolicy:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return StateWriterPolicy(
        enabled=bool(data.get("enabled", False)),
        max_history_entries=max(1, int(data.get("max_history_entries", 50))),
        max_summary_length=max(40, int(data.get("max_summary_length", 180))),
        max_recommendation_length=max(60, int(data.get("max_recommendation_length", 220))),
        trusted_targets=[str(p) for p in data.get("trusted_targets", [])],
    )


def _is_trusted_target(path: Path, policy: StateWriterPolicy) -> bool:
    return str(path.resolve()) in {str(Path(p).resolve()) for p in policy.trusted_targets}


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _state_schema(root: Path) -> Dict[str, Any]:
    schema_path = root / "config" / "schemas" / "operator_state.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _build_summary(plan: Dict[str, Any], execution_result: Dict[str, Any], policy: StateWriterPolicy) -> str:
    status = str(execution_result.get("verification", {}).get("status", "unknown"))
    risk = str(plan.get("risk", "unknown"))
    action_id = str(plan.get("action_id", "none") or "none")
    return _sanitize_text(
        f"execute {status}; risk={risk}; action={action_id}", policy.max_summary_length
    )


def _first_recommendation(
    execution_result: Dict[str, Any], policy: StateWriterPolicy
) -> str:
    recs = list(execution_result.get("recommendations", []))
    if not recs:
        return ""
    first = str(recs[0].get("recommendation", ""))
    return _sanitize_text(first, policy.max_recommendation_length)


def write_canonical_state(
    root: Path,
    *,
    plan: Dict[str, Any],
    proof_path: Path,
    execution_result: Dict[str, Any],
    apply: bool,
) -> Dict[str, Any]:
    policy = load_state_writer_policy(root / "config" / "state_writer_targets.json")
    if not policy.enabled:
        return {"enabled": False, "updated": []}

    dashboard_path = Path("/opt/stack/services/apps/dashboard/status.json")
    operator_state_path = root / "artifacts" / "state" / "operator_state.json"

    updated: List[str] = []

    event = {
        "timestamp": _utc_now_iso(),
        "plan_id": str(plan.get("plan_id", "")),
        "task": _sanitize_text(str(plan.get("task", "")), 160),
        "risk": str(plan.get("risk", "unknown")),
        "apply": bool(apply),
        "action_id": str(plan.get("action_id", "") or "none"),
        "verification_status": str(
            execution_result.get("verification", {}).get("status", "unknown")
        ),
        "proof_path": str(proof_path),
        "summary": _build_summary(plan, execution_result, policy),
        "top_recommendation": _first_recommendation(execution_result, policy),
    }

    if _is_trusted_target(operator_state_path, policy):
        payload = _load_json(
            operator_state_path,
            {"version": 1, "updated_at": _utc_now_iso(), "last_event": {}, "history": []},
        )
        history = list(payload.get("history", []))
        history.append(event)
        payload["history"] = history[-policy.max_history_entries :]
        payload["last_event"] = event
        payload["updated_at"] = _utc_now_iso()

        schema = _state_schema(root)
        errors = validate_data_against_schema(payload, schema)
        if errors:
            raise ValueError("operator state schema validation failed: " + "; ".join(errors))

        _write_json(operator_state_path, payload)
        updated.append(str(operator_state_path))

    if _is_trusted_target(dashboard_path, policy):
        payload = _load_json(dashboard_path, {})
        payload["operator_kernel"] = {
            "updated_at": _utc_now_iso(),
            "last_execute": {
                "plan_id": event["plan_id"],
                "task": event["task"],
                "risk": event["risk"],
                "apply": event["apply"],
                "action_id": event["action_id"],
                "verification_status": event["verification_status"],
                "proof_path": event["proof_path"],
                "summary": event["summary"],
                "top_recommendation": event["top_recommendation"],
            },
        }
        _write_json(dashboard_path, payload)
        updated.append(str(dashboard_path))

    return {"enabled": True, "updated": updated, "event": event}
