import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _load_run_index(root: Path) -> List[Dict[str, Any]]:
    path = root / "artifacts" / "index" / "run_index.json"
    payload = _load_json(path, {"version": 1, "updated_at": "", "runs": []})
    return list(payload.get("runs", []))


def _load_archive_index(root: Path) -> List[Dict[str, Any]]:
    path = root / "artifacts" / "archive" / "archive_index.json"
    payload = _load_json(path, {"version": 1, "updated_at": "", "records": []})
    return list(payload.get("records", []))


def _top(counter: Counter, n: int) -> List[str]:
    items = counter.most_common(n)
    return [f"{key}:{value}" for key, value in items]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="Time window in days. Use 0 for all.")
    parser.add_argument("--top", type=int, default=5, help="Top-N rows for grouped summaries.")
    parser.add_argument("--recent", type=int, default=10, help="Show N most recent run records.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    runs = _load_run_index(root)
    archives = _load_archive_index(root)

    recent_runs: List[Dict[str, Any]] = []
    for run in runs:
        ts = str(run.get("timestamp", "")).strip()
        if not ts:
            continue
        try:
            if _is_recent(ts, args.days):
                recent_runs.append(run)
        except ValueError:
            continue

    recent_archives: List[Dict[str, Any]] = []
    for rec in archives:
        ts = str(rec.get("archived_at", "")).strip()
        if not ts:
            continue
        try:
            if _is_recent(ts, args.days):
                recent_archives.append(rec)
        except ValueError:
            continue

    by_mode = Counter(str(r.get("mode", "unknown")) for r in recent_runs)
    by_status = Counter(str(r.get("status", "unknown")) for r in recent_runs)
    by_action = Counter(str(r.get("action_id", "none")) or "none" for r in recent_runs)
    by_verification = Counter(
        str(r.get("verification_status", "n/a")) for r in recent_runs if r.get("mode") == "execute"
    )
    by_task = Counter(str(r.get("task", "")) for r in recent_runs if str(r.get("task", "")).strip())

    archived_by_status = Counter(str(r.get("status", "unknown")) for r in recent_archives)
    archived_by_type = Counter(str(r.get("file_type", "unknown")) for r in recent_archives)
    archived_by_reason = Counter()
    for rec in recent_archives:
        for reason in rec.get("reasons", []):
            archived_by_reason[str(reason)] += 1

    rec_total = sum(int(r.get("recommendation_count", 0) or 0) for r in recent_runs)
    execute_count = sum(1 for r in recent_runs if str(r.get("mode", "")) == "execute")
    inspect_runs = [r for r in recent_runs if str(r.get("mode", "")) == "inspect"]
    inspect_provider_usage = sum(
        1 for r in inspect_runs if str(r.get("inspect_cost_source", "")) == "provider_usage"
    )
    inspect_heuristic = max(0, len(inspect_runs) - inspect_provider_usage)
    inspect_token_total = sum(
        int(r.get("inspect_token_estimate", 0) or 0) for r in inspect_runs
    )
    inspect_cost_total = sum(
        float(r.get("inspect_estimated_cost_usd", 0.0) or 0.0) for r in inspect_runs
    )
    inspect_open_ended = 0
    inspect_lane_matched = 0
    for run in inspect_runs:
        branch = str(run.get("inspect_branch", "")).strip()
        if branch == "open_ended":
            inspect_open_ended += 1
            continue
        if branch == "lane_matched":
            inspect_lane_matched += 1
            continue
        # Backward compatibility for inspect runs recorded before Stage 4 metrics fields.
        action = str(run.get("action_id", "")).strip()
        if action:
            inspect_lane_matched += 1
        else:
            inspect_open_ended += 1

    print("Run Health Report")
    print(f"- window_days: {args.days}")
    print(f"- runs_total: {len(recent_runs)}")
    print(f"- executes_total: {execute_count}")
    print(f"- recommendations_total: {rec_total}")
    print(f"- inspect_runs_total: {len(inspect_runs)}")
    print(f"- inspect_open_ended_total: {inspect_open_ended}")
    print(f"- inspect_lane_matched_total: {inspect_lane_matched}")
    print(
        "- inspect_cost_source_mix: "
        f"provider_usage={inspect_provider_usage}, heuristic={inspect_heuristic}"
    )
    print(f"- inspect_token_estimate_total: {inspect_token_total}")
    print(f"- inspect_estimated_cost_usd_total: {inspect_cost_total:.6f}")
    print(f"- mode_counts: {', '.join(_top(by_mode, args.top)) or 'none'}")
    print(f"- status_counts: {', '.join(_top(by_status, args.top)) or 'none'}")
    print(f"- verification_counts: {', '.join(_top(by_verification, args.top)) or 'none'}")
    print(f"- top_actions: {', '.join(_top(by_action, args.top)) or 'none'}")
    print(f"- top_tasks: {', '.join(_top(by_task, args.top)) or 'none'}")

    print("Archive Activity")
    print(f"- records_total: {len(recent_archives)}")
    print(
        f"- status_counts: {', '.join(_top(archived_by_status, args.top)) or 'none'}"
    )
    print(
        f"- file_type_counts: {', '.join(_top(archived_by_type, args.top)) or 'none'}"
    )
    print(
        f"- reason_counts: {', '.join(_top(archived_by_reason, args.top)) or 'none'}"
    )

    if args.recent > 0:
        print("Recent Runs")
        ordered_runs = sorted(
            recent_runs,
            key=lambda r: str(r.get("timestamp", "")),
            reverse=True,
        )
        for run in ordered_runs[: args.recent]:
            print(
                "- "
                f"{run.get('timestamp','')} | {run.get('mode','')} | {run.get('status','')} | "
                f"plan={run.get('plan_id','')} | action={run.get('action_id','')}"
            )


if __name__ == "__main__":
    main()
