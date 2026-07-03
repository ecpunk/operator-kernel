import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat()


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _run(args: list[str]) -> Dict[str, Any]:
    env = dict(os.environ)
    src_path = "/opt/stack/services/projects/operator-kernel/src"
    existing = str(env.get("PYTHONPATH", "")).strip()
    env["PYTHONPATH"] = f"{src_path}:{existing}" if existing else src_path
    proc = subprocess.run(args, text=True, capture_output=True, check=False, env=env)
    return {
        "command": " ".join(args),
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def _parse_iso(ts: str) -> datetime:
    value = datetime.fromisoformat(ts)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _readiness_freshness(root: Path, max_staleness_minutes: int) -> Dict[str, Any]:
    readiness_path = root / "artifacts" / "stage3" / "stage3_readiness_latest.json"
    readiness = _load_json(readiness_path, {})
    generated_at = str(readiness.get("generated_at", "")).strip()
    if not generated_at:
        return {
            "path": str(readiness_path),
            "fresh": False,
            "age_minutes": None,
            "reason": "missing generated_at",
        }

    try:
        age = (_utc_now() - _parse_iso(generated_at)).total_seconds() / 60.0
    except Exception:
        return {
            "path": str(readiness_path),
            "fresh": False,
            "age_minutes": None,
            "reason": "invalid generated_at format",
        }

    return {
        "path": str(readiness_path),
        "fresh": age <= float(max_staleness_minutes),
        "age_minutes": round(age, 2),
        "reason": "ok" if age <= float(max_staleness_minutes) else "stale",
    }


def _timer_state(timer_name: str) -> Dict[str, Any]:
    enabled = _run(["systemctl", "is-enabled", timer_name])
    active = _run(["systemctl", "is-active", timer_name])
    details = _run(
        [
            "systemctl",
            "show",
            timer_name,
            "--property=NextElapseUSecRealtime,LastTriggerUSecRealtime,UnitFileState,ActiveState",
        ]
    )
    raw = str(details.get("stdout", ""))
    kv: Dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        kv[key.strip()] = value.strip()

    return {
        "enabled": enabled,
        "active": active,
        "details": details,
        "properties": kv,
    }


def _write_artifacts(root: Path, payload: Dict[str, Any]) -> Dict[str, str]:
    out_dir = root / "artifacts" / "stage3"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = out_dir / f"stage3_scheduler_health_{stamp}.json"
    latest = out_dir / "stage3_scheduler_health_latest.json"
    text = json.dumps(payload, indent=2) + "\n"
    snapshot.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return {"snapshot": str(snapshot), "latest": str(latest)}


def _export_dashboard(payload: Dict[str, Any]) -> Path:
    dashboard_path = Path("/opt/stack/services/apps/dashboard/status.json")
    dashboard = _load_json(dashboard_path, {})
    dashboard["operator_kernel_stage3_scheduler"] = payload
    dashboard_path.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")
    return dashboard_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--timer-name",
        default="operator-kernel-stage3-ops.timer",
        help="Systemd timer unit name",
    )
    parser.add_argument(
        "--max-staleness-minutes",
        type=int,
        default=90,
        help="Maximum allowed age for stage3_readiness_latest artifact",
    )
    parser.add_argument("--export-dashboard", action="store_true", help="Export scheduler health to dashboard")
    parser.add_argument("--enforce-healthy", action="store_true", help="Return non-zero if status is not healthy")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    timer = _timer_state(str(args.timer_name))
    readiness = _readiness_freshness(root, int(args.max_staleness_minutes))

    timer_enabled = int(timer["enabled"].get("exit_code", 1)) == 0 and timer["enabled"].get("stdout", "") == "enabled"
    timer_active = int(timer["active"].get("exit_code", 1)) == 0 and timer["active"].get("stdout", "") == "active"
    readiness_fresh = bool(readiness.get("fresh", False))

    alerts = []
    if not timer_enabled:
        alerts.append("timer_not_enabled")
    if not timer_active:
        alerts.append("timer_not_active")
    if not readiness_fresh:
        alerts.append("readiness_artifact_stale")

    payload = {
        "generated_at": _utc_now_iso(),
        "status": "healthy" if not alerts else "unhealthy",
        "timer_name": str(args.timer_name),
        "checks": {
            "timer_enabled": timer_enabled,
            "timer_active": timer_active,
            "readiness_artifact_fresh": readiness_fresh,
        },
        "alerts": alerts,
        "timer": timer,
        "readiness": readiness,
        "policy": {
            "max_staleness_minutes": int(args.max_staleness_minutes),
        },
    }
    paths = _write_artifacts(root, payload)

    print("Operator Kernel Stage 3 Scheduler Health")
    print(f"- status: {payload['status']}")
    print(f"- timer_enabled: {timer_enabled}")
    print(f"- timer_active: {timer_active}")
    print(f"- readiness_artifact_fresh: {readiness_fresh}")
    if alerts:
        print(f"- alerts: {', '.join(alerts)}")
    print(f"- snapshot: {paths['snapshot']}")
    print(f"- latest: {paths['latest']}")

    if args.export_dashboard:
        dashboard = _export_dashboard(payload)
        print(f"- dashboard_export: {dashboard}")

    if args.enforce_healthy and payload["status"] != "healthy":
        raise SystemExit(2)


if __name__ == "__main__":
    main()