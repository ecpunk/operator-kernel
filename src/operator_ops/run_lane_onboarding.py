import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse


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


def _extract(pattern: str, text: str) -> str:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _set_lifecycle_state(root: Path, action_id: str, state: str) -> Path:
    lifecycle_path = root / "config" / "lane_lifecycle.json"
    payload = _load_json(lifecycle_path, {"version": 1, "actions": {}})
    actions = dict(payload.get("actions", {}))
    actions[action_id] = {"state": state}
    payload["actions"] = actions
    _write_json(lifecycle_path, payload)
    return lifecycle_path


def _ensure_action_exists(root: Path, action_id: str) -> None:
    lane_path = root / "config" / "lanes" / f"{action_id}.json"
    if not lane_path.exists():
        raise FileNotFoundError(f"lane spec missing for action_id: {lane_path}")


def _probe_preflight(root: Path, action_id: str) -> List[str]:
    """Curl the lane endpoint and each verify_probe URL.
    Returns a list of failure messages; empty means all clear."""
    lane_path = root / "config" / "lanes" / f"{action_id}.json"
    spec = json.loads(lane_path.read_text(encoding="utf-8"))
    failures: List[str] = []

    endpoint = str(spec.get("endpoint", "")).strip()
    if not endpoint:
        failures.append("lane spec has no endpoint — cannot pre-flight")
        return failures

    # Use -k to tolerate self-signed certs (portainer), -s silent, -o /dev/null,
    # --max-time 8 so we fail fast rather than hanging the full onboarding run.
    curl_base = ["curl", "-s", "-k", "-o", "/dev/null", "-w", "%{http_code}",
                 "--max-time", "8", "--connect-timeout", "5"]

    def _try(label: str, url: str) -> None:
        try:
            result = subprocess.run(
                curl_base + [url],
                capture_output=True, text=True, check=False, timeout=12
            )
            code = result.stdout.strip()
            # Any HTTP response (even 4xx/5xx) means the host is reachable.
            # 000 means curl could not connect at all.
            if code == "000" or not code:
                failures.append(f"{label}: host unreachable (curl exit={result.returncode}, url={url})")
        except subprocess.TimeoutExpired:
            failures.append(f"{label}: timed out connecting to {url}")
        except Exception as exc:
            failures.append(f"{label}: pre-flight error: {exc}")

    _try("endpoint", endpoint)

    for probe in spec.get("verify_probes", []):
        cmd = str(probe.get("command", ""))
        # Extract the URL from the probe command (first http/https token)
        m = re.search(r'(https?://[^\s|<>"]+)', cmd)
        if m:
            probe_url = m.group(1)
            _try(f"probe '{probe.get('name', '?')}'", probe_url)

    return failures


def _propose(root: Path, task: str, channel: str, run_type: str) -> Tuple[str, str, str, Dict[str, Any]]:
    res = _run(
        root,
        [
            sys.executable,
            "src/operator_runtime/main.py",
            "--run-type",
            run_type,
            "--mode",
            "propose",
            "--channel",
            channel,
            "--task",
            task,
        ],
    )
    if res["exit_code"] != 0:
        raise RuntimeError(f"propose failed: {res['stderr'] or res['stdout']}")

    plan_id = _extract(r"- plan_id:\s*(\S+)", res["stdout"])
    token = _extract(r"- approval_token_for_test:\s*(\S+)", res["stdout"])
    action = _extract(r"- action_id:\s*(\S+)", res["stdout"])
    if not plan_id or not token:
        raise RuntimeError("propose output missing plan id or token")
    return plan_id, token, action, res


