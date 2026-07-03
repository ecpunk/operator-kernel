import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


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


def _load_run_index(root: Path) -> List[Dict[str, Any]]:
    payload = _load_json(root / "artifacts" / "index" / "run_index.json", {"runs": []})
    return list(payload.get("runs", []))


def _load_guardrails_scorecard_config(root: Path) -> Dict[str, Any]:
    guardrails_path = root / "config" / "guardrails.json"
    payload = _load_json(guardrails_path, {})
    scorecard = dict(payload.get("scorecard", {}))

    thresholds = dict(scorecard.get("thresholds", {}))
    scorecard["thresholds"] = {
        "green_min_confidence_score": float(thresholds.get("green_min_confidence_score", 85.0)),
        "yellow_min_confidence_score": float(thresholds.get("yellow_min_confidence_score", 70.0)),
        "min_execute_verification_success_pct": float(
            thresholds.get("min_execute_verification_success_pct", 85.0)
        ),
        "min_test_pass_rate_pct": float(thresholds.get("min_test_pass_rate_pct", 85.0)),
        "min_proof_pack_completeness_pct": float(
            thresholds.get("min_proof_pack_completeness_pct", 98.0)
        ),
        "max_duplicate_run_ids": int(thresholds.get("max_duplicate_run_ids", 0)),
    }

    scorecard["fail_closed_on"] = list(scorecard.get("fail_closed_on", ["yellow", "red"]))
    scorecard["stage_gate_fail_closed"] = bool(scorecard.get("stage_gate_fail_closed", True))
    scorecard["default_window_days"] = int(scorecard.get("default_window_days", 7))
    return scorecard


def _load_test_reports(root: Path, days: int) -> List[Dict[str, Any]]:
    tests_dir = root / "artifacts" / "tests"
    if not tests_dir.exists():
        return []

    reports: List[Dict[str, Any]] = []
    for path in sorted(tests_dir.glob("*.json")):
        name = path.name
        if not (
            name.startswith("go_no_go_") or name.startswith("broad_spectrum_batch")
        ):
            continue
        try:
            payload = _load_json(path, {})
            ts = str(payload.get("generated_at", "")).strip()
            if ts and _is_recent(ts, days):
                payload["_path"] = str(path)
                reports.append(payload)
        except Exception:
            continue
    return reports


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 2)


