import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from operator_kernel.schema_validator import validate_data_against_schema


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _short_id(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _new_run_id(seed: str, used_ids: Set[str]) -> str:
    candidate = f"run_{_short_id(seed)}"
    if candidate not in used_ids:
        return candidate

    # Collision fallback: uuid-backed run id keeps IDs unique even under tight timestamp reuse.
    while True:
        candidate = f"run_{uuid.uuid4().hex[:16]}"
        if candidate not in used_ids:
            return candidate


def _index_path(root: Path) -> Path:
    return root / "artifacts" / "index" / "run_index.json"


def _load_schema(root: Path) -> Dict[str, Any]:
    schema_path = root / "config" / "schemas" / "run_index.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _load_or_init_index(root: Path) -> Dict[str, Any]:
    path = _index_path(root)
    if not path.exists():
        return {"version": 1, "updated_at": _utc_now_iso(), "runs": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_run_ids(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    used_ids: Set[str] = set()
    normalized: List[Dict[str, Any]] = []

    for idx, run in enumerate(runs):
        row = dict(run)
        row.setdefault("run_type", "prod")
        existing = str(row.get("id", "")).strip()

        if existing and existing not in used_ids:
            row["id"] = existing
            used_ids.add(existing)
            normalized.append(row)
            continue

        seed = (
            f"{row.get('timestamp','')}|{row.get('mode','')}|"
            f"{row.get('task','')}|{row.get('plan_id','')}|{idx}|normalize"
        )
        row["id"] = _new_run_id(seed, used_ids)
        used_ids.add(row["id"])
        normalized.append(row)

    return normalized


def repair_run_index(root: Path, *, max_entries: int = 1000) -> Path:
    index = _load_or_init_index(root)
    runs = list(index.get("runs", []))
    runs = _normalize_run_ids(runs)
    cap = max(1, int(max_entries))
    runs = runs[-cap:]

    index["runs"] = runs
    index["updated_at"] = _utc_now_iso()

    schema = _load_schema(root)
    errors = validate_data_against_schema(index, schema)
    if errors:
        raise ValueError("run index schema validation failed: " + "; ".join(errors))

    path = _index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return path


def append_run_entry(
    root: Path,
    *,
    mode: str,
    task: str,
    plan_id: str,
    action_id: str,
    status: str,
    max_entries: int = 1000,
    proof_path: Optional[str] = None,
    verification_status: Optional[str] = None,
    recommendation_count: Optional[int] = None,
    inspect_branch: Optional[str] = None,
    inspect_token_estimate: Optional[int] = None,
    inspect_estimated_cost_usd: Optional[float] = None,
    inspect_cited_sources: Optional[List[str]] = None,
    inspect_cost_source: Optional[str] = None,
    inspect_provider: Optional[str] = None,
    inspect_model: Optional[str] = None,
    inspect_input_tokens: Optional[int] = None,
    inspect_output_tokens: Optional[int] = None,
    run_type: str = "prod",
) -> Path:
    index = _load_or_init_index(root)
    existing_ids = {
        str(r.get("id", "")).strip()
        for r in index.get("runs", [])
        if str(r.get("id", "")).strip()
    }

    now = _utc_now_iso()
    entry_id = _new_run_id(f"{now}|{mode}|{task}|{plan_id}|append", existing_ids)

    entry = {
        "id": entry_id,
        "timestamp": now,
        "mode": mode,
        "run_type": run_type,
        "task": task,
        "plan_id": plan_id,
        "action_id": action_id,
        "status": status,
    }
    if proof_path is not None:
        entry["proof_path"] = proof_path
    if verification_status is not None:
        entry["verification_status"] = verification_status
    if recommendation_count is not None:
        entry["recommendation_count"] = recommendation_count
    if inspect_branch is not None:
        entry["inspect_branch"] = inspect_branch
    if inspect_token_estimate is not None:
        entry["inspect_token_estimate"] = int(inspect_token_estimate)
    if inspect_estimated_cost_usd is not None:
        entry["inspect_estimated_cost_usd"] = float(inspect_estimated_cost_usd)
    if inspect_cited_sources is not None:
        entry["inspect_cited_sources"] = list(inspect_cited_sources)
    if inspect_cost_source is not None:
        entry["inspect_cost_source"] = str(inspect_cost_source)
    if inspect_provider is not None:
        entry["inspect_provider"] = str(inspect_provider)
    if inspect_model is not None:
        entry["inspect_model"] = str(inspect_model)
    if inspect_input_tokens is not None:
        entry["inspect_input_tokens"] = int(inspect_input_tokens)
    if inspect_output_tokens is not None:
        entry["inspect_output_tokens"] = int(inspect_output_tokens)

    runs = list(index.get("runs", []))
    runs.append(entry)
    runs = _normalize_run_ids(runs)
    cap = max(1, int(max_entries))
    runs = runs[-cap:]
    index["runs"] = runs
    index["updated_at"] = _utc_now_iso()

    schema = _load_schema(root)
    errors = validate_data_against_schema(index, schema)
    if errors:
        raise ValueError("run index schema validation failed: " + "; ".join(errors))

    path = _index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return path
