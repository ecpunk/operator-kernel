import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat()


def _parse_iso(ts: str) -> datetime:
    value = datetime.fromisoformat(ts)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _is_recent(ts: str, days: int) -> bool:
    if days <= 0:
        return True
    return _parse_iso(ts) >= (_utc_now() - timedelta(days=days))


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_learning_policy(root: Path) -> Dict[str, Any]:
    payload = _load_json(root / "config" / "guardrails.json", {})
    learning = dict(payload.get("learning", {}))
    return {
        "review_only": bool(learning.get("review_only", True)),
        "default_window_days": int(learning.get("default_window_days", 7)),
        "min_task_recurrence": int(learning.get("min_task_recurrence", 3)),
        "min_failure_recurrence": int(learning.get("min_failure_recurrence", 2)),
        "max_suggestions": int(learning.get("max_suggestions", 12)),
    }


def _load_runs(root: Path, days: int, include_tests: bool) -> List[Dict[str, Any]]:
    payload = _load_json(root / "artifacts" / "index" / "run_index.json", {"runs": []})
    rows: List[Dict[str, Any]] = []
    for run in payload.get("runs", []):
        ts = str(run.get("timestamp", "")).strip()
        if not ts:
            continue
        try:
            if not _is_recent(ts, days):
                continue
        except ValueError:
            continue

        row = dict(run)
        row.setdefault("run_type", "prod")
        if not include_tests and str(row.get("run_type", "prod")).lower() == "test":
            continue
        rows.append(row)
    return rows


def _build_task_suggestions(runs: List[Dict[str, Any]], min_recurrence: int) -> List[Dict[str, Any]]:
    counts = Counter(str(r.get("task", "")).strip() for r in runs if str(r.get("task", "")).strip())
    items: List[Dict[str, Any]] = []
    for task, count in counts.most_common():
        if count < min_recurrence:
            continue
        items.append(
            {
                "id": f"task_recurrence::{task}",
                "type": "doc_update",
                "priority": "medium",
                "target": "10-CONCEPT/OPERATOR_KERNEL_CONCEPT.md",
                "evidence_count": count,
                "rationale": "Recurring operator task pattern detected in run history.",
                "recommended_change": (
                    "Add or refine a troubleshooting section for recurring task pattern: " + task
                ),
                "review_only": True,
            }
        )
    return items


def _build_failure_suggestions(runs: List[Dict[str, Any]], min_recurrence: int) -> List[Dict[str, Any]]:
    blocked_statuses = {"blocked_approval", "blocked_risk_policy", "blocked_missing_plan_id"}
    failure_counts: Counter[str] = Counter()
    for run in runs:
        action_id = str(run.get("action_id", "")).strip()
        if not action_id:
            continue

        status = str(run.get("status", "")).strip()
        verification = str(run.get("verification_status", "")).strip()
        if verification == "failed":
            failure_counts[action_id] += 1
            continue
        if any(status.startswith(prefix) for prefix in blocked_statuses):
            failure_counts[action_id] += 1

    items: List[Dict[str, Any]] = []
    for action_id, count in failure_counts.most_common():
        if count < min_recurrence:
            continue
        items.append(
            {
                "id": f"failure_recurrence::{action_id}",
                "type": "policy_review",
                "priority": "high",
                "target": "config/guardrails.json",
                "evidence_count": count,
                "rationale": "Repeated execute failures or policy blocks for lane action.",
                "recommended_change": (
                    "Review lane verification probes and failure policy for action_id: " + action_id
                ),
                "review_only": True,
            }
        )
    return items


