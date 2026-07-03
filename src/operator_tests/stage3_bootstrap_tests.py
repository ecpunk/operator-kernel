import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from operator_notify import notify_event


@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run_cmd(root: Path, args: List[str]) -> Tuple[int, str, str]:
    env = dict(os.environ)
    src_path = str(root / "src")
    existing = str(env.get("PYTHONPATH", "")).strip()
    env["PYTHONPATH"] = f"{src_path}:{existing}" if existing else src_path
    # Avoid notification storms from nested runtime calls inside this test suite.
    env["OPERATOR_NOTIFY_SUPPRESS"] = "1"
    proc = subprocess.run(
        args,
        cwd=str(root),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _extract(pattern: str, text: str) -> Optional[str]:
    m = re.search(pattern, text)
    if not m:
        return None
    return m.group(1).strip()


def _propose(root: Path, task: str) -> Tuple[Optional[Dict[str, Any]], str]:
    code, out, err = _run_cmd(
        root,
        [
            "python3",
            "src/operator_runtime/main.py",
            "--run-type",
            "test",
            "--mode",
            "propose",
            "--channel",
            "voice",
            "--task",
            task,
        ],
    )
    if code != 0:
        return None, err.strip() or out.strip()

    plan_id = _extract(r"- plan_id:\s*(\S+)", out)
    token = _extract(r"- approval_token_for_test:\s*(\S+)", out)
    action_id = _extract(r"- action_id:\s*(\S+)", out)
    if not plan_id or not token:
        return None, "missing plan_id/token in propose output"

    return {"plan_id": plan_id, "token": token, "action_id": action_id or ""}, "ok"


def _execute(root: Path, task: str, plan_id: str, token: str) -> Tuple[int, str, str]:
    return _run_cmd(
        root,
        [
            "python3",
            "src/operator_runtime/main.py",
            "--run-type",
            "test",
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


def test_radarr_keyword_resolution(root: Path) -> TestResult:
    plan, detail = _propose(root, "Radarr web UI unavailable after update")
    if not plan:
        return TestResult("radarr_keyword_resolution", False, detail)
    if str(plan.get("action_id", "")) == "radarr_safe_recovery_v1":
        return TestResult("radarr_keyword_resolution", True, "task resolved to radarr lane")
    return TestResult(
        "radarr_keyword_resolution",
        False,
        f"unexpected action_id: {plan.get('action_id','')}",
    )


def test_radarr_dry_run_execute(root: Path) -> TestResult:
    plan, detail = _propose(root, "Radarr web UI unavailable after update")
    if not plan:
        return TestResult("radarr_dry_run_execute", False, detail)

    code, out, err = _execute(
        root,
        "Radarr web UI unavailable after update",
        str(plan.get("plan_id", "")),
        str(plan.get("token", "")),
    )
    if code != 0:
        return TestResult("radarr_dry_run_execute", False, err.strip() or out.strip())

    if "Execute Contract" not in out or "action_id: radarr_safe_recovery_v1" not in out:
        return TestResult("radarr_dry_run_execute", False, "execute output missing expected contract markers")
    if "proof_pack:" not in out:
        return TestResult("radarr_dry_run_execute", False, "execute output missing proof path")
    return TestResult("radarr_dry_run_execute", True, "dry-run execute completed with proof generation")


def test_bazarr_keyword_resolution(root: Path) -> TestResult:
    plan, detail = _propose(root, "Bazarr web UI unavailable after update")
    if not plan:
        return TestResult("bazarr_keyword_resolution", False, detail)
    if str(plan.get("action_id", "")) == "bazarr_safe_recovery_v1":
        return TestResult("bazarr_keyword_resolution", True, "task resolved to bazarr lane")
    return TestResult(
        "bazarr_keyword_resolution",
        False,
        f"unexpected action_id: {plan.get('action_id','')}",
    )


def test_bazarr_dry_run_execute(root: Path) -> TestResult:
    plan, detail = _propose(root, "Bazarr web UI unavailable after update")
    if not plan:
        return TestResult("bazarr_dry_run_execute", False, detail)

    code, out, err = _execute(
        root,
        "Bazarr web UI unavailable after update",
        str(plan.get("plan_id", "")),
        str(plan.get("token", "")),
    )
    if code != 0:
        return TestResult("bazarr_dry_run_execute", False, err.strip() or out.strip())

    if "Execute Contract" not in out or "action_id: bazarr_safe_recovery_v1" not in out:
        return TestResult("bazarr_dry_run_execute", False, "execute output missing expected contract markers")
    if "proof_pack:" not in out:
        return TestResult("bazarr_dry_run_execute", False, "execute output missing proof path")
    return TestResult("bazarr_dry_run_execute", True, "dry-run execute completed with proof generation")


def test_homeassistant_keyword_resolution(root: Path) -> TestResult:
    plan, detail = _propose(root, "Home Assistant dashboard unavailable")
    if not plan:
        return TestResult("homeassistant_keyword_resolution", False, detail)
    if str(plan.get("action_id", "")) == "homeassistant_safe_recovery_v1":
        return TestResult("homeassistant_keyword_resolution", True, "task resolved to homeassistant lane")
    return TestResult(
        "homeassistant_keyword_resolution",
        False,
        f"unexpected action_id: {plan.get('action_id','')}",
    )


def test_homeassistant_dry_run_execute(root: Path) -> TestResult:
    plan, detail = _propose(root, "Home Assistant dashboard unavailable")
    if not plan:
        return TestResult("homeassistant_dry_run_execute", False, detail)

    code, out, err = _execute(
        root,
        "Home Assistant dashboard unavailable",
        str(plan.get("plan_id", "")),
        str(plan.get("token", "")),
    )
    if code != 0:
        return TestResult("homeassistant_dry_run_execute", False, err.strip() or out.strip())

    if "Execute Contract" not in out or "action_id: homeassistant_safe_recovery_v1" not in out:
        return TestResult(
            "homeassistant_dry_run_execute",
            False,
            "execute output missing expected contract markers",
        )
    if "proof_pack:" not in out:
        return TestResult("homeassistant_dry_run_execute", False, "execute output missing proof path")
    return TestResult("homeassistant_dry_run_execute", True, "dry-run execute completed with proof generation")


def test_sabnzbd_keyword_resolution(root: Path) -> TestResult:
    plan, detail = _propose(root, "SABnzbd queue UI unavailable")
    if not plan:
        return TestResult("sabnzbd_keyword_resolution", False, detail)
    if str(plan.get("action_id", "")) == "sabnzbd_safe_recovery_v1":
        return TestResult("sabnzbd_keyword_resolution", True, "task resolved to sabnzbd lane")
    return TestResult(
        "sabnzbd_keyword_resolution",
        False,
        f"unexpected action_id: {plan.get('action_id','')}",
    )


def test_sabnzbd_dry_run_execute(root: Path) -> TestResult:
    plan, detail = _propose(root, "SABnzbd queue UI unavailable")
    if not plan:
        return TestResult("sabnzbd_dry_run_execute", False, detail)

    code, out, err = _execute(
        root,
        "SABnzbd queue UI unavailable",
        str(plan.get("plan_id", "")),
        str(plan.get("token", "")),
    )
    if code != 0:
        return TestResult("sabnzbd_dry_run_execute", False, err.strip() or out.strip())

    if "Execute Contract" not in out or "action_id: sabnzbd_safe_recovery_v1" not in out:
        return TestResult("sabnzbd_dry_run_execute", False, "execute output missing expected contract markers")
    if "proof_pack:" not in out:
        return TestResult("sabnzbd_dry_run_execute", False, "execute output missing proof path")
    return TestResult("sabnzbd_dry_run_execute", True, "dry-run execute completed with proof generation")


def run_suite(root: Path) -> List[TestResult]:
    tests = [
        test_radarr_keyword_resolution,
        test_radarr_dry_run_execute,
        test_bazarr_keyword_resolution,
        test_bazarr_dry_run_execute,
        test_homeassistant_keyword_resolution,
        test_homeassistant_dry_run_execute,
        test_sabnzbd_keyword_resolution,
        test_sabnzbd_dry_run_execute,
    ]
    return [test(root) for test in tests]


def write_report(root: Path, results: List[TestResult]) -> Path:
    out_dir = root / "artifacts" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = out_dir / f"stage3_bootstrap_{stamp}.json"

    payload = {
        "generated_at": _utc_now_iso(),
        "suite": "stage3_bootstrap",
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "results": [
            {"name": r.name, "passed": r.passed, "detail": r.detail} for r in results
        ],
    }
    report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any test fails",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    try:
        notify_event(
            root=root,
            source="operator_tests.stage3_bootstrap_tests",
            event="test_suite",
            status="start",
            message="stage3 bootstrap test suite started",
            milestone="suite_start",
            details={"suite": "stage3_bootstrap", "strict": bool(args.strict)},
        )
    except Exception:
        pass

    results = run_suite(root)
    report = write_report(root, results)

    for row in results:
        if row.passed:
            continue
        try:
            notify_event(
                root=root,
                source="operator_tests.stage3_bootstrap_tests",
                event="test_case",
                status="failure",
                message=f"stage3 bootstrap test failed: {row.name}",
                severity="warning",
                milestone="test_case_failed",
                details={"test": row.name, "detail": row.detail},
            )
        except Exception:
            pass

    print("Operator Kernel Stage 3 Bootstrap Tests")
    for row in results:
        status = "PASS" if row.passed else "FAIL"
        print(f"- {status}: {row.name} | {row.detail}")
    print(f"- report: {report}")

    try:
        failed_count = sum(1 for r in results if not r.passed)
        notify_event(
            root=root,
            source="operator_tests.stage3_bootstrap_tests",
            event="test_suite",
            status="complete" if failed_count == 0 else "failure",
            message="stage3 bootstrap test suite completed",
            severity="success" if failed_count == 0 else "error",
            milestone="suite_complete",
            details={
                "suite": "stage3_bootstrap",
                "total": len(results),
                "failed": failed_count,
                "report": str(report),
            },
        )
    except Exception:
        pass

    if args.strict and any(not r.passed for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
