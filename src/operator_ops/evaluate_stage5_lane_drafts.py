import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List

from operator_kernel.lane_catalog import resolve_action_id
from operator_runtime.context_loader import build_preflight_context, preflight_ok
from operator_runtime.inspect_engine import generate_inspect_report


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_tasks() -> List[str]:
    return [
        "investigate jellyfin startup failures and suggest a safe lane",
        "diagnose postgres vacuum lag and propose non-disruptive recovery",
        "stabilize nginx cert renewal workflow and add verification lane",
        "evaluate redis memory pressure and recommend safe mitigation lane",
        "review backup checksum drift and propose a repair lane",
        "analyze home automation event queue delays and suggest lane",
        "assess kafka consumer lag spikes and draft incident lane",
        "triage vpn reconnect instability and propose maintenance lane",
        "inspect ldap auth timeout trend and suggest remediation lane",
        "investigate docker volume permission errors and draft lane",
        "check ci runner exhaustion pattern and propose safe response lane",
        "review filesystem inode pressure and suggest recovery lane",
    ]


def _load_tasks(path: Path) -> List[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [str(x).strip() for x in payload if str(x).strip()]
    if isinstance(payload, dict) and isinstance(payload.get("tasks"), list):
        return [str(x).strip() for x in payload["tasks"] if str(x).strip()]
    raise ValueError("task file must be a JSON array or object with 'tasks' array")


def _is_placeholder(value: str) -> bool:
    lowered = str(value).strip().lower()
    return lowered in {"", "candidate_service", "myservice", "http://127.0.0.1:8080"}


def _summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_strategy: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "scores": [],
        "wins": 0,
        "schema_valid": 0,
        "service_non_placeholder": 0,
        "endpoint_non_placeholder": 0,
    })
    recommended_counts: Counter[str] = Counter()

    for row in results:
        drafts = list(row.get("lane_drafts", []))
        for d in drafts:
            strategy = str(d.get("strategy", ""))
            if not strategy:
                continue
            by_strategy[strategy]["scores"].append(float(d.get("score", 0.0) or 0.0))
            if bool(d.get("schema_valid", False)):
                by_strategy[strategy]["schema_valid"] += 1
            if not _is_placeholder(str(d.get("service", ""))):
                by_strategy[strategy]["service_non_placeholder"] += 1
            if not _is_placeholder(str(d.get("endpoint", ""))):
                by_strategy[strategy]["endpoint_non_placeholder"] += 1
            if bool(d.get("recommended", False)):
                by_strategy[strategy]["wins"] += 1
                recommended_counts[strategy] += 1

    strategy_summary: Dict[str, Any] = {}
    total = max(1, len(results))
    for strategy, metrics in sorted(by_strategy.items()):
        scores = list(metrics["scores"])
        strategy_summary[strategy] = {
            "avg_score": round(mean(scores), 2) if scores else 0.0,
            "wins": int(metrics["wins"]),
            "win_rate": round(float(metrics["wins"]) / total, 4),
            "schema_valid_rate": round(float(metrics["schema_valid"]) / total, 4),
            "service_specific_rate": round(float(metrics["service_non_placeholder"]) / total, 4),
            "endpoint_specific_rate": round(float(metrics["endpoint_non_placeholder"]) / total, 4),
        }

    top_strategy = ""
    if recommended_counts:
        top_strategy = recommended_counts.most_common(1)[0][0]

    multi_strategy_value = {
        "enabled": True,
        "strategy_count": len(strategy_summary),
        "winner_diversity": len([k for k, v in recommended_counts.items() if int(v) > 0]),
        "note": (
            "Running multiple draft strategies in parallel improves tuning quality by "
            "capturing both successful and failed approaches for comparison."
        ),
    }

    return {
        "task_count": len(results),
        "top_strategy": top_strategy,
        "recommended_counts": dict(recommended_counts),
        "strategy_summary": strategy_summary,
        "method_learning": multi_strategy_value,
    }


def _write_artifact(root: Path, payload: Dict[str, Any]) -> Path:
    out_dir = root / "artifacts" / "stage5"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    path = out_dir / f"stage5_lane_draft_eval_{stamp}.json"
    latest = out_dir / "stage5_lane_draft_eval_latest.json"
    text = json.dumps(payload, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tasks-json",
        default="",
        help="Optional JSON file with array of task strings or {\"tasks\": [...]}.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit on number of tasks from input list.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    tasks = _default_tasks()
    if str(args.tasks_json).strip():
        tasks = _load_tasks(Path(str(args.tasks_json).strip()))

    if args.limit and args.limit > 0:
        tasks = tasks[: int(args.limit)]

    context_path = root / "config" / "context_sources.json"
    lane_catalog_path = root / "config" / "lane_catalog.json"
    lanes_dir = root / "config" / "lanes"

    context_rows = build_preflight_context(context_path)
    if not preflight_ok(context_rows):
        raise SystemExit("preflight failed: required context missing")

    results: List[Dict[str, Any]] = []
    skipped_lane_matched = 0

    for task in tasks:
        action_id = resolve_action_id(task, lane_catalog_path)
        report, artifact_path = generate_inspect_report(
            root=root,
            task=task,
            action_id=action_id,
            lanes_dir=lanes_dir,
            context_rows=context_rows,
            usage_hint=None,
        )

        if str(report.get("branch", "")) != "open_ended":
            skipped_lane_matched += 1
            continue

        results.append(
            {
                "task": task,
                "inspect_artifact": str(artifact_path),
                "lane_drafts": list(report.get("lane_drafts", [])),
            }
        )

    summary = _summarize(results)
    payload = {
        "version": 1,
        "generated_at": _utc_now_iso(),
        "tasks_total": len(tasks),
        "tasks_evaluated_open_ended": len(results),
        "tasks_skipped_lane_matched": skipped_lane_matched,
        "summary": summary,
        "results": results,
    }

    out_path = _write_artifact(root, payload)

    print("Operator Kernel Stage 5 Lane Draft Evaluation")
    print(f"- tasks_total: {len(tasks)}")
    print(f"- tasks_evaluated_open_ended: {len(results)}")
    print(f"- tasks_skipped_lane_matched: {skipped_lane_matched}")
    print(f"- top_strategy: {summary.get('top_strategy', '') or 'none'}")
    method_learning = dict(summary.get("method_learning", {}))
    if method_learning.get("enabled"):
        print(
            "- method_learning: "
            f"multi_strategy enabled with {method_learning.get('strategy_count', 0)} strategies; "
            f"winner_diversity={method_learning.get('winner_diversity', 0)}"
        )

    for strategy, row in sorted(summary.get("strategy_summary", {}).items()):
        print(
            f"- {strategy}: wins={row['wins']} win_rate={row['win_rate']} "
            f"avg_score={row['avg_score']} schema_valid_rate={row['schema_valid_rate']} "
            f"service_specific_rate={row['service_specific_rate']} "
            f"endpoint_specific_rate={row['endpoint_specific_rate']}"
        )

    print(f"- artifact: {out_path}")
    print(f"- latest: {root / 'artifacts' / 'stage5' / 'stage5_lane_draft_eval_latest.json'}")


if __name__ == "__main__":
    main()
