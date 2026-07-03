import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional


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


def _plan_timing_metrics(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_plan: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs:
        plan_id = str(run.get("plan_id", "")).strip()
        if not plan_id:
            continue
        by_plan.setdefault(plan_id, []).append(run)

    mttt_values: List[float] = []
    mttvf_values: List[float] = []

    for plan_runs in by_plan.values():
        propose_ts: Optional[datetime] = None
        execute_attempt_ts: Optional[datetime] = None
        verify_completed_ts: Optional[datetime] = None

        for run in sorted(plan_runs, key=lambda r: str(r.get("timestamp", ""))):
            ts = _parse_iso(str(run.get("timestamp", "")))
            mode = str(run.get("mode", "")).strip()
            if mode == "propose" and propose_ts is None:
                propose_ts = ts
            if mode == "execute" and execute_attempt_ts is None:
                execute_attempt_ts = ts
            if (
                mode == "execute"
                and str(run.get("verification_status", "")).strip() == "completed"
                and verify_completed_ts is None
            ):
                verify_completed_ts = ts

        if propose_ts and execute_attempt_ts and execute_attempt_ts >= propose_ts:
            mttt_values.append((execute_attempt_ts - propose_ts).total_seconds() / 60.0)
        if propose_ts and verify_completed_ts and verify_completed_ts >= propose_ts:
            mttvf_values.append((verify_completed_ts - propose_ts).total_seconds() / 60.0)

    return {
        "mttt_minutes": round(mean(mttt_values), 2) if mttt_values else 0.0,
        "mttvf_minutes": round(mean(mttvf_values), 2) if mttvf_values else 0.0,
        "mttt_sample_size": len(mttt_values),
        "mttvf_sample_size": len(mttvf_values),
    }


def _build_trend_report(root: Path, days: int, include_tests: bool, top: int) -> Dict[str, Any]:
    runs = _load_runs(root, days, include_tests)

    by_mode = Counter(str(r.get("mode", "unknown")) for r in runs)
    by_status = Counter(str(r.get("status", "unknown")) for r in runs)
    by_task = Counter(str(r.get("task", "")).strip() for r in runs if str(r.get("task", "")).strip())

    execute_runs = [r for r in runs if str(r.get("mode", "")) == "execute"]
    completed = sum(1 for r in execute_runs if str(r.get("verification_status", "")) == "completed")
    failed = sum(1 for r in execute_runs if str(r.get("verification_status", "")) == "failed")
    success_pct = round((completed / len(execute_runs)) * 100.0, 2) if execute_runs else 0.0

    approval_blocked = sum(1 for r in runs if str(r.get("status", "")).startswith("blocked_approval"))
    risk_blocked = sum(1 for r in runs if str(r.get("status", "")).startswith("blocked_risk_policy"))

    timing = _plan_timing_metrics(runs)

    recurring = [
        {"task": task, "count": count}
        for task, count in by_task.most_common(top)
        if count >= 2
    ]

    return {
        "generated_at": _utc_now_iso(),
        "window_days": days,
        "include_tests": include_tests,
        "metrics": {
            "runs_total": len(runs),
            "execute_total": len(execute_runs),
            "execute_verification_completed_total": completed,
            "execute_verification_failed_total": failed,
            "execute_verification_success_pct": success_pct,
            "approval_failures_total": approval_blocked,
            "risk_policy_blocks_total": risk_blocked,
            "mttt_minutes": timing["mttt_minutes"],
            "mttt_sample_size": timing["mttt_sample_size"],
            "mttvf_minutes": timing["mttvf_minutes"],
            "mttvf_sample_size": timing["mttvf_sample_size"],
        },
        "mode_counts": dict(by_mode),
        "status_counts": dict(by_status),
        "recurring_tasks": recurring,
        "sources": {
            "run_index": str(root / "artifacts" / "index" / "run_index.json"),
        },
    }


def _write_artifacts(root: Path, payload: Dict[str, Any]) -> Dict[str, str]:
    out_dir = root / "artifacts" / "trends"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = out_dir / f"trend_report_{stamp}.json"
    latest = out_dir / "trend_report_latest.json"
    text = json.dumps(payload, indent=2) + "\n"
    snapshot.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return {"snapshot": str(snapshot), "latest": str(latest)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="Rolling window in days")
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include run_type=test entries in trend metrics",
    )
    parser.add_argument("--top", type=int, default=10, help="Top recurring tasks to include")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    payload = _build_trend_report(root, args.days, args.include_tests, args.top)
    paths = _write_artifacts(root, payload)

    metrics = payload["metrics"]
    print("Operator Kernel Trend Report")
    print(f"- window_days: {args.days}")
    print(f"- include_tests: {args.include_tests}")
    print(f"- runs_total: {metrics['runs_total']}")
    print(f"- execute_total: {metrics['execute_total']}")
    print(f"- execute_verification_success_pct: {metrics['execute_verification_success_pct']}")
    print(f"- approval_failures_total: {metrics['approval_failures_total']}")
    print(f"- risk_policy_blocks_total: {metrics['risk_policy_blocks_total']}")
    print(f"- mttt_minutes: {metrics['mttt_minutes']} (n={metrics['mttt_sample_size']})")
    print(f"- mttvf_minutes: {metrics['mttvf_minutes']} (n={metrics['mttvf_sample_size']})")
    print(f"- snapshot: {paths['snapshot']}")
    print(f"- latest: {paths['latest']}")


if __name__ == "__main__":
    main()
