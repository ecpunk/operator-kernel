import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Set

from operator_kernel.schema_validator import validate_data_against_schema


def _json_files_sorted_newest(directory: Path) -> List[Path]:
    if not directory.exists():
        return []
    files = [p for p in directory.glob("*.json") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _archive_root(root: Path) -> Path:
    return root / "artifacts" / "archive"


def _archive_index_path(root: Path) -> Path:
    return _archive_root(root) / "archive_index.json"


def _archive_schema_path(root: Path) -> Path:
    return root / "config" / "schemas" / "archive_index.schema.json"


def _load_archive_index(root: Path) -> Dict[str, Any]:
    path = _archive_index_path(root)
    if not path.exists():
        return {"version": 1, "updated_at": _utc_now_iso(), "records": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_archive_index(root: Path, payload: Dict[str, Any]) -> None:
    schema = json.loads(_archive_schema_path(root).read_text(encoding="utf-8"))
    errors = validate_data_against_schema(payload, schema)
    if errors:
        raise ValueError("archive index schema validation failed: " + "; ".join(errors))

    path = _archive_index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _candidate_reasons(files: List[Path], max_files: int, max_age_days: int) -> Dict[Path, Set[str]]:
    reasons: Dict[Path, Set[str]] = {}

    if max_age_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        for path in files:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if modified < cutoff:
                reasons.setdefault(path, set()).add("age")

    if max_files > 0 and len(files) > max_files:
        for path in files[max_files:]:
            reasons.setdefault(path, set()).add("count")

    return reasons


def _dedupe_archive_name(path: Path) -> Path:
    if not path.exists():
        return path
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return path.with_name(f"{path.stem}_dup_{stamp}{path.suffix}")


def _soft_archive_candidates(
    root: Path,
    *,
    source_dir: Path,
    archive_subdir_name: str,
    file_type: str,
    max_files: int,
    max_age_days: int,
    enabled: bool,
) -> Dict[str, int]:
    files = _json_files_sorted_newest(source_dir)
    reasons = _candidate_reasons(files, max_files=max_files, max_age_days=max_age_days)

    if not reasons:
        return {
            "archived": 0,
            "archived_by_age": 0,
            "archived_by_count": 0,
            "deleted": 0,
            "deleted_by_age": 0,
            "deleted_by_count": 0,
        }

    archived = 0
    archived_by_age = 0
    archived_by_count = 0
    archive_failed = 0
    deleted = 0
    deleted_by_age = 0
    deleted_by_count = 0
    errors: List[str] = []

    archive_index = _load_archive_index(root)
    archive_dir = _archive_root(root) / archive_subdir_name
    archive_dir.mkdir(parents=True, exist_ok=True)

    for path, reason_set in sorted(
        reasons.items(), key=lambda item: item[0].stat().st_mtime
    ):
        if enabled:
            target = _dedupe_archive_name(archive_dir / path.name)
            try:
                path.replace(target)
            except OSError as exc:
                archive_failed += 1
                errors.append(
                    f"archive_move_failed:{path} -> {target}: {exc.__class__.__name__}: {exc}"
                )
                continue
            archive_index.setdefault("records", []).append(
                {
                    "id": f"arc_{uuid.uuid4().hex[:16]}",
                    "file_type": file_type,
                    "original_path": str(path),
                    "archived_path": str(target),
                    "reasons": sorted(list(reason_set)),
                    "archived_at": _utc_now_iso(),
                    "status": "archived",
                }
            )
            archived += 1
            if "age" in reason_set:
                archived_by_age += 1
            if "count" in reason_set:
                archived_by_count += 1
        else:
            path.unlink(missing_ok=True)
            deleted += 1
            if "age" in reason_set:
                deleted_by_age += 1
            if "count" in reason_set:
                deleted_by_count += 1

    archive_index["updated_at"] = _utc_now_iso()
    if enabled:
        _save_archive_index(root, archive_index)

    return {
        "archived": archived,
        "archived_by_age": archived_by_age,
        "archived_by_count": archived_by_count,
        "archive_failed": archive_failed,
        "deleted": deleted,
        "deleted_by_age": deleted_by_age,
        "deleted_by_count": deleted_by_count,
        "errors": errors,
    }


def _purge_expired_archive(root: Path, archive_retention_days: int) -> int:
    if archive_retention_days <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=archive_retention_days)
    archive_index = _load_archive_index(root)
    records = list(archive_index.get("records", []))
    purged = 0

    for record in records:
        if record.get("status") != "archived":
            continue

        archived_at = str(record.get("archived_at", "")).strip()
        if not archived_at:
            continue

        try:
            ts = datetime.fromisoformat(archived_at)
        except ValueError:
            continue

        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        if ts < cutoff:
            archived_path = Path(str(record.get("archived_path", "")))
            archived_path.unlink(missing_ok=True)
            record["status"] = "purged"
            record["purged_at"] = _utc_now_iso()
            purged += 1

    archive_index["records"] = records
    archive_index["updated_at"] = _utc_now_iso()
    _save_archive_index(root, archive_index)
    return purged


def run_artifact_gc(
    root: Path,
    *,
    max_plan_files: int,
    max_plan_age_days: int,
    max_proof_files: int,
    max_proof_age_days: int,
    soft_deprecate_enabled: bool,
    archive_retention_days: int,
) -> Dict[str, Any]:
    plans_dir = root / "artifacts" / "plans"
    proofs_dir = root / "artifacts" / "proofs"

    plan_summary = _soft_archive_candidates(
        root,
        source_dir=plans_dir,
        archive_subdir_name="plans",
        file_type="plan",
        max_files=max_plan_files,
        max_age_days=max_plan_age_days,
        enabled=soft_deprecate_enabled,
    )
    proof_summary = _soft_archive_candidates(
        root,
        source_dir=proofs_dir,
        archive_subdir_name="proofs",
        file_type="proof",
        max_files=max_proof_files,
        max_age_days=max_proof_age_days,
        enabled=soft_deprecate_enabled,
    )

    purged_archive = _purge_expired_archive(root, archive_retention_days)

    return {
        "soft_deprecate_enabled": soft_deprecate_enabled,
        "plans_archived": plan_summary["archived"],
        "plans_archived_by_age": plan_summary["archived_by_age"],
        "plans_archived_by_count": plan_summary["archived_by_count"],
        "plans_archive_failed": plan_summary.get("archive_failed", 0),
        "proofs_archived": proof_summary["archived"],
        "proofs_archived_by_age": proof_summary["archived_by_age"],
        "proofs_archived_by_count": proof_summary["archived_by_count"],
        "proofs_archive_failed": proof_summary.get("archive_failed", 0),
        "plans_deleted": plan_summary["deleted"],
        "plans_deleted_by_age": plan_summary["deleted_by_age"],
        "plans_deleted_by_count": plan_summary["deleted_by_count"],
        "proofs_deleted": proof_summary["deleted"],
        "proofs_deleted_by_age": proof_summary["deleted_by_age"],
        "proofs_deleted_by_count": proof_summary["deleted_by_count"],
        "archive_purged": purged_archive,
        "errors": plan_summary.get("errors", []) + proof_summary.get("errors", []),
    }
