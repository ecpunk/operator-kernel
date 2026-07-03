import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


PLACEHOLDER_SERVICES: Set[str] = {"", "candidate_service", "myservice"}
PLACEHOLDER_ENDPOINTS: Set[str] = {"", "http://127.0.0.1:8080"}
KNOWN_STRATEGIES = ("evidence_driven", "template_first", "nearest_lane")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        ts = datetime.fromisoformat(value)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except (ValueError, TypeError):
        return None


def _extract_strategy(stem: str) -> Optional[str]:
    for s in KNOWN_STRATEGIES:
        if stem.endswith(f"_{s}"):
            return s
    return None


def _extract_stamp(stem: str, strategy: str) -> str:
    # stem: lane_draft_{stamp}_{strategy}
    prefix = f"lane_draft_"
    suffix = f"_{strategy}"
    inner = stem[len(prefix):]
    if inner.endswith(strategy):
        inner = inner[: -(len(strategy) + 1)]
    return inner


def _is_placeholder_service(value: str) -> bool:
    return str(value).strip().lower() in PLACEHOLDER_SERVICES


def _is_placeholder_endpoint(value: str) -> bool:
    return str(value).strip().lower() in PLACEHOLDER_ENDPOINTS


def _load_draft_artifacts(root: Path, days: int) -> List[Dict[str, Any]]:
    drafts_dir = root / "artifacts" / "lane_drafts"
    rows: List[Dict[str, Any]] = []

    if not drafts_dir.exists():
        return rows

    cutoff = _utc_now() - timedelta(days=days) if days > 0 else None

    for path in sorted(drafts_dir.glob("lane_draft_[0-9]*.json")):
        strategy = _extract_strategy(path.stem)
        if strategy is None:
            continue

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        generated_at = str(payload.get("generated_at", "")).strip()
        if not generated_at:
            continue
        ts = _parse_iso(generated_at)
        if ts is None:
            continue
        if cutoff is not None and ts < cutoff:
            continue

        inspect_stamp = _extract_stamp(path.stem, strategy)
        payload["_path"] = str(path)
        payload["_inspect_stamp"] = inspect_stamp
        payload["_strategy_parsed"] = strategy
        rows.append(payload)

    return rows


def _load_guardrails(root: Path) -> Dict[str, Any]:
    path = root / "config" / "guardrails.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("stage5_drafts", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _load_latest_eval(root: Path) -> Optional[Dict[str, Any]]:
    path = root / "artifacts" / "stage5" / "stage5_lane_draft_eval_latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _build_metrics(root: Path, days: int, sample_min: int) -> Dict[str, Any]:
    rows = _load_draft_artifacts(root, days)
    policy = _load_guardrails(root)

    by_strategy: Dict[str, List[Dict[str, Any]]] = {s: [] for s in KNOWN_STRATEGIES}
    for r in rows:
        s = str(r.get("_strategy_parsed", r.get("strategy", "")))
        if s in by_strategy:
            by_strategy[s].append(r)

    unique_stamps: Set[str] = {str(r.get("_inspect_stamp", "")) for r in rows if r.get("_inspect_stamp")}

    strategy_summaries: Dict[str, Any] = {}
    for strategy, items in by_strategy.items():
        total = len(items)
        schema_valid = sum(1 for r in items if r.get("schema_valid") is True)
        service_non_placeholder = sum(
            1
            for r in items
            if not _is_placeholder_service(
                str(r.get("candidate_lane_spec", {}).get("service", ""))
            )
        )
        endpoint_non_placeholder = sum(
            1
            for r in items
            if not _is_placeholder_endpoint(
                str(r.get("candidate_lane_spec", {}).get("endpoint", ""))
            )
        )
        strategy_summaries[strategy] = {
            "total": total,
            "schema_valid": schema_valid,
            "schema_valid_rate": round(schema_valid / total, 4) if total else 0.0,
            "service_non_placeholder": service_non_placeholder,
            "service_specific_rate": round(service_non_placeholder / total, 4) if total else 0.0,
            "endpoint_non_placeholder": endpoint_non_placeholder,
            "endpoint_specific_rate": round(endpoint_non_placeholder / total, 4) if total else 0.0,
        }

    primary = str(policy.get("primary_strategy", "evidence_driven"))
    fallback = str(policy.get("fallback_strategy", "template_first"))
    min_score = int(policy.get("min_recommend_score", 60))
    require_schema_valid = bool(policy.get("require_schema_valid", True))
    require_non_placeholder_service = bool(policy.get("require_non_placeholder_service", True))

    primary_stats = strategy_summaries.get(primary, {})
    sample_target_met = len(unique_stamps) >= sample_min

    gate_progress = {
        "sample_min": sample_min,
        "unique_runs_collected": len(unique_stamps),
        "sample_target_met": sample_target_met,
        "primary_strategy": primary,
        "primary_schema_valid_rate": primary_stats.get("schema_valid_rate", 0.0),
        "primary_service_specific_rate": primary_stats.get("service_specific_rate", 0.0),
        "primary_endpoint_specific_rate": primary_stats.get("endpoint_specific_rate", 0.0),
        "ready_to_finalize_thresholds": sample_target_met,
    }

    latest_eval = _load_latest_eval(root)
    eval_summary: Optional[Dict[str, Any]] = None
    if latest_eval:
        ls = latest_eval.get("summary", {})
        eval_summary = {
            "generated_at": latest_eval.get("generated_at"),
            "task_count": ls.get("task_count"),
            "top_strategy": ls.get("top_strategy"),
            "evidence_driven_avg_score": ls.get("strategy_summary", {}).get(
                "evidence_driven", {}
            ).get("avg_score"),
            "evidence_driven_win_rate": ls.get("strategy_summary", {}).get(
                "evidence_driven", {}
            ).get("win_rate"),
            "evidence_driven_schema_valid_rate": ls.get("strategy_summary", {}).get(
                "evidence_driven", {}
            ).get("schema_valid_rate"),
        }

    result: Dict[str, Any] = {
        "generated_at": _utc_now_iso(),
        "window_days": days,
        "counts": {
            "total_draft_artifacts": len(rows),
            "unique_inspect_runs": len(unique_stamps),
        },
        "strategy_metrics": strategy_summaries,
        "active_policy": {
            "primary_strategy": primary,
            "fallback_strategy": fallback,
            "min_recommend_score": min_score,
            "require_schema_valid": require_schema_valid,
            "require_non_placeholder_service": require_non_placeholder_service,
        },
        "gate_progress": gate_progress,
    }
    if eval_summary:
        result["latest_evaluator_summary"] = eval_summary

    return result


def _write_artifacts(root: Path, payload: Dict[str, Any]) -> Dict[str, str]:
    out_dir = root / "artifacts" / "stage5"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_stamp = _stamp()
    snapshot = out_dir / f"stage5_draft_metrics_{ts_stamp}.json"
    latest = out_dir / "stage5_draft_metrics_latest.json"
    text = json.dumps(payload, indent=2) + "\n"
    snapshot.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return {"snapshot": str(snapshot), "latest": str(latest)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report Stage 5 lane draft quality metrics from production artifacts."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Lookback window in days. 0 includes all artifacts.",
    )
    parser.add_argument(
        "--sample-min",
        type=int,
        default=30,
        help="Minimum unique inspect runs required before entry gate thresholds can be finalized.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print report without writing artifacts.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent.parent
    metrics = _build_metrics(root, args.days, args.sample_min)

    if not args.no_write:
        paths = _write_artifacts(root, metrics)
        metrics["_artifacts"] = paths

    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
