import argparse
import json
import os
import shutil
import re
import subprocess
import tempfile
from unittest.mock import patch
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from operator_runtime.artifact_gc import run_artifact_gc


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
    plan["_stdout"] = out
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


def test_required_context_missing_blocks(root: Path) -> TestResult:
    cfg_path = root / "config" / "context_sources.json"
    original = _load_json(cfg_path)
    try:
        mutated = dict(original)
        sources = list(mutated.get("sources", []))
        sources.append(
            {
                "id": "required_missing_test",
                "path": "/opt/stack/services/this/path/does/not/exist_for_test.md",
                "required": True,
            }
        )
        mutated["sources"] = sources
        _write_json(cfg_path, mutated)

        code, out, err = _run_cmd(
            root,
            [
                "python3",
                "src/operator_runtime/main.py",
                "--run-type",
                "test",
                "--mode",
                "inspect",
                "--channel",
                "text",
                "--task",
                "Plex playback failing",
            ],
        )
        if code == 0 and "Preflight failed: required context missing" in out:
            return TestResult(
                "required_context_missing_blocks",
                "openclaw_learned",
                True,
                "required missing source failed closed",
            )
        return TestResult(
            "required_context_missing_blocks",
            "openclaw_learned",
            False,
            err.strip() or out.strip(),
        )
    finally:
        _write_json(cfg_path, original)


def test_provider_budget_alert_explicit(root: Path) -> TestResult:
    p = Path("/opt/stack/services/apps/dashboard/provider_budget_status.json")
    if not p.exists():
        return TestResult("provider_budget_alert_explicit", "openclaw_learned", False, "provider budget file missing")

    data = _load_json(p)
    if "alerts" not in data:
        return TestResult("provider_budget_alert_explicit", "openclaw_learned", False, "alerts key missing from provider budget status")

    alerts = list(data["alerts"])
    if not alerts:
        return TestResult(
            "provider_budget_alert_explicit",
            "openclaw_learned",
            True,
            "budget status exposes alerts field (no active alerts)",
        )

    has_required = all(
        isinstance(a, dict)
        and "provider" in a
        and "level" in a
        and "percent" in a
        and "message" in a
        for a in alerts
    )
    if has_required:
        return TestResult(
            "provider_budget_alert_explicit",
            "openclaw_learned",
            True,
            "budget status exposes explicit provider alert fields",
        )
    return TestResult(
        "provider_budget_alert_explicit",
        "openclaw_learned",
        False,
        "alert entries missing required explicit fields",
    )


def test_multi_service_single_lane_deterministic(root: Path) -> TestResult:
    cases = [
        (
            "Plex and Sonarr both not responding",
            "plex_safe_recovery_v1",
            "earliest mention should win for equal-priority mixed phrase",
        ),
        (
            "Sonarr and Plex both not responding",
            "sonarr_safe_recovery_v1",
            "earliest mention should win for reversed phrase",
        ),
        (
            "sab queue stuck and plex web unavailable",
            "plex_safe_recovery_v1",
            "higher priority mapping should beat earlier low-priority mention",
        ),
        (
            "Home Assistant and Radarr both unavailable",
            "homeassistant_safe_recovery_v1",
            "earliest mention should win even when keyword lengths differ",
        ),
    ]

    mismatches: List[str] = []
    for task, expected_action, reason in cases:
        plan, detail = _propose(root, task)
        if not plan:
            return TestResult("multi_service_single_lane_deterministic", "speculative", False, detail)

        actual_action = str(plan.get("action_id", ""))
        if actual_action != expected_action:
            mismatches.append(
                f"task='{task}' expected={expected_action} actual={actual_action} ({reason})"
            )

    if mismatches:
        return TestResult(
            "multi_service_single_lane_deterministic",
            "speculative",
            False,
            " | ".join(mismatches),
        )

    return TestResult(
        "multi_service_single_lane_deterministic",
        "speculative",
        True,
        "mixed-service phrases resolved deterministically by mention order and priority policy",
    )


def test_repeated_incident_visible_in_report(root: Path) -> TestResult:
    for _ in range(2):
        plan, detail = _propose(root, "Sonarr not responding on web")
        if not plan:
            return TestResult("repeated_incident_visible_in_report", "speculative", False, detail)

    code, out, err = _run_cmd(
        root,
        [
            "python3",
            "src/operator_ops/report_runs.py",
            "--days",
            "1",
            "--top",
            "10",
            "--recent",
            "3",
        ],
    )
    if code != 0:
        return TestResult("repeated_incident_visible_in_report", "speculative", False, err.strip() or out.strip())

    match = re.search(r"Sonarr not responding on web:(\d+)", out)
    if match and int(match.group(1)) >= 2:
        return TestResult(
            "repeated_incident_visible_in_report",
            "speculative",
            True,
            "recurrence is visible in consolidated report",
        )
    return TestResult(
        "repeated_incident_visible_in_report",
        "speculative",
        False,
        "recurrence signal not detected in top_tasks output",
    )


def test_voice_ambiguity_parity(root: Path) -> TestResult:
    p1, d1 = _propose(root, "Sonarr not responding on web", channel="voice")
    if not p1:
        return TestResult("voice_ambiguity_parity", "speculative", False, d1)

    p2, d2 = _propose(root, "Uh sonarr web ui is kind of down right now", channel="voice")
    if not p2:
        return TestResult("voice_ambiguity_parity", "speculative", False, d2)

    same_action = p1.get("action_id") == p2.get("action_id")
    same_lane = p1.get("lane") == p2.get("lane")
    if same_action and same_lane:
        return TestResult(
            "voice_ambiguity_parity",
            "speculative",
            True,
            "voice phrasing ambiguity resolved to same lane/action semantics",
        )

    return TestResult(
        "voice_ambiguity_parity",
        "speculative",
        False,
        "voice phrasing produced divergent action/lane semantics",
    )


