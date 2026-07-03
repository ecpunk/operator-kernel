import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from operator_notify import notify_event


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_stage3_policy(root: Path) -> Dict[str, Any]:
    guardrails = _load_json(root / "config" / "guardrails.json", {})
    row = dict(guardrails.get("stage3_operations", {}))
    return {
        "allow_unattended_delta_apply": bool(row.get("allow_unattended_delta_apply", False)),
        "alert_retry_max_attempts": int(row.get("alert_retry_max_attempts", 3)),
        "alert_retry_backoff_seconds": int(row.get("alert_retry_backoff_seconds", 2)),
        "alert_dedupe_minutes": int(row.get("alert_dedupe_minutes", 30)),
    }


def _write_artifacts(root: Path, payload: Dict[str, Any]) -> Dict[str, str]:
    out_dir = root / "artifacts" / "stage3"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = out_dir / f"stage3_operations_{stamp}.json"
    latest = out_dir / "stage3_operations_latest.json"
    text = json.dumps(payload, indent=2) + "\n"
    snapshot.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return {"snapshot": str(snapshot), "latest": str(latest)}


def _write_alert_artifact(root: Path, payload: Dict[str, Any], reason: str, signature: str) -> Dict[str, str]:
    out_dir = root / "artifacts" / "stage3"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = out_dir / f"stage3_alert_{stamp}.json"
    latest = out_dir / "stage3_alert_latest.json"
    alert_payload = {
        "generated_at": _utc_now_iso(),
        "reason": reason,
        "signature": signature,
        "status": payload.get("status", "unknown"),
        "alerts": payload.get("alerts", []),
        "steps_executed": payload.get("steps_executed", 0),
        "steps_total": payload.get("steps_total", 0),
        "failed_commands": [
            row for row in payload.get("commands", []) if int(row.get("exit_code", 1)) != 0
        ],
    }
    text = json.dumps(alert_payload, indent=2) + "\n"
    snapshot.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return {"snapshot": str(snapshot), "latest": str(latest)}


