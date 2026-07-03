import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from .schema_validator import validate_data_against_schema


@dataclass
class ArtifactRetentionPolicy:
    max_plan_files: int
    max_plan_age_days: int
    max_proof_files: int
    max_proof_age_days: int
    max_run_index_entries: int
    soft_deprecate_enabled: bool
    archive_retention_days: int


@dataclass
class Guardrails:
    allowed_modes: List[str]
    channel_allowlist: List[str]
    require_approval_for_execute: bool
    token_min_length: int
    max_plan_age_minutes: int
    apply_blocked_risks: List[str]
    allowed_plan_risks: List[str]
    apply_requires_action_id: bool
    recommendation_reorder_min_confidence: float
    stage4_enforce_open_ended_thresholds: bool
    stage4_min_traceability_ratio: float
    stage4_max_open_ended_token_estimate: int
    stage4_max_open_ended_estimated_cost_usd: float
    artifacts: ArtifactRetentionPolicy


@dataclass
class PlanRecord:
    plan_id: str
    created_at: str
    lane: str
    channel: str
    mode: str
    task: str
    risk: str
    steps: List[str]
    rollback: List[str]
    verify: List[str]
    action_id: str
    recommendations: List[Dict[str, str]]
    approval_token: str
    status: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_guardrails(config_path: Path) -> Guardrails:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    execution = data.get("execution", {})
    propose = data.get("propose", {})
    stage4 = data.get("stage4_inspect", {})
    artifacts = data.get("artifacts", {})
    return Guardrails(
        allowed_modes=list(data.get("allowed_modes", [])),
        channel_allowlist=list(data.get("channel_allowlist", [])),
        require_approval_for_execute=bool(execution.get("require_approval_for_execute", True)),
        token_min_length=int(execution.get("token_min_length", 8)),
        max_plan_age_minutes=int(execution.get("max_plan_age_minutes", 240)),
        apply_blocked_risks=[
            str(v).lower() for v in execution.get("apply_blocked_risks", ["high", "critical"])
        ],
        allowed_plan_risks=[
            str(v).lower() for v in execution.get(
                "allowed_plan_risks", ["low", "medium", "high", "critical"]
            )
        ],
        apply_requires_action_id=bool(execution.get("apply_requires_action_id", True)),
        recommendation_reorder_min_confidence=float(
            propose.get("recommendation_reorder_min_confidence", 0.9)
        ),
        stage4_enforce_open_ended_thresholds=bool(
            stage4.get("enforce_open_ended_thresholds", False)
        ),
        stage4_min_traceability_ratio=float(
            stage4.get("min_traceability_ratio", 0.95)
        ),
        stage4_max_open_ended_token_estimate=int(
            stage4.get("max_open_ended_token_estimate", 5000)
        ),
        stage4_max_open_ended_estimated_cost_usd=float(
            stage4.get("max_open_ended_estimated_cost_usd", 1.0)
        ),
        artifacts=ArtifactRetentionPolicy(
            max_plan_files=int(artifacts.get("max_plan_files", 500)),
            max_plan_age_days=int(artifacts.get("max_plan_age_days", 30)),
            max_proof_files=int(artifacts.get("max_proof_files", 1000)),
            max_proof_age_days=int(artifacts.get("max_proof_age_days", 60)),
            max_run_index_entries=int(artifacts.get("max_run_index_entries", 1000)),
            soft_deprecate_enabled=bool(artifacts.get("soft_deprecate_enabled", True)),
            archive_retention_days=int(artifacts.get("archive_retention_days", 90)),
        ),
    )


def validate_mode_and_channel(mode: str, channel: str, guardrails: Guardrails) -> List[str]:
    errors: List[str] = []
    if mode not in guardrails.allowed_modes:
        errors.append(f"mode '{mode}' is not allowed")
    if channel not in guardrails.channel_allowlist:
        errors.append(f"channel '{channel}' is not allowed")
    return errors