def _execute(root: Path, task: str, plan_id: str, token: str, run_type: str) -> Dict[str, Any]:
    res = _run(
        root,
        [
            sys.executable,
            "src/operator_runtime/main.py",
            "--run-type",
            run_type,
            "--mode",
            "execute",
            "--task",
            task,
            "--plan-id",
            plan_id,
            "--approval-token",
            token,
        ],
    )
    if res["exit_code"] != 0:
        raise RuntimeError(f"execute failed: {res['stderr'] or res['stdout']}")
    return res


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action-id", required=True, help="Allowlisted lane action_id to onboard")
    parser.add_argument("--task", required=True, help="Representative task text for onboarding")
    parser.add_argument("--channel", default="voice", choices=["text", "voice"])
    parser.add_argument("--days", type=int, default=7, help="Window used for scorecards and soak")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    _ensure_action_exists(root, args.action_id)

    # Quality gate: validate lane spec before anything else.
    from operator_ops.validate_lane import validate_lane as _validate_lane_spec
    lane_path = root / "config" / "lanes" / f"{args.action_id}.json"
    print(f"Quality gate: validating lane spec {lane_path.name}...")
    gate_ok, gate_errors = _validate_lane_spec(lane_path, root)
    if not gate_ok:
        print("QUALITY GATE FAILED — fix the following before onboarding:")
        for e in gate_errors:
            print(f"  - {e}")
        raise SystemExit(1)
    print("Quality gate: all checks passed.")
    print()

    # Pre-flight: verify endpoint and probe URLs are reachable before touching
    # the run index, lifecycle state, or any artifacts.
    print(f"Pre-flight: checking endpoint and probes for {args.action_id}...")
    preflight_failures = _probe_preflight(root, args.action_id)
    if preflight_failures:
        print("PREFLIGHT FAILED — aborting onboarding. Fix the following before retrying:")
        for f in preflight_failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("Pre-flight: all endpoints reachable.")
    print()

    steps: List[Dict[str, Any]] = []

    lifecycle_path = _set_lifecycle_state(root, args.action_id, "candidate")
    steps.append({"step": "set_candidate", "path": str(lifecycle_path), "status": "ok"})

    test_plan_id, test_token, test_action, test_propose = _propose(
        root, args.task, args.channel, run_type="test"
    )
    if test_action != args.action_id:
        raise RuntimeError(f"test propose resolved unexpected action_id: {test_action}")
    steps.append({"step": "test_propose", "status": "ok", "action_id": test_action, "result": test_propose})

    test_execute = _execute(root, args.task, test_plan_id, test_token, run_type="test")
    steps.append({"step": "test_execute", "status": "ok", "result": test_execute})

    prod_plan_id, prod_token, prod_action, prod_propose = _propose(
        root, args.task, args.channel, run_type="prod"
    )
    if prod_action != args.action_id:
        raise RuntimeError(f"prod propose resolved unexpected action_id: {prod_action}")
    steps.append({"step": "prod_propose", "status": "ok", "action_id": prod_action, "result": prod_propose})

    prod_execute = _execute(root, args.task, prod_plan_id, prod_token, run_type="prod")
    steps.append({"step": "prod_execute", "status": "ok", "result": prod_execute})

    _set_lifecycle_state(root, args.action_id, "active")
    steps.append({"step": "set_active", "path": str(lifecycle_path), "status": "ok"})

    runbooks = _run(root, [sys.executable, "src/operator_ops/generate_runbooks.py"])
    steps.append({"step": "generate_runbooks", "status": "ok" if runbooks["exit_code"] == 0 else "fail", "result": runbooks})

    svc = _run(
        root,
        [
            sys.executable,
            "src/operator_ops/report_service_scorecards.py",
            "--days",
            str(args.days),
            "--export-dashboard",
            "--enforce-gate",
        ],
    )
    steps.append({"step": "service_scorecards_gate", "status": "ok" if svc["exit_code"] == 0 else "fail", "result": svc})

    soak = _run(
        root,
        [
            sys.executable,
            "src/operator_ops/run_stage2_soak_snapshot.py",
            "--days",
            str(args.days),
            "--enforce-exit",
        ],
    )
    steps.append({"step": "stage2_soak_gate", "status": "ok" if soak["exit_code"] == 0 else "fail", "result": soak})

    out_dir = root / "artifacts" / "onboarding"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"lane_onboarding_{args.action_id}_{stamp}.json"

    payload = {
        "generated_at": _utc_now_iso(),
        "action_id": args.action_id,
        "task": args.task,
        "channel": args.channel,
        "window_days": args.days,
        "steps": steps,
        "status": "pass" if all(s.get("status") == "ok" for s in steps) else "fail",
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print("Operator Kernel Lane Onboarding")
    print(f"- action_id: {args.action_id}")
    print(f"- status: {payload['status']}")
    print(f"- artifact: {out_path}")

    if payload["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
