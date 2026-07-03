import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from operator_kernel.schema_validator import validate_runtime_configs
from operator_notify import notify_event


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat()


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _run(root: Path, args: List[str]) -> Dict[str, Any]:
    env = dict(os.environ)
    src_path = str(root / "src")
    existing = str(env.get("PYTHONPATH", "")).strip()
    env["PYTHONPATH"] = f"{src_path}:{existing}" if existing else src_path
    proc = subprocess.run(
        args,
        cwd=str(root),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    return {
        "command": " ".join(args),
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def _load_guardrails_stage2(root: Path) -> Dict[str, Any]:
    payload = _load_json(root / "config" / "guardrails.json", {})
    stage2 = dict(payload.get("stage2", {}))
    stage2.setdefault("soak_required", True)
    stage2.setdefault("soak_min_snapshots", 3)
    stage2.setdefault("soak_window_hours", 24)
    exit_req = dict(stage2.get("exit_requirements", {}))
    exit_req.setdefault("require_stage_scorecard_gate_pass", True)
    exit_req.setdefault("require_service_scorecard_gate_pass", True)
    exit_req.setdefault("require_migration_policy_validation", True)
    stage2["exit_requirements"] = exit_req
    return stage2


def _recent_soak_snapshots(soak_dir: Path, window_hours: int) -> List[Path]:
    if not soak_dir.exists():
        return []
    cutoff = _utc_now() - timedelta(hours=window_hours)
    rows: List[Path] = []
    for path in sorted(soak_dir.glob("stage2_soak_snapshot_*.json")):
        try:
            payload = _load_json(path, {})
            ts = datetime.fromisoformat(str(payload.get("generated_at", "")))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                rows.append(path)
        except Exception:
            continue
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="Rolling window for generated reports")
    parser.add_argument(
        "--enforce-exit",
        action="store_true",
        help="Return non-zero when Stage 2 exit requirements are not satisfied",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    try:
        notify_event(
            root=root,
            source="operator_ops.run_stage2_soak_snapshot",
            event="stage2_soak_snapshot",
            status="start",
            message="stage2 soak snapshot started",
            milestone="run_start",
            details={"days": int(args.days), "enforce_exit": bool(args.enforce_exit)},
        )
    except Exception:
        pass

    stage2 = _load_guardrails_stage2(root)

    schema_errors = validate_runtime_configs(root)

    commands = [
        _run(root, [sys.executable, "src/operator_ops/report_scorecard.py", "--days", str(args.days), "--export-dashboard"]),
        _run(
            root,
            [
                sys.executable,
                "src/operator_ops/report_service_scorecards.py",
                "--days",
                str(args.days),
                "--export-dashboard",
            ],
        ),
        _run(root, [sys.executable, "src/operator_ops/report_trends.py", "--days", str(args.days)]),
        _run(root, [sys.executable, "src/operator_ops/report_learning_suggestions.py", "--days", str(args.days)]),
        _run(root, [sys.executable, "src/operator_ops/report_learning_deltas.py"]),
    ]

    for idx, row in enumerate(commands, start=1):
        try:
            notify_event(
                root=root,
                source="operator_ops.run_stage2_soak_snapshot",
                event="stage2_soak_snapshot",
                status="milestone",
                message="stage2 snapshot command completed",
                severity="info" if int(row.get("exit_code", 1)) == 0 else "warning",
                milestone="command_complete",
                details={
                    "step": idx,
                    "total": len(commands),
                    "command": str(row.get("command", "")),
                    "exit_code": int(row.get("exit_code", 1)),
                },
            )
        except Exception:
            pass

    stage_scorecard = _load_json(root / "artifacts" / "scorecards" / "stage_scorecard_latest.json", {})
    service_scorecard = _load_json(
        root / "artifacts" / "service_scorecards" / "service_scorecards_latest.json", {}
    )
    trend_report = _load_json(root / "artifacts" / "trends" / "trend_report_latest.json", {})
    learning_suggestions = _load_json(
        root / "artifacts" / "learning" / "learning_suggestions_latest.json", {}
    )
    learning_deltas = _load_json(root / "artifacts" / "learning" / "learning_deltas_latest.json", {})

    stage_gate_pass = str(stage_scorecard.get("stage_gate", {}).get("status", "")) == "pass"
    service_gate_pass = str(service_scorecard.get("stage_gate", {}).get("status", "")) == "pass"
    migration_ok = len(schema_errors) == 0

    soak_dir = root / "artifacts" / "soak"
    soak_dir.mkdir(parents=True, exist_ok=True)

    tmp_payload = {
        "generated_at": _utc_now_iso(),
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = soak_dir / f"stage2_soak_snapshot_{stamp}.json"

    history = _recent_soak_snapshots(soak_dir, int(stage2.get("soak_window_hours", 24)))
    snapshot_count_within_window = len(history) + 1

    requirements = stage2.get("exit_requirements", {})
    unmet: List[str] = []
    if requirements.get("require_stage_scorecard_gate_pass", True) and not stage_gate_pass:
        unmet.append("stage_scorecard_gate")
    if requirements.get("require_service_scorecard_gate_pass", True) and not service_gate_pass:
        unmet.append("service_scorecard_gate")
    if requirements.get("require_migration_policy_validation", True) and not migration_ok:
        unmet.append("migration_policy_validation")
    if bool(stage2.get("soak_required", True)):
        if snapshot_count_within_window < int(stage2.get("soak_min_snapshots", 3)):
            unmet.append("soak_snapshot_count")

    payload = {
        "generated_at": _utc_now_iso(),
        "window_days": args.days,
        "commands": commands,
        "stage2_policy": stage2,
        "checks": {
            "stage_scorecard_gate_pass": stage_gate_pass,
            "service_scorecard_gate_pass": service_gate_pass,
            "migration_policy_validation_pass": migration_ok,
            "migration_policy_errors": schema_errors,
            "soak_snapshot_count_within_window": snapshot_count_within_window,
            "soak_window_hours": int(stage2.get("soak_window_hours", 24)),
        },
        "linked_artifacts": {
            "stage_scorecard_latest": str(root / "artifacts" / "scorecards" / "stage_scorecard_latest.json"),
            "service_scorecard_latest": str(
                root / "artifacts" / "service_scorecards" / "service_scorecards_latest.json"
            ),
            "trend_report_latest": str(root / "artifacts" / "trends" / "trend_report_latest.json"),
            "learning_suggestions_latest": str(
                root / "artifacts" / "learning" / "learning_suggestions_latest.json"
            ),
            "learning_deltas_latest": str(root / "artifacts" / "learning" / "learning_deltas_latest.json"),
        },
        "learning": {
            "review_only": bool(learning_suggestions.get("policy", {}).get("review_only", True)),
            "suggestions_total": int(learning_suggestions.get("metrics", {}).get("suggestions_total", 0)),
            "delta_targets_total": int(learning_deltas.get("metrics", {}).get("targets_total", 0)),
        },
        "exit_gate": {
            "status": "pass" if len(unmet) == 0 else "block",
            "unmet": unmet,
        },
    }

    snapshot_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    latest_path = soak_dir / "stage2_soak_snapshot_latest.json"
    latest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print("Operator Kernel Stage 2 Soak Snapshot")
    print(f"- window_days: {args.days}")
    print(f"- stage_scorecard_gate_pass: {stage_gate_pass}")
    print(f"- service_scorecard_gate_pass: {service_gate_pass}")
    print(f"- migration_policy_validation_pass: {migration_ok}")
    print(f"- soak_snapshot_count_within_window: {snapshot_count_within_window}")
    print(f"- exit_gate_status: {payload['exit_gate']['status']}")
    if unmet:
        print(f"- exit_gate_unmet: {', '.join(unmet)}")
    print(f"- snapshot: {snapshot_path}")
    print(f"- latest: {latest_path}")

    try:
        notify_event(
            root=root,
            source="operator_ops.run_stage2_soak_snapshot",
            event="stage2_soak_snapshot",
            status="complete" if payload["exit_gate"]["status"] == "pass" else "failure",
            message="stage2 soak snapshot completed",
            severity="success" if payload["exit_gate"]["status"] == "pass" else "warning",
            milestone="exit_gate_evaluated",
            details={
                "exit_gate_status": payload["exit_gate"]["status"],
                "exit_gate_unmet": list(payload["exit_gate"].get("unmet", [])),
                "snapshot": str(snapshot_path),
            },
        )
    except Exception:
        pass

    if args.enforce_exit and payload["exit_gate"]["status"] == "block":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