def _plan_age_minutes(created_at: str) -> int:
    ts = datetime.fromisoformat(created_at)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - ts
    return max(0, int(age.total_seconds() // 60))


def evaluate_execute_risk_controls(
    plan: Dict[str, Any], guardrails: Guardrails, apply: bool
) -> List[str]:
    errors: List[str] = []

    risk = str(plan.get("risk", "")).strip().lower()
    if risk not in guardrails.allowed_plan_risks:
        errors.append(
            f"plan risk '{risk or 'unknown'}' is not allowed by execution policy"
        )

    created_at = str(plan.get("created_at", "")).strip()
    if created_at:
        try:
            plan_age = _plan_age_minutes(created_at)
            if plan_age > guardrails.max_plan_age_minutes:
                errors.append(
                    "plan age exceeds execution policy maximum "
                    f"({plan_age}m > {guardrails.max_plan_age_minutes}m)"
                )
        except ValueError:
            errors.append("plan created_at is not a valid ISO timestamp")

    if apply and risk in guardrails.apply_blocked_risks:
        errors.append(
            f"apply is blocked for risk tier '{risk}' by execution policy"
        )

    if apply and guardrails.apply_requires_action_id and not str(plan.get("action_id", "")).strip():
        errors.append("apply requires allowlisted action_id in the approved plan")

    return errors


def plan_to_dict(plan: PlanRecord) -> Dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "created_at": plan.created_at,
        "lane": plan.lane,
        "channel": plan.channel,
        "mode": plan.mode,
        "task": plan.task,
        "risk": plan.risk,
        "steps": plan.steps,
        "rollback": plan.rollback,
        "verify": plan.verify,
        "action_id": plan.action_id,
        "recommendations": plan.recommendations,
        "approval_token": plan.approval_token,
        "status": plan.status,
    }


def save_plan(plan: PlanRecord, plans_dir: Path) -> Path:
    plans_dir.mkdir(parents=True, exist_ok=True)
    out_path = plans_dir / f"{plan.plan_id}.json"
    payload = plan_to_dict(plan)
    root = Path(__file__).resolve().parents[2]
    schema_path = root / "config" / "schemas" / "plan.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = validate_data_against_schema(payload, schema)
    if errors:
        raise ValueError("plan schema validation failed: " + "; ".join(errors))
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out_path


def load_plan(plan_id: str, plans_dir: Path) -> Dict[str, Any]:
    plan_path = plans_dir / f"{plan_id}.json"
    if not plan_path.exists():
        raise FileNotFoundError(f"plan not found: {plan_path}")
    return json.loads(plan_path.read_text(encoding="utf-8"))


def verify_approval(token: str, guardrails: Guardrails, expected: str) -> List[str]:
    errors: List[str] = []
    if guardrails.require_approval_for_execute:
        if not token:
            errors.append("approval token is required for execute mode")
        if token and len(token) < guardrails.token_min_length:
            errors.append("approval token length is below policy minimum")
        if token and token != expected:
            errors.append("approval token does not match the approved plan token")
    return errors


def write_proof_pack(plan: Dict[str, Any], proof_dir: Path, execution_result: Dict[str, Any]) -> Path:
    proof_dir.mkdir(parents=True, exist_ok=True)
    ts = _utc_now_iso().replace(":", "-")
    out_path = proof_dir / f"proof_{plan['plan_id']}_{ts}.json"

    payload = {
        "proof_version": 1,
        "generated_at": _utc_now_iso(),
        "plan_id": plan["plan_id"],
        "task": plan["task"],
        "lane": plan["lane"],
        "precheck": execution_result.get("precheck", {}),
        "command_transcript": execution_result.get("command_transcript", []),
        "verification": execution_result.get("verification", {}),
        "rollback": execution_result.get("rollback", plan.get("rollback", [])),
        "execution_notes": execution_result.get("execution_notes", []),
        "observations": execution_result.get("observations", []),
        "recommendations": execution_result.get("recommendations", []),
    }

    root = Path(__file__).resolve().parents[2]
    schema_path = root / "config" / "schemas" / "proof.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = validate_data_against_schema(payload, schema)
    if errors:
        raise ValueError("proof schema validation failed: " + "; ".join(errors))

    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out_path