def _build_probe_coverage_suggestions(root: Path) -> List[Dict[str, Any]]:
    lifecycle = _load_json(root / "config" / "lane_lifecycle.json", {"actions": {}})
    active_ids = [
        action_id
        for action_id, row in dict(lifecycle.get("actions", {})).items()
        if str(dict(row).get("state", "")).strip() == "active"
    ]

    lane_specs: Dict[str, Dict[str, Any]] = {}
    for lane_path in sorted((root / "config" / "lanes").glob("*.json")):
        lane = _load_json(lane_path, {})
        action_id = str(lane.get("action_id", "")).strip()
        if action_id:
            lane_specs[action_id] = lane

    missing_required: List[str] = []
    for action_id in active_ids:
        lane = lane_specs.get(action_id, {})
        probes = list(lane.get("verify_probes", []))
        has_required = any(bool(dict(p).get("required", False)) for p in probes)
        if not has_required:
            missing_required.append(action_id)

    items: List[Dict[str, Any]] = []
    for action_id in sorted(missing_required):
        items.append(
            {
                "id": f"probe_coverage::{action_id}",
                "type": "lane_quality",
                "priority": "medium",
                "target": "config/lanes",
                "evidence_count": 1,
                "rationale": "Active lane lacks required verify_probes coverage.",
                "recommended_change": (
                    "Add at least one required verify_probe for action_id: " + action_id
                ),
                "review_only": True,
            }
        )
    return items


def _priority_rank(value: str) -> int:
    if value == "high":
        return 3
    if value == "medium":
        return 2
    return 1


def _build_payload(
    root: Path,
    days: int,
    include_tests: bool,
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    runs = _load_runs(root, days, include_tests)
    task_suggestions = _build_task_suggestions(runs, int(policy["min_task_recurrence"]))
    failure_suggestions = _build_failure_suggestions(runs, int(policy["min_failure_recurrence"]))
    probe_suggestions = _build_probe_coverage_suggestions(root)

    combined = task_suggestions + failure_suggestions + probe_suggestions
    combined.sort(
        key=lambda row: (
            -_priority_rank(str(row.get("priority", "low"))),
            -int(row.get("evidence_count", 0)),
            str(row.get("id", "")),
        )
    )
    max_suggestions = max(1, int(policy["max_suggestions"]))
    suggestions = combined[:max_suggestions]

    for row in suggestions:
        row["review_only"] = bool(policy["review_only"])

    return {
        "generated_at": _utc_now_iso(),
        "window_days": days,
        "include_tests": include_tests,
        "policy": policy,
        "metrics": {
            "runs_total": len(runs),
            "candidate_suggestions_total": len(combined),
            "suggestions_total": len(suggestions),
        },
        "suggestions": suggestions,
        "sources": {
            "run_index": str(root / "artifacts" / "index" / "run_index.json"),
            "lane_lifecycle": str(root / "config" / "lane_lifecycle.json"),
            "lanes_dir": str(root / "config" / "lanes"),
        },
    }


def _write_artifacts(root: Path, payload: Dict[str, Any]) -> Dict[str, str]:
    out_dir = root / "artifacts" / "learning"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = out_dir / f"learning_suggestions_{stamp}.json"
    latest = out_dir / "learning_suggestions_latest.json"

    text = json.dumps(payload, indent=2) + "\n"
    snapshot.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return {"snapshot": str(snapshot), "latest": str(latest)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None, help="Rolling window in days")
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include synthetic test runs in recurrence analysis",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    policy = _load_learning_policy(root)
    days = int(args.days) if args.days is not None else int(policy["default_window_days"])

    payload = _build_payload(root, days, args.include_tests, policy)
    paths = _write_artifacts(root, payload)

    print("Operator Kernel Learning Suggestions")
    print(f"- window_days: {days}")
    print(f"- include_tests: {args.include_tests}")
    print(f"- review_only: {policy['review_only']}")
    print(f"- runs_total: {payload['metrics']['runs_total']}")
    print(f"- candidate_suggestions_total: {payload['metrics']['candidate_suggestions_total']}")
    print(f"- suggestions_total: {payload['metrics']['suggestions_total']}")
    print(f"- snapshot: {paths['snapshot']}")
    print(f"- latest: {paths['latest']}")


if __name__ == "__main__":
    main()