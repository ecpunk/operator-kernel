import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat()


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_iso(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    k = (len(ordered) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    frac = k - lo
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * frac)


def _load_inspect_artifacts(root: Path, days: int) -> List[Dict[str, Any]]:
    inspect_dir = root / "artifacts" / "inspect"
    rows: List[Dict[str, Any]] = []

    if not inspect_dir.exists():
        return rows

    cutoff = _utc_now() - timedelta(days=days)
    for path in sorted(inspect_dir.glob("inspect_*.json")):
        if path.name == "inspect_latest.json":
            continue
        payload = _load_json(path, {})
        generated_at = str(payload.get("generated_at", "")).strip()
        if not generated_at:
            continue
        try:
            ts = _parse_iso(generated_at)
        except ValueError:
            continue
        if days > 0 and ts < cutoff:
            continue
        payload["_path"] = str(path)
        rows.append(payload)

    return rows


def _row_traceability(row: Dict[str, Any]) -> Dict[str, Any]:
    evidence_ids = {
        str(item.get("source_id", "")).strip()
        for item in row.get("evidence", [])
        if str(item.get("source_id", "")).strip()
    }
    hypotheses = list(row.get("hypotheses", []))
    total = len(hypotheses)
    cited = 0

    for hyp in hypotheses:
        srcs = [str(s).strip() for s in hyp.get("sources", []) if str(s).strip()]
        if not srcs:
            continue
        if all(s in evidence_ids for s in srcs):
            cited += 1

    ratio = (cited / total) if total else 0.0
    return {
        "hypotheses_total": total,
        "hypotheses_traceable": cited,
        "traceability_ratio": ratio,
    }


def _build_metrics(root: Path, days: int, sample_min: int) -> Dict[str, Any]:
    rows = _load_inspect_artifacts(root, days)
    open_ended = [r for r in rows if str(r.get("branch", "")) == "open_ended"]
    lane_matched = [r for r in rows if str(r.get("branch", "")) == "lane_matched"]
    provider_usage_rows = [
        r for r in open_ended if str(r.get("cost_source", "")) == "provider_usage"
    ]
    heuristic_rows = [r for r in open_ended if str(r.get("cost_source", "")) != "provider_usage"]

    token_values = [int(r.get("token_estimate", 0) or 0) for r in open_ended]
    cost_values = [float(r.get("estimated_cost_usd", 0.0) or 0.0) for r in open_ended]
    provider_cost_values = [
        float(r.get("estimated_cost_usd", 0.0) or 0.0) for r in provider_usage_rows
    ]

    trace_rows = [_row_traceability(r) for r in open_ended]
    total_hyp = sum(int(r["hypotheses_total"]) for r in trace_rows)
    total_traceable_hyp = sum(int(r["hypotheses_traceable"]) for r in trace_rows)
    traceability_ratio = (total_traceable_hyp / total_hyp) if total_hyp else 0.0

    zero_citation_runs = 0
    for r in open_ended:
        has_any_sources = any(bool(h.get("sources")) for h in r.get("hypotheses", []))
        if not has_any_sources:
            zero_citation_runs += 1

    avg_tokens = (sum(token_values) / len(token_values)) if token_values else 0.0
    avg_cost = (sum(cost_values) / len(cost_values)) if cost_values else 0.0

    gate = {
        "sample_target": int(sample_min),
        "open_ended_samples_collected": len(open_ended),
        "sample_target_met": len(open_ended) >= int(sample_min),
        "traceability_ratio": round(traceability_ratio, 4),
        "zero_citation_open_ended_runs": int(zero_citation_runs),
        "ready_to_finalize_thresholds": len(open_ended) >= int(sample_min),
    }

    recommended = {
        "quality_traceability_floor": round(max(0.95, traceability_ratio - 0.02), 4)
        if open_ended
        else 0.95,
        "cost_cap_usd_per_open_ended_inspect_p95": round(_percentile(cost_values, 0.95), 6),
        "cost_cap_usd_per_open_ended_inspect_p99": round(_percentile(cost_values, 0.99), 6),
        "token_cap_per_open_ended_inspect_p95": int(round(_percentile([float(v) for v in token_values], 0.95)))
        if token_values
        else 0,
    }

    return {
        "generated_at": _utc_now_iso(),
        "window_days": int(days),
        "counts": {
            "inspect_total": len(rows),
            "open_ended_total": len(open_ended),
            "lane_matched_total": len(lane_matched),
            "open_ended_provider_usage_total": len(provider_usage_rows),
            "open_ended_heuristic_total": len(heuristic_rows),
        },
        "open_ended_metrics": {
            "token_estimate_avg": round(avg_tokens, 2),
            "token_estimate_p95": int(round(_percentile([float(v) for v in token_values], 0.95)))
            if token_values
            else 0,
            "token_estimate_p99": int(round(_percentile([float(v) for v in token_values], 0.99)))
            if token_values
            else 0,
            "estimated_cost_usd_avg": round(avg_cost, 6),
            "estimated_cost_usd_p95": round(_percentile(cost_values, 0.95), 6),
            "estimated_cost_usd_p99": round(_percentile(cost_values, 0.99), 6),
            "provider_usage_estimated_cost_usd_p95": round(
                _percentile(provider_cost_values, 0.95), 6
            ),
            "provider_usage_estimated_cost_usd_p99": round(
                _percentile(provider_cost_values, 0.99), 6
            ),
            "hypotheses_total": int(total_hyp),
            "hypotheses_traceable_total": int(total_traceable_hyp),
            "traceability_ratio": round(traceability_ratio, 4),
            "zero_citation_runs": int(zero_citation_runs),
        },
        "gate_progress": gate,
        "recommended_thresholds": recommended,
    }


def _write_artifacts(root: Path, payload: Dict[str, Any]) -> Dict[str, str]:
    out_dir = root / "artifacts" / "stage4"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = out_dir / f"stage4_inspect_metrics_{stamp}.json"
    latest = out_dir / "stage4_inspect_metrics_latest.json"
    text = json.dumps(payload, indent=2) + "\n"
    snapshot.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return {"snapshot": str(snapshot), "latest": str(latest)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="Window in days; 0 includes all.")
    parser.add_argument(
        "--sample-min",
        type=int,
        default=50,
        help="Minimum open-ended inspect samples required before threshold finalization.",
    )
    parser.add_argument(
        "--enforce-sample-min",
        action="store_true",
        help="Return non-zero when open-ended sample target is not met.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    payload = _build_metrics(root, args.days, args.sample_min)
    paths = _write_artifacts(root, payload)

    gate = payload["gate_progress"]
    metrics = payload["open_ended_metrics"]
    rec = payload["recommended_thresholds"]

    print("Operator Kernel Stage 4 Inspect Metrics")
    print(f"- window_days: {payload['window_days']}")
    print(f"- inspect_total: {payload['counts']['inspect_total']}")
    print(f"- open_ended_total: {payload['counts']['open_ended_total']}")
    print(f"- lane_matched_total: {payload['counts']['lane_matched_total']}")
    print(
        "- open_ended_cost_source_mix: "
        f"provider_usage={payload['counts']['open_ended_provider_usage_total']}, "
        f"heuristic={payload['counts']['open_ended_heuristic_total']}"
    )
    print(f"- traceability_ratio: {metrics['traceability_ratio']}")
    print(f"- estimated_cost_usd_p95: {metrics['estimated_cost_usd_p95']}")
    print(f"- token_estimate_p95: {metrics['token_estimate_p95']}")
    print(f"- sample_target_met: {gate['sample_target_met']}")
    print(
        "- recommended_thresholds: "
        f"quality_traceability_floor={rec['quality_traceability_floor']}, "
        f"cost_cap_p95={rec['cost_cap_usd_per_open_ended_inspect_p95']}, "
        f"token_cap_p95={rec['token_cap_per_open_ended_inspect_p95']}"
    )
    print(f"- snapshot: {paths['snapshot']}")
    print(f"- latest: {paths['latest']}")

    if args.enforce_sample_min and not gate["sample_target_met"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