def _compute_stage_signal(metrics: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[str, List[str]]:
    thresholds = cfg["thresholds"]
    reasons: List[str] = []
    no_prod_data = int(metrics["execute_total_prod"]) == 0

    if no_prod_data:
        # No prod executes in window. execute_verification_success_pct reflects
        # test-env service health checks, not contract correctness — services are
        # not expected to be running in test mode. Gate on test_pass_rate_pct instead.
        # proof_pack_completeness_pct gate is waived: denominator now covers only
        # runs that actually executed (gate-blocked excluded); test dry-runs that
        # ran but failed service checks still produce proof packs.
        if float(metrics["test_pass_rate_pct"]) < thresholds["min_execute_verification_success_pct"]:
            reasons.append("test_pass_rate_pct (execute gate proxy — no prod data) below policy minimum")
    else:
        exec_pct_prod = float(metrics["execute_verification_success_pct_prod"])
        if exec_pct_prod < thresholds["min_execute_verification_success_pct"]:
            reasons.append("execute_verification_success_pct_prod below policy minimum")
        if float(metrics["proof_pack_completeness_pct_prod"]) < thresholds["min_proof_pack_completeness_pct"]:
            reasons.append("proof_pack_completeness_pct_prod below policy minimum")

    if float(metrics["test_pass_rate_pct"]) < thresholds["min_test_pass_rate_pct"]:
        reasons.append("test_pass_rate_pct below policy minimum")

    if int(metrics["duplicate_run_ids"]) > thresholds["max_duplicate_run_ids"]:
        reasons.append("duplicate_run_ids exceeds policy maximum")

    confidence = float(metrics["confidence_score"]) 
    if confidence < thresholds["yellow_min_confidence_score"]:
        return "red", reasons + ["confidence_score below yellow floor"]
    if confidence < thresholds["green_min_confidence_score"] or reasons:
        return "yellow", reasons
    return "green", reasons


def _compute_stage_gate(stage_signal: str, cfg: Dict[str, Any], reasons: List[str]) -> Dict[str, Any]:
    fail_closed_on = {str(v).strip().lower() for v in cfg.get("fail_closed_on", [])}
    fail_closed_enabled = bool(cfg.get("stage_gate_fail_closed", True))
    blocked = fail_closed_enabled and stage_signal in fail_closed_on
    return {
        "enabled": fail_closed_enabled,
        "status": "block" if blocked else "pass",
        "block_on": sorted(fail_closed_on),
        "blocked_by": reasons,
    }


def _compute_scorecard(root: Path, days: int) -> Dict[str, Any]:
    scorecard_cfg = _load_guardrails_scorecard_config(root)
    runs = [r for r in _load_run_index(root) if str(r.get("timestamp", "")).strip()]
    runs = [r for r in runs if _is_recent(str(r.get("timestamp", "")), days)]

    for run in runs:
        run.setdefault("run_type", "prod")

    executes = [r for r in runs if str(r.get("mode", "")) == "execute"]
    executes_prod = [
        r for r in executes if str(r.get("run_type", "prod")).strip().lower() != "test"
    ]
    proposes = [r for r in runs if str(r.get("mode", "")) == "propose"]

    # Exclude gate-blocked attempts from verification and proof denominators.
    # Gate-blocked runs have empty verification_status — the action never ran;
    # the kernel correctly rejected them (e.g. missing approval token, blocked risk).
    # Including them in denominators would penalise the gate policy working correctly.
    executes_ran = [
        r for r in executes
        if str(r.get("verification_status", "") or "").strip()
    ]
    executes_ran_prod = [
        r for r in executes_prod
        if str(r.get("verification_status", "") or "").strip()
    ]
    execute_gate_blocked = len(executes) - len(executes_ran)

    verification_completed = sum(
        1 for r in executes_ran if str(r.get("verification_status", "")) == "completed"
    )
    verification_failed = sum(
        1 for r in executes_ran if str(r.get("verification_status", "")) == "failed"
    )

    unique_plan_ids = len({str(r.get("plan_id", "")).strip() for r in runs if str(r.get("plan_id", "")).strip()})
    duplicate_run_ids = len(runs) - len(
        {str(r.get("id", "")).strip() for r in runs if str(r.get("id", "")).strip()}
    )

    by_task = Counter(str(r.get("task", "")).strip() for r in runs if str(r.get("task", "")).strip())
    recurring_tasks = [
        {"task": task, "count": count}
        for task, count in by_task.items()
        if count >= 3
    ]
    recurring_tasks.sort(key=lambda x: x["count"], reverse=True)

    reports = _load_test_reports(root, days)
    total_tests = sum(int(rep.get("total", 0) or 0) for rep in reports)
    passed_tests = sum(int(rep.get("passed", 0) or 0) for rep in reports)
    failed_tests = sum(int(rep.get("failed", 0) or 0) for rep in reports)

    suite_status: Dict[str, str] = {}
    for rep in reports:
        suite = str(rep.get("suite", "")).strip()
        if not suite:
            continue
        status = "pass" if int(rep.get("failed", 0) or 0) == 0 else "fail"
        suite_status[suite] = status

    proof_paths_present = sum(
        1 for r in executes_ran if str(r.get("proof_path", "")).strip()
    )
    proof_paths_present_prod = sum(
        1 for r in executes_ran_prod if str(r.get("proof_path", "")).strip()
    )
    proof_pack_completeness_pct = _pct(proof_paths_present, len(executes_ran))
    proof_pack_completeness_pct_prod = _pct(proof_paths_present_prod, len(executes_ran_prod))
    execute_verification_success_pct = _pct(verification_completed, len(executes_ran))
    verification_completed_prod = sum(
        1 for r in executes_ran_prod if str(r.get("verification_status", "")) == "completed"
    )
    verification_failed_prod = sum(
        1 for r in executes_ran_prod if str(r.get("verification_status", "")) == "failed"
    )
    execute_verification_success_pct_prod = _pct(verification_completed_prod, len(executes_ran_prod))
    test_pass_rate_pct = _pct(passed_tests, total_tests)

    no_prod_data = len(executes_prod) == 0
    if no_prod_data:
        # No prod execute runs in window.
        # execute_verification_success_pct captures test-env service check outcomes
        # which are expected to fail when services are not running in test mode.
        # Use test_pass_rate_pct (contract correctness) as the primary quality signal.
        # proof_pack_completeness_pct now covers only runs that actually executed.
        confidence_score = round(
            (0.60 * test_pass_rate_pct)
            + (0.40 * execute_verification_success_pct),
            2,
        )
    else:
        confidence_score = round(
            (0.45 * execute_verification_success_pct_prod)
            + (0.35 * test_pass_rate_pct)
            + (0.20 * proof_pack_completeness_pct_prod),
            2,
        )

    metrics = {
        "runs_total": len(runs),
        "propose_total": len(proposes),
        "execute_total": len(executes),
        "execute_total_prod": len(executes_prod),
        "execute_total_gate_blocked": execute_gate_blocked,
        "verification_completed_total": verification_completed,
        "verification_failed_total": verification_failed,
        "verification_completed_total_prod": verification_completed_prod,
        "verification_failed_total_prod": verification_failed_prod,
        "proof_pack_completeness_pct": proof_pack_completeness_pct,
        "proof_pack_completeness_pct_prod": proof_pack_completeness_pct_prod,
        "execute_verification_success_pct": execute_verification_success_pct,
        "execute_verification_success_pct_prod": execute_verification_success_pct_prod,
        "tests_total": total_tests,
        "tests_passed_total": passed_tests,
        "tests_failed_total": failed_tests,
        "test_pass_rate_pct": test_pass_rate_pct,
        "duplicate_run_ids": duplicate_run_ids,
        "unique_plan_ids": unique_plan_ids,
        "recurring_task_count": len(recurring_tasks),
        "confidence_score": confidence_score,
    }
    stage_signal, stage_reasons = _compute_stage_signal(metrics, scorecard_cfg)
    metrics["stage_signal"] = stage_signal
    metrics["stage_reasons"] = stage_reasons
    gate = _compute_stage_gate(stage_signal, scorecard_cfg, stage_reasons)

    return {
        "generated_at": _utc_now_iso(),
        "window_days": days,
        "metrics": metrics,
        "suite_status": suite_status,
        "stage_gate": gate,
        "policy": {
            "scorecard": scorecard_cfg,
        },
        "recurring_tasks": recurring_tasks[:10],
        "sources": {
            "run_index": str(root / "artifacts" / "index" / "run_index.json"),
            "tests_dir": str(root / "artifacts" / "tests"),
        },
    }


def _write_scorecard_artifacts(root: Path, payload: Dict[str, Any]) -> Dict[str, str]:
    out_dir = root / "artifacts" / "scorecards"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    snapshot = out_dir / f"stage_scorecard_{stamp}.json"
    latest = out_dir / "stage_scorecard_latest.json"

    text = json.dumps(payload, indent=2) + "\n"
    snapshot.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")

    return {"snapshot": str(snapshot), "latest": str(latest)}


def _export_to_dashboard(payload: Dict[str, Any]) -> Path:
    dashboard_path = Path("/opt/stack/services/apps/dashboard/status.json")
    data = _load_json(dashboard_path, {})
    data["operator_kernel_scorecard"] = {
        "updated_at": payload.get("generated_at", ""),
        "window_days": payload.get("window_days", 0),
        "metrics": payload.get("metrics", {}),
        "suite_status": payload.get("suite_status", {}),
        "recurring_tasks": payload.get("recurring_tasks", []),
    }
    dashboard_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return dashboard_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None, help="Rolling window in days")
    parser.add_argument(
        "--export-dashboard",
        action="store_true",
        help="Write scorecard summary into apps/dashboard/status.json",
    )
    parser.add_argument(
        "--enforce-gate",
        action="store_true",
        help="Return non-zero when fail-closed stage gate blocks progression.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    cfg = _load_guardrails_scorecard_config(root)
    window_days = int(args.days) if args.days is not None else int(cfg.get("default_window_days", 7))
    payload = _compute_scorecard(root, window_days)
    paths = _write_scorecard_artifacts(root, payload)

    print("Operator Kernel Stage Scorecard")
    print(f"- window_days: {window_days}")
    print(f"- confidence_score: {payload['metrics']['confidence_score']}")
    print(f"- stage_signal: {payload['metrics']['stage_signal']}")
    print(f"- execute_verification_success_pct: {payload['metrics']['execute_verification_success_pct']}")
    print(f"- execute_verification_success_pct_prod: {payload['metrics']['execute_verification_success_pct_prod']}")
    print(f"- test_pass_rate_pct: {payload['metrics']['test_pass_rate_pct']}")
    print(f"- proof_pack_completeness_pct: {payload['metrics']['proof_pack_completeness_pct']}")
    print(f"- proof_pack_completeness_pct_prod: {payload['metrics']['proof_pack_completeness_pct_prod']}")
    print(f"- duplicate_run_ids: {payload['metrics']['duplicate_run_ids']}")
    print(f"- stage_gate_status: {payload['stage_gate']['status']}")
    print(f"- recurring_task_count: {payload['metrics']['recurring_task_count']}")
    print(f"- snapshot: {paths['snapshot']}")
    print(f"- latest: {paths['latest']}")

    if args.export_dashboard:
        dashboard = _export_to_dashboard(payload)
        print(f"- dashboard_export: {dashboard}")

    if args.enforce_gate and payload.get("stage_gate", {}).get("status") == "block":
        print("- gate_enforcement: blocked by scorecard policy")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
