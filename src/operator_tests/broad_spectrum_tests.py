import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from operator_runtime.artifact_gc import run_artifact_gc
from operator_kernel.schema_validator import validate_data_against_schema


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
    m = re.search(pattern, text)
    if not m:
        return None
    return m.group(1).strip()


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _propose(root: Path, task: str, channel: str = "voice") -> Tuple[Optional[Dict[str, Any]], str, str]:
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
        return None, out, f"propose rc={code}: {err.strip() or out.strip()}"

    plan_id = _extract(r"- plan_id:\s*(\S+)", out)
    token = _extract(r"- approval_token_for_test:\s*(\S+)", out)
    path_str = _extract(r"- path:\s*(\S+)", out)
    if not plan_id or not token or not path_str:
        return None, out, "missing plan_id or token or path"

    plan_path = Path(path_str)
    if not plan_path.exists():
        return None, out, f"plan file missing: {plan_path}"

    plan = _load_json(plan_path)
    plan["_approval_token"] = token
    plan["_path"] = str(plan_path)
    return plan, out, "ok"


def _execute(
    root: Path,
    task: str,
    plan_id: str,
    token: Optional[str],
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
    ]
    if token is not None:
        args.extend(["--approval-token", token])
    if apply:
        args.append("--apply")
    return _run_cmd(root, args)


def test_execute_requires_plan_id(root: Path) -> TestResult:
    code, out, err = _run_cmd(
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
        ],
    )
    if code == 0 and "--plan-id is required for execute mode" in out:
        return TestResult("execute_requires_plan_id", "guardrail", True, "blocked missing plan-id")
    return TestResult("execute_requires_plan_id", "guardrail", False, err.strip() or out.strip())


def test_mismatched_token_block(root: Path) -> TestResult:
    plan, _, detail = _propose(root, "Fix Plex after recent restart")
    if not plan:
        return TestResult("mismatched_token_block", "guardrail", False, detail)

    code, out, err = _execute(
        root,
        "Fix Plex after recent restart",
        str(plan.get("plan_id", "")),
        "appr_invalid_token",
        apply=False,
    )
    if code == 0 and "approval token does not match the approved plan token" in out:
        return TestResult("mismatched_token_block", "guardrail", True, "mismatched token blocked")
    return TestResult("mismatched_token_block", "guardrail", False, err.strip() or out.strip())


