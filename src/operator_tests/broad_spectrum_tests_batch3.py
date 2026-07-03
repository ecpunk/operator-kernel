import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TestResult:
    name: str
    category: str
    passed: bool
    detail: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run_cmd(root: Path, args: List[str]) -> Tuple[int, str, str]:
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
    return proc.returncode, proc.stdout, proc.stderr


def _extract(pattern: str, text: str) -> Optional[str]:
    match = re.search(pattern, text)
    if not match:
        return None
    return match.group(1).strip()


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _propose(root: Path, task: str, channel: str = "voice") -> Tuple[Optional[Dict[str, Any]], str]:
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
            channel,
            "--task",
            task,
        ],
    )
    if code != 0:
        return None, err.strip() or out.strip()

    plan_id = _extract(r"- plan_id:\s*(\S+)", out)
    token = _extract(r"- approval_token_for_test:\s*(\S+)", out)
    path_str = _extract(r"- path:\s*(\S+)", out)
    if not plan_id or not token or not path_str:
        return None, "propose output missing plan details"

    plan_path = Path(path_str)
    if not plan_path.exists():
        return None, f"plan not found at {plan_path}"

    plan = _load_json(plan_path)
    plan["_approval_token"] = token
    return plan, "ok"


def _execute(
    root: Path,
    task: str,
    plan_id: str,
    token: str,
    apply: bool,
) -> Tuple[int, str, str]:
    args = [
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
    ]
    if apply:
        args.append("--apply")
    return _run_cmd(root, args)


def test_malformed_lane_spec_fails_closed(root: Path) -> TestResult:
    lane_path = root / "config" / "lanes" / "plex_safe_recovery_v1.json"
    original = _load_json(lane_path)
    try:
        mutated = dict(original)
        mutated.pop("endpoint", None)
        _write_json(lane_path, mutated)

        code, out, err = _run_cmd(
            root,
            ["python3", "src/operator_runtime/main.py", "--mode", "inspect", "--task", "Plex playback failing on LAN"],
        )
        combined = (out or "") + "\n" + (err or "")
        if "Schema validation failure" in combined and "missing required key 'endpoint'" in combined:
            return TestResult(
                "malformed_lane_spec_fails_closed",
                "failure_injection",
                True,
                "runtime config validation failed closed on malformed lane spec",
            )
        return TestResult(
            "malformed_lane_spec_fails_closed",
            "failure_injection",
            False,
            err.strip() or out.strip(),
        )
    finally:
        _write_json(lane_path, original)


def test_malformed_guardrails_fails_closed(root: Path) -> TestResult:
    guardrails_path = root / "config" / "guardrails.json"
    original = _load_json(guardrails_path)
    try:
        mutated = dict(original)
        execution = dict(mutated.get("execution", {}))
        execution.pop("token_min_length", None)
        mutated["execution"] = execution
        _write_json(guardrails_path, mutated)

        code, out, err = _run_cmd(
            root,
            ["python3", "src/operator_runtime/main.py", "--mode", "inspect", "--task", "Sonarr web down"],
        )
        combined = (out or "") + "\n" + (err or "")
        if "Schema validation failure" in combined and "missing required key 'token_min_length'" in combined:
            return TestResult(
                "malformed_guardrails_fails_closed",
                "failure_injection",
                True,
                "runtime config validation failed closed on malformed guardrails",
            )
        return TestResult(
            "malformed_guardrails_fails_closed",
            "failure_injection",
            False,
            err.strip() or out.strip(),
        )
    finally:
        _write_json(guardrails_path, original)


def test_degraded_endpoint_verification_failed(root: Path) -> TestResult:
    plan, detail = _propose(root, "Fix Plex after recent restart")
    if not plan:
        return TestResult("degraded_endpoint_verification_failed", "degraded_dependency", False, detail)

    lane_path = root / "config" / "lanes" / "plex_safe_recovery_v1.json"
    original_lane = _load_json(lane_path)
    try:
        mutated = dict(original_lane)
        mutated["endpoint"] = "http://127.0.0.1:1"
        _write_json(lane_path, mutated)

        code, out, err = _execute(
            root,
            "Fix Plex after recent restart",
            str(plan.get("plan_id", "")),
            str(plan.get("_approval_token", "")),
            apply=False,
        )
        if code != 0:
            return TestResult("degraded_endpoint_verification_failed", "degraded_dependency", False, err.strip() or out.strip())

        proof_path = _extract(r"- proof_pack:\s*(\S+)", out)
        if not proof_path:
            return TestResult("degraded_endpoint_verification_failed", "degraded_dependency", False, "missing proof pack path")

        proof = _load_json(Path(proof_path))
        status = str(proof.get("verification", {}).get("status", ""))
        if status == "failed":
            return TestResult(
                "degraded_endpoint_verification_failed",
                "degraded_dependency",
                True,
                "endpoint degradation produced failed verification status",
            )
        return TestResult(
            "degraded_endpoint_verification_failed",
            "degraded_dependency",
            False,
            f"expected failed verification, got {status}",
        )
    finally:
        _write_json(lane_path, original_lane)