def test_unsafe_destructive_refusal(root: Path) -> TestResult:
    plan, detail = _propose(root, "rm -rf everything in plex")
    if not plan:
        return TestResult("unsafe_destructive_refusal", "speculative", False, detail)

    risk = str(plan.get("risk", ""))
    if risk != "critical":
        return TestResult("unsafe_destructive_refusal", "speculative", False, f"expected critical risk, got {risk}")

    code, out, err = _execute(
        root,
        "rm -rf everything in plex",
        str(plan.get("plan_id", "")),
        str(plan.get("_approval_token", "")),
        apply=True,
    )
    if code == 0 and "apply is blocked for risk tier 'critical'" in out:
        return TestResult(
            "unsafe_destructive_refusal",
            "speculative",
            True,
            "critical destructive request was policy-blocked",
        )
    return TestResult("unsafe_destructive_refusal", "speculative", False, err.strip() or out.strip())


def test_reachability_triage_step_present(root: Path) -> TestResult:
    plan, detail = _propose(root, "Plex not responding")
    if not plan:
        return TestResult("reachability_triage_step_present", "openclaw_learned", False, detail)

    steps = [str(s) for s in plan.get("steps", [])]
    has_step = any("network reachability" in s.lower() for s in steps)
    if has_step:
        return TestResult(
            "reachability_triage_step_present",
            "openclaw_learned",
            True,
            "plan includes deterministic network reachability triage step",
        )
    return TestResult(
        "reachability_triage_step_present",
        "openclaw_learned",
        False,
        "network reachability triage step missing",
    )


def test_gc_permission_denied_nonfatal(root: Path) -> TestResult:
    try:
        with tempfile.TemporaryDirectory(prefix="ok_gc_perm_") as tmp:
            sandbox = Path(tmp)
            (sandbox / "config" / "schemas").mkdir(parents=True, exist_ok=True)
            (sandbox / "artifacts" / "plans").mkdir(parents=True, exist_ok=True)
            (sandbox / "artifacts" / "proofs").mkdir(parents=True, exist_ok=True)
            (sandbox / "artifacts" / "archive" / "plans").mkdir(parents=True, exist_ok=True)

            shutil.copy2(
                root / "config" / "schemas" / "archive_index.schema.json",
                sandbox / "config" / "schemas" / "archive_index.schema.json",
            )

            for idx in range(2):
                p = sandbox / "artifacts" / "plans" / f"p{idx}.json"
                p.write_text('{"k":1}\n', encoding="utf-8")

            # Force one deterministic permission error so behavior is independent
            # of calling user privileges and filesystem semantics.
            original_replace = Path.replace
            forced_error = {"raised": False}

            def _replace_with_forced_permission_error(self: Path, target: Path) -> Path:
                if (
                    not forced_error["raised"]
                    and str(self).startswith(str(sandbox / "artifacts" / "plans"))
                ):
                    forced_error["raised"] = True
                    raise PermissionError("forced test permission error")
                return original_replace(self, target)

            with patch("pathlib.Path.replace", autospec=True, side_effect=_replace_with_forced_permission_error):
                summary = run_artifact_gc(
                    sandbox,
                    max_plan_files=1,
                    max_plan_age_days=0,
                    max_proof_files=10,
                    max_proof_age_days=0,
                    soft_deprecate_enabled=True,
                    archive_retention_days=365,
                )

            failed_moves = int(summary.get("plans_archive_failed", 0))
            errors = list(summary.get("errors", []))
            if failed_moves >= 1 and any("archive_move_failed" in str(e) for e in errors):
                return TestResult(
                    "gc_permission_denied_nonfatal",
                    "openclaw_learned",
                    True,
                    "gc reports permission-denied archive move without crashing",
                )

            return TestResult(
                "gc_permission_denied_nonfatal",
                "openclaw_learned",
                False,
                "expected non-fatal archive_move_failed signal missing",
            )
    except Exception as exc:
        return TestResult("gc_permission_denied_nonfatal", "openclaw_learned", False, str(exc))


def run_suite(root: Path) -> List[TestResult]:
    tests = [
        test_required_context_missing_blocks,
        test_provider_budget_alert_explicit,
        test_multi_service_single_lane_deterministic,
        test_repeated_incident_visible_in_report,
        test_voice_ambiguity_parity,
        test_unsafe_destructive_refusal,
        test_reachability_triage_step_present,
        test_gc_permission_denied_nonfatal,
    ]
    results: List[TestResult] = []
    for test in tests:
        results.append(test(root))
    return results


def write_report(root: Path, results: List[TestResult]) -> Path:
    out_dir = root / "artifacts" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = out_dir / f"broad_spectrum_batch2_{stamp}.json"

    payload = {
        "generated_at": _utc_now_iso(),
        "suite": "broad_spectrum_batch2",
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
    _write_json(report, payload)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args()

    root = Path(args.root).resolve()
    results = run_suite(root)
    report = write_report(root, results)

    print("Broad Spectrum Test Suite Batch 2")
    for result in results:
        state = "PASS" if result.passed else "FAIL"
        print(f"- {state} {result.category}.{result.name}: {result.detail}")
    print(f"- summary: {sum(1 for r in results if r.passed)}/{len(results)} passed")
    print(f"- report: {report}")

    if any(not r.passed for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