def _should_dedupe_alert(root: Path, reason: str, signature: str, dedupe_minutes: int) -> bool:
    latest = _load_json(root / "artifacts" / "stage3" / "stage3_alert_latest.json", {})
    if not latest:
        return False
    if str(latest.get("reason", "")) != reason:
        return False
    if str(latest.get("signature", "")) != signature:
        return False

    generated_at = str(latest.get("generated_at", "")).strip()
    if not generated_at:
        return False
    try:
        ts = datetime.fromisoformat(generated_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except Exception:
        return False

    age_minutes = (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
    return age_minutes < float(dedupe_minutes)


def _notify(
    root: Path,
    event: str,
    status: str,
    message: str,
    severity: str = "info",
    milestone: str = "",
    details: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    try:
        return notify_event(
            root=root,
            source="operator_ops.run_stage3_operations",
            event=event,
            status=status,
            message=message,
            severity=severity,
            milestone=milestone,
            details=details or {},
        )
    except Exception:
        return {"enabled": False, "sent": 0, "results": []}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="Rolling window for reports and gates")
    parser.add_argument(
        "--delta-profile",
        default="none",
        choices=["none", "concept_only", "runbooks_only", "top3", "top5", "all"],
        help="Selection profile for learning delta apply step",
    )
    parser.add_argument("--apply-deltas", action="store_true", help="Apply selected learning deltas")
    parser.add_argument("--approval-token", default="", help="Approval token required when --apply-deltas")
    parser.add_argument(
        "--alert-webhook-url",
        default=os.environ.get("STAGE3_ALERT_WEBHOOK_URL", ""),
        help="Deprecated: retained for compatibility; use config/notify.json routes",
    )
    parser.add_argument(
        "--skip-batch2",
        action="store_true",
        help="Skip broad spectrum batch2 during orchestration",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    policy = _load_stage3_policy(root)
    unattended = bool(os.environ.get("INVOCATION_ID") or os.environ.get("SYSTEMD_EXEC_PID"))

    _notify(
        root,
        event="stage3_operations",
        status="start",
        message="stage3 operations run started",
        milestone="run_start",
        details={"days": int(args.days), "delta_profile": str(args.delta_profile)},
    )

    policy_block = False
    policy_block_reason = ""
    if args.apply_deltas and unattended and not policy["allow_unattended_delta_apply"]:
        policy_block = True
        policy_block_reason = "unattended_delta_apply_blocked_by_policy"

    commands: List[List[str]] = [
        [sys.executable, "src/operator_ops/report_learning_suggestions.py", "--days", str(args.days)],
        [sys.executable, "src/operator_ops/report_learning_deltas.py"],
    ]

    if args.apply_deltas or str(args.delta_profile) != "none":
        apply_cmd = [
            sys.executable,
            "src/operator_ops/apply_learning_deltas.py",
            "--profile",
            str(args.delta_profile),
        ]
        if args.apply_deltas:
            apply_cmd.extend(["--apply", "--approval-token", str(args.approval_token)])
        commands.append(apply_cmd)

    if not args.skip_batch2:
        commands.append([sys.executable, "src/operator_tests/broad_spectrum_tests_batch2.py"])

    commands.extend(
        [
            [sys.executable, "src/operator_tests/adapter_contract_tests.py", "--strict"],
            [sys.executable, "src/operator_tests/stage3_bootstrap_tests.py", "--strict"],
            [sys.executable, "src/operator_tests/scorecard_regression_tests.py", "--strict"],
            [
                sys.executable,
                "src/operator_ops/report_service_scorecards.py",
                "--days",
                str(args.days),
                "--export-dashboard",
                "--enforce-gate",
            ],
            [sys.executable, "src/operator_ops/run_stage2_soak_snapshot.py", "--days", str(args.days), "--enforce-exit"],
            [
                sys.executable,
                "src/operator_ops/report_stage3_readiness.py",
                "--export-dashboard",
                "--emit-ticket",
                "--enforce-ready",
            ],
            [
                sys.executable,
                "src/operator_ops/report_stage3_scheduler_health.py",
                "--export-dashboard",
                "--enforce-healthy",
                "--max-staleness-minutes",
                "90",
            ],
        ]
    )

    results: List[Dict[str, Any]] = []
    if policy_block:
        results.append(
            {
                "command": "policy_guard.unattended_delta_apply",
                "exit_code": 2,
                "stdout": "",
                "stderr": policy_block_reason,
            }
        )
    else:
        for cmd in commands:
            res = _run(root, cmd)
            results.append(res)
            _notify(
                root,
                event="stage3_operations",
                status="milestone",
                message="stage3 command completed",
                severity="info" if int(res.get("exit_code", 1)) == 0 else "warning",
                milestone="command_complete",
                details={
                    "command": str(res.get("command", "")),
                    "exit_code": int(res.get("exit_code", 1)),
                    "steps_executed": len(results),
                    "steps_total": len(commands),
                },
            )
            if res["exit_code"] != 0:
                break

    failed = [r for r in results if int(r.get("exit_code", 1)) != 0]
    payload = {
        "generated_at": _utc_now_iso(),
        "window_days": args.days,
        "apply_deltas": bool(args.apply_deltas),
        "delta_profile": str(args.delta_profile),
        "skip_batch2": bool(args.skip_batch2),
        "unattended_context": unattended,
        "stage3_policy": policy,
        "steps_total": len(commands),
        "steps_executed": len(results),
        "status": "pass" if not failed else "fail",
        "alerts": [f"command_failed: {f.get('command', '')}" for f in failed],
        "commands": results,
        "scheduler_health_latest": str(
            root / "artifacts" / "stage3" / "stage3_scheduler_health_latest.json"
        ),
    }

    reason = ""
    readiness_latest = _load_json(root / "artifacts" / "stage3" / "stage3_readiness_latest.json", {})
    readiness_status = str(readiness_latest.get("status", "")).strip()
    if failed:
        reason = "command_failed"
    elif readiness_status and readiness_status != "ready":
        reason = "readiness_not_ready"

    alert_paths: Dict[str, str] = {}
    webhook_status = ""
    webhook_deduped = False
    if reason:
        failed_sig = "|".join(
            sorted([str(r.get("command", "")) for r in failed])
        )[:400]
        signature = f"{reason}:{failed_sig}"
        dedupe_hit = _should_dedupe_alert(
            root,
            reason=reason,
            signature=signature,
            dedupe_minutes=int(policy["alert_dedupe_minutes"]),
        )
        alert_paths = _write_alert_artifact(root, payload, reason, signature)

        _run(root, [sys.executable, "src/operator_ops/manage_stage3_incident.py", "--action", "open_from_latest"])

        if dedupe_hit:
            webhook_deduped = True
            webhook_status = "deduped"
        else:
            notify_result = _notify(
                root,
                event="stage3_operations",
                status="failure",
                message=f"stage3 alert triggered: {reason}",
                severity="error",
                milestone="alert_triggered",
                details={
                    "reason": reason,
                    "alerts": payload.get("alerts", []),
                    "alert_artifact": alert_paths.get("snapshot", ""),
                    "signature": signature,
                    "legacy_alert_webhook_arg_set": bool(str(args.alert_webhook_url).strip()),
                },
            )
            webhook_status = f"notify_routes_sent={int(notify_result.get('sent', 0))}"

    if alert_paths:
        payload["alert_artifacts"] = alert_paths
    if webhook_status:
        payload["webhook_status"] = webhook_status
    if webhook_deduped:
        payload["webhook_deduped"] = True

    paths = _write_artifacts(root, payload)

    print("Operator Kernel Stage 3 Operations")
    print(f"- status: {payload['status']}")
    print(f"- steps_executed: {payload['steps_executed']}/{payload['steps_total']}")
    print(f"- delta_profile: {payload['delta_profile']}")
    print(f"- apply_deltas: {payload['apply_deltas']}")
    if payload["alerts"]:
        print(f"- alerts: {' | '.join(payload['alerts'])}")
    if alert_paths:
        print(f"- alert_snapshot: {alert_paths.get('snapshot', '')}")
    if webhook_status:
        print(f"- webhook_status: {webhook_status}")
    print(f"- snapshot: {paths['snapshot']}")
    print(f"- latest: {paths['latest']}")

    _notify(
        root,
        event="stage3_operations",
        status="complete" if not failed else "failure",
        message="stage3 operations run finished",
        severity="success" if not failed else "error",
        milestone="run_complete",
        details={
            "status": payload.get("status", "unknown"),
            "steps_executed": payload.get("steps_executed", 0),
            "steps_total": payload.get("steps_total", 0),
            "snapshot": paths.get("snapshot", ""),
        },
    )

    if failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()