def test_missing_lane_spec_proof_failure(root: Path) -> TestResult:
    plan, detail = _propose(root, "Fix Plex after recent restart")
    if not plan:
        return TestResult("missing_lane_spec_proof_failure", "degraded_dependency", False, detail)

    lane_path = root / "config" / "lanes" / "plex_safe_recovery_v1.json"
    backup_path = lane_path.with_suffix(".json.bak_test")

    if backup_path.exists():
        backup_path.unlink()

    try:
        lane_path.rename(backup_path)

        code, out, err = _execute(
            root,
            "Fix Plex after recent restart",
            str(plan.get("plan_id", "")),
            str(plan.get("_approval_token", "")),
            apply=False,
        )
        if code != 0:
            return TestResult("missing_lane_spec_proof_failure", "degraded_dependency", False, err.strip() or out.strip())

        proof_path = _extract(r"- proof_pack:\s*(\S+)", out)
        if not proof_path:
            return TestResult("missing_lane_spec_proof_failure", "degraded_dependency", False, "missing proof pack path")

        proof = _load_json(Path(proof_path))
        precheck = str(proof.get("precheck", {}).get("status", ""))
        notes = " ".join(str(n) for n in proof.get("execution_notes", []))
        if precheck == "failed" and "lane spec lookup" in notes.lower():
            return TestResult(
                "missing_lane_spec_proof_failure",
                "degraded_dependency",
                True,
                "missing lane spec produced deterministic failed proof output",
            )
        return TestResult(
            "missing_lane_spec_proof_failure",
            "degraded_dependency",
            False,
            "proof did not carry expected lane spec failure markers",
        )
    finally:
        if backup_path.exists():
            backup_path.rename(lane_path)


def test_state_writer_disable_mode(root: Path) -> TestResult:
    policy_path = root / "config" / "state_writer_targets.json"
    original = _load_json(policy_path)
    try:
        mutated = dict(original)
        mutated["enabled"] = False
        _write_json(policy_path, mutated)

        plan, detail = _propose(root, "Fix Plex after recent restart")
        if not plan:
            return TestResult("state_writer_disable_mode", "failure_injection", False, detail)

        code, out, err = _execute(
            root,
            "Fix Plex after recent restart",
            str(plan.get("plan_id", "")),
            str(plan.get("_approval_token", "")),
            apply=False,
        )
        if code != 0:
            return TestResult("state_writer_disable_mode", "failure_injection", False, err.strip() or out.strip())

        if "- state_writer:" in out and "enabled=False" in out and "targets_updated=0" in out:
            return TestResult(
                "state_writer_disable_mode",
                "failure_injection",
                True,
                "state writer disabled mode honored with zero target writes",
            )
        return TestResult(
            "state_writer_disable_mode",
            "failure_injection",
            False,
            "state writer output did not reflect disabled mode",
        )
    finally:
        _write_json(policy_path, original)


def run_suite(root: Path) -> List[TestResult]:
    tests = [
        test_malformed_lane_spec_fails_closed,
        test_malformed_guardrails_fails_closed,
        test_degraded_endpoint_verification_failed,
        test_missing_lane_spec_proof_failure,
        test_state_writer_disable_mode,
    ]
    results: List[TestResult] = []
    for test in tests:
        results.append(test(root))
    return results


def write_report(root: Path, results: List[TestResult]) -> Path:
    out_dir = root / "artifacts" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"broad_spectrum_batch3_{stamp}.json"

    payload = {
        "generated_at": _utc_now_iso(),
        "suite": "broad_spectrum_batch3",
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "results": [
            {
                "name": r.name,
                "category": r.category,
                "passed": r.passed,
                "detail": r.detail,
            }
            for r in results
        ],
    }
    _write_json(path, payload)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args()

    root = Path(args.root).resolve()
    results = run_suite(root)
    report = write_report(root, results)

    print("Broad Spectrum Test Suite Batch 3")
    for result in results:
        state = "PASS" if result.passed else "FAIL"
        print(f"- {state} {result.category}.{result.name}: {result.detail}")
    print(f"- summary: {sum(1 for r in results if r.passed)}/{len(results)} passed")
    print(f"- report: {report}")

    if any(not r.passed for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
