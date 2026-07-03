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


def _propose(root: Path, task: str, channel: str) -> Tuple[Optional[Dict[str, Any]], str]:
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
        return None, f"propose failed rc={code}: {err.strip() or out.strip()}"

    plan_id = _extract(r"- plan_id:\s*(\S+)", out)
    token = _extract(r"- approval_token_for_test:\s*(\S+)", out)
    path_str = _extract(r"- path:\s*(\S+)", out)
    if not plan_id or not token or not path_str:
        return None, "propose output missing plan_id/token/path"

    plan_path = Path(path_str)
    if not plan_path.exists():
        return None, f"plan path missing: {plan_path}"

    plan = _load_json(plan_path)
    plan["_approval_token"] = token
    plan["_stdout"] = out
    return plan, "ok"


def _execute(
    root: Path, task: str, plan_id: str, token: str, apply: bool
) -> Tuple[int, str, str]:
    cmd = [
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
        cmd.append("--apply")
    return _run_cmd(root, cmd)


def test_mode_safety_inspect(root: Path) -> TestResult:
    code, out, err = _run_cmd(
        root,
        [
            "python3",
            "src/operator_runtime/main.py",
            "--mode",
            "inspect",
            "--channel",
            "text",
            "--task",
            "Delete all media configs now",
        ],
    )
    if code != 0:
        return TestResult("mode_safety_inspect", False, f"inspect rc={code}: {err.strip()}")

    if "Inspect Contract" in out and "Execute Contract" not in out:
        return TestResult("mode_safety_inspect", True, "inspect remained read-only")
    return TestResult("mode_safety_inspect", False, "inspect output did not match expected contract")


def test_approval_enforcement(root: Path) -> TestResult:
    plan, detail = _propose(root, "Fix Plex after recent restart", "voice")
    if not plan:
        return TestResult("approval_enforcement", False, detail)

    code, out, _ = _run_cmd(
        root,
        [
            "python3",
            "src/operator_runtime/main.py",
            "--run-type",
            "test",
            "--mode",
            "execute",
            "--task",
            "Fix Plex after recent restart",
            "--plan-id",
            str(plan.get("plan_id", "")),
        ],
    )
    expected = "approval token is required for execute mode"
    if code == 0 and "Guardrail failure" in out and expected in out:
        return TestResult("approval_enforcement", True, "missing approval token blocked")
    return TestResult("approval_enforcement", False, "execute without token was not blocked as expected")


def test_proof_pack_generation(root: Path) -> TestResult:
    plan, detail = _propose(root, "Fix Plex after recent restart", "voice")
    if not plan:
        return TestResult("proof_pack_generation", False, detail)

    code, out, err = _execute(
        root,
        "Fix Plex after recent restart",
        str(plan.get("plan_id", "")),
        str(plan.get("_approval_token", "")),
        apply=False,
    )
    if code != 0:
        return TestResult("proof_pack_generation", False, f"execute rc={code}: {err.strip() or out.strip()}")

    proof_path_str = _extract(r"- proof_pack:\s*(\S+)", out)
    if not proof_path_str:
        return TestResult("proof_pack_generation", False, "execute output missing proof_pack path")

    proof_path = Path(proof_path_str)
    if not proof_path.exists():
        return TestResult("proof_pack_generation", False, f"proof missing at {proof_path}")

    try:
        proof = _load_json(proof_path)
    except Exception as exc:
        return TestResult("proof_pack_generation", False, f"proof JSON invalid: {exc}")

    if str(proof.get("plan_id", "")) != str(plan.get("plan_id", "")):
        return TestResult("proof_pack_generation", False, "proof plan_id mismatch")

    return TestResult("proof_pack_generation", True, "proof pack written and valid JSON")


def test_risk_apply_block(root: Path) -> TestResult:
    plan, detail = _propose(root, "Fix Plex and delete old config", "voice")
    if not plan:
        return TestResult("risk_apply_block", False, detail)

    code, out, err = _execute(
        root,
        "Fix Plex and delete old config",
        str(plan.get("plan_id", "")),
        str(plan.get("_approval_token", "")),
        apply=True,
    )
    if code != 0:
        return TestResult("risk_apply_block", False, f"execute rc={code}: {err.strip() or out.strip()}")

    if "Guardrail failure" in out and "apply is blocked for risk tier 'high'" in out:
        return TestResult("risk_apply_block", True, "high-risk apply blocked by policy")

    return TestResult("risk_apply_block", False, "high-risk apply was not blocked")


def test_modality_parity(root: Path) -> TestResult:
    text_plan, text_detail = _propose(root, "Sonarr not responding on web", "text")
    if not text_plan:
        return TestResult("modality_parity", False, text_detail)

    voice_plan, voice_detail = _propose(root, "Sonarr not responding on web", "voice")
    if not voice_plan:
        return TestResult("modality_parity", False, voice_detail)

    same_risk = text_plan.get("risk") == voice_plan.get("risk")
    same_action = text_plan.get("action_id") == voice_plan.get("action_id")
    same_lane = text_plan.get("lane") == voice_plan.get("lane")

    if same_risk and same_action and same_lane:
        return TestResult(
            "modality_parity",
            True,
            "text and voice produced same lane/action/risk semantics",
        )

    return TestResult(
        "modality_parity",
        False,
        "text and voice semantics diverged",
    )


def test_state_writer_update(root: Path) -> TestResult:
    plan, detail = _propose(root, "Fix Plex after recent restart", "voice")
    if not plan:
        return TestResult("state_writer_update", False, detail)

    code, out, err = _execute(
        root,
        "Fix Plex after recent restart",
        str(plan.get("plan_id", "")),
        str(plan.get("_approval_token", "")),
        apply=True,
    )
    if code != 0:
        return TestResult("state_writer_update", False, f"execute rc={code}: {err.strip() or out.strip()}")

    state_path = root / "artifacts" / "state" / "operator_state.json"
    dashboard_path = Path("/opt/stack/services/apps/dashboard/status.json")

    if not state_path.exists():
        return TestResult("state_writer_update", False, "operator_state artifact missing")
    if not dashboard_path.exists():
        return TestResult("state_writer_update", False, "dashboard status missing")

    try:
        state = _load_json(state_path)
        dashboard = _load_json(dashboard_path)
    except Exception as exc:
        return TestResult("state_writer_update", False, f"state target JSON invalid: {exc}")

    last_event = state.get("last_event", {})
    if str(last_event.get("plan_id", "")) != str(plan.get("plan_id", "")):
        return TestResult("state_writer_update", False, "last_event plan_id mismatch")

    if "operator_kernel" not in dashboard:
        return TestResult("state_writer_update", False, "dashboard missing operator_kernel section")

    return TestResult("state_writer_update", True, "state writer updated both trusted targets")


def run_suite(root: Path) -> List[TestResult]:
    tests = [
        test_mode_safety_inspect,
        test_approval_enforcement,
        test_proof_pack_generation,
        test_risk_apply_block,
        test_modality_parity,
        test_state_writer_update,
    ]
    results: List[TestResult] = []
    for test_fn in tests:
        results.append(test_fn(root))
    return results


def write_report(root: Path, results: List[TestResult]) -> Path:
    report_dir = root / "artifacts" / "tests"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = report_dir / f"go_no_go_{stamp}.json"

    payload = {
        "generated_at": _utc_now_iso(),
        "suite": "go_no_go",
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "results": [
            {"name": r.name, "passed": r.passed, "detail": r.detail}
            for r in results
        ],
    }
    report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[2]),
        help="Operator Kernel project root",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    results = run_suite(root)
    report_path = write_report(root, results)

    print("Go/No-Go Test Suite")
    for result in results:
        state = "PASS" if result.passed else "FAIL"
        print(f"- {state} {result.name}: {result.detail}")

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"- summary: {passed}/{total} passed")
    print(f"- report: {report_path}")

    if any(not r.passed for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