def test_stale_plan_block(root: Path) -> TestResult:
    plan, _, detail = _propose(root, "Fix Plex after recent restart")
    if not plan:
        return TestResult("stale_plan_block", "guardrail", False, detail)

    plan_path = Path(str(plan.get("_path", "")))
    original = _load_json(plan_path)
    try:
        stale = dict(original)
        stale["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=10)).replace(microsecond=0).isoformat()
        _write_json(plan_path, stale)

        code, out, err = _execute(
            root,
            "Fix Plex after recent restart",
            str(stale.get("plan_id", "")),
            str(stale.get("approval_token", "")),
            apply=False,
        )
        if code == 0 and "plan age exceeds execution policy maximum" in out:
            return TestResult("stale_plan_block", "guardrail", True, "stale plan blocked")
        return TestResult("stale_plan_block", "guardrail", False, err.strip() or out.strip())
    finally:
        _write_json(plan_path, original)


def test_apply_requires_action_id(root: Path) -> TestResult:
    plan, _, detail = _propose(root, "Fix Plex after recent restart")
    if not plan:
        return TestResult("apply_requires_action_id", "guardrail", False, detail)

    plan_path = Path(str(plan.get("_path", "")))
    original = _load_json(plan_path)
    try:
        mutated = dict(original)
        mutated["action_id"] = ""
        _write_json(plan_path, mutated)

        code, out, err = _execute(
            root,
            "Fix Plex after recent restart",
            str(mutated.get("plan_id", "")),
            str(mutated.get("approval_token", "")),
            apply=True,
        )
        if code == 0 and "apply requires allowlisted action_id" in out:
            return TestResult("apply_requires_action_id", "guardrail", True, "missing action_id blocked")
        return TestResult("apply_requires_action_id", "guardrail", False, err.strip() or out.strip())
    finally:
        _write_json(plan_path, original)


def test_plan_and_proof_schema(root: Path) -> TestResult:
    plan, _, detail = _propose(root, "Fix Plex after recent restart")
    if not plan:
        return TestResult("plan_and_proof_schema", "artifact", False, detail)

    code, out, err = _execute(
        root,
        "Fix Plex after recent restart",
        str(plan.get("plan_id", "")),
        str(plan.get("_approval_token", "")),
        apply=False,
    )
    if code != 0:
        return TestResult("plan_and_proof_schema", "artifact", False, err.strip() or out.strip())

    proof_path = _extract(r"- proof_pack:\s*(\S+)", out)
    if not proof_path:
        return TestResult("plan_and_proof_schema", "artifact", False, "missing proof path in execute output")

    try:
        plan_data = _load_json(Path(str(plan.get("_path", ""))) )
        proof_data = _load_json(Path(proof_path))
        plan_schema = _load_json(root / "config" / "schemas" / "plan.schema.json")
        proof_schema = _load_json(root / "config" / "schemas" / "proof.schema.json")
    except Exception as exc:
        return TestResult("plan_and_proof_schema", "artifact", False, f"schema read error: {exc}")

    plan_errors = validate_data_against_schema(plan_data, plan_schema)
    proof_errors = validate_data_against_schema(proof_data, proof_schema)
    if not plan_errors and not proof_errors:
        return TestResult("plan_and_proof_schema", "artifact", True, "plan and proof match schemas")

    return TestResult(
        "plan_and_proof_schema",
        "artifact",
        False,
        f"plan_errors={len(plan_errors)}, proof_errors={len(proof_errors)}",
    )


def test_gc_archive_and_purge_sandbox(root: Path) -> TestResult:
    try:
        with tempfile.TemporaryDirectory(prefix="ok_gc_") as tmp:
            sandbox = Path(tmp)
            (sandbox / "config" / "schemas").mkdir(parents=True, exist_ok=True)
            (sandbox / "artifacts" / "plans").mkdir(parents=True, exist_ok=True)
            (sandbox / "artifacts" / "proofs").mkdir(parents=True, exist_ok=True)

            shutil.copy2(
                root / "config" / "schemas" / "archive_index.schema.json",
                sandbox / "config" / "schemas" / "archive_index.schema.json",
            )

            now = datetime.now(timezone.utc)
            for idx in range(3):
                p = sandbox / "artifacts" / "plans" / f"p{idx}.json"
                p.write_text('{"k":1}\n', encoding="utf-8")
                ts = (now - timedelta(days=idx + 5)).timestamp()
                p.touch()
                p.chmod(0o644)
                os_times = (ts, ts)
                import os
                os.utime(p, os_times)

            for idx in range(2):
                p = sandbox / "artifacts" / "proofs" / f"r{idx}.json"
                p.write_text('{"k":1}\n', encoding="utf-8")

            summary = run_artifact_gc(
                sandbox,
                max_plan_files=1,
                max_plan_age_days=1,
                max_proof_files=1,
                max_proof_age_days=0,
                soft_deprecate_enabled=True,
                archive_retention_days=365,
            )
            if summary.get("plans_archived", 0) < 2:
                return TestResult("gc_archive_and_purge_sandbox", "artifact", False, "archive count lower than expected")

            index_path = sandbox / "artifacts" / "archive" / "archive_index.json"
            if not index_path.exists():
                return TestResult("gc_archive_and_purge_sandbox", "artifact", False, "archive index missing")

            idx_payload = _load_json(index_path)
            for rec in idx_payload.get("records", []):
                rec["archived_at"] = "2000-01-01T00:00:00+00:00"
            _write_json(index_path, idx_payload)

            purge = run_artifact_gc(
                sandbox,
                max_plan_files=100,
                max_plan_age_days=100,
                max_proof_files=100,
                max_proof_age_days=100,
                soft_deprecate_enabled=True,
                archive_retention_days=1,
            )
            if purge.get("archive_purged", 0) < 1:
                return TestResult("gc_archive_and_purge_sandbox", "artifact", False, "archive purge did not trigger")

            return TestResult("gc_archive_and_purge_sandbox", "artifact", True, "archive and purge behaviors validated in sandbox")
    except Exception as exc:
        return TestResult("gc_archive_and_purge_sandbox", "artifact", False, str(exc))


def test_dashboard_section_only(root: Path) -> TestResult:
    dashboard_path = Path("/opt/stack/services/apps/dashboard/status.json")
    before = _load_json(dashboard_path)
    before_non_ok = dict(before)
    before_non_ok.pop("operator_kernel", None)

    plan, _, detail = _propose(root, "Fix Plex after recent restart")
    if not plan:
        return TestResult("dashboard_section_only", "state_writer", False, detail)

    code, out, err = _execute(
        root,
        "Fix Plex after recent restart",
        str(plan.get("plan_id", "")),
        str(plan.get("_approval_token", "")),
        apply=True,
    )
    if code != 0:
        return TestResult("dashboard_section_only", "state_writer", False, err.strip() or out.strip())

    after = _load_json(dashboard_path)
    after_non_ok = dict(after)
    after_non_ok.pop("operator_kernel", None)

    if before_non_ok == after_non_ok and "operator_kernel" in after:
        return TestResult("dashboard_section_only", "state_writer", True, "only operator_kernel section changed")

    return TestResult("dashboard_section_only", "state_writer", False, "dashboard fields outside operator_kernel changed")


def test_unknown_lane_safe_behavior(root: Path) -> TestResult:
    plan, _, detail = _propose(root, "Random service no known lane keyword")
    if not plan:
        return TestResult("unknown_lane_safe_behavior", "regression", False, detail)

    if str(plan.get("action_id", "")).strip():
        return TestResult("unknown_lane_safe_behavior", "regression", False, "unexpected action_id for unknown task")

    code, out, err = _execute(
        root,
        "Random service no known lane keyword",
        str(plan.get("plan_id", "")),
        str(plan.get("_approval_token", "")),
        apply=True,
    )
    if code == 0 and "apply requires allowlisted action_id" in out:
        return TestResult("unknown_lane_safe_behavior", "regression", True, "unknown lane remained safe")

    return TestResult("unknown_lane_safe_behavior", "regression", False, err.strip() or out.strip())


def run_suite(root: Path) -> List[TestResult]:
    tests = [
        test_execute_requires_plan_id,
        test_mismatched_token_block,
        test_stale_plan_block,
        test_apply_requires_action_id,
        test_plan_and_proof_schema,
        test_gc_archive_and_purge_sandbox,
        test_dashboard_section_only,
        test_unknown_lane_safe_behavior,
    ]
    results: List[TestResult] = []
    for test in tests:
        results.append(test(root))
    return results


def write_report(root: Path, results: List[TestResult]) -> Path:
    out_dir = root / "artifacts" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"broad_spectrum_batch1_{stamp}.json"
    payload = {
        "generated_at": _utc_now_iso(),
        "suite": "broad_spectrum_batch1",
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

    print("Broad Spectrum Test Suite Batch 1")
    for r in results:
        state = "PASS" if r.passed else "FAIL"
        print(f"- {state} {r.category}.{r.name}: {r.detail}")
    print(f"- summary: {sum(1 for r in results if r.passed)}/{len(results)} passed")
    print(f"- report: {report}")

    if any(not r.passed for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
