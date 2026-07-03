"""
Regression tests for report_scorecard._compute_scorecard.

Each test builds a fully self-contained fixture in a temp directory and calls
_compute_scorecard directly — no subprocess, no disk mutation of real files.

Scenarios covered:
  1. gate_blocked_runs_excluded_from_denominator
     Verifies the March-11 bug fix: gate-blocked runs (empty verification_status)
     must not be counted in the execute denominator.  Before the fix 280/360=77.8%
     would block; after 280/280=100% passes.

  2. no_prod_data_high_test_pass_rate_passes
     With only test-type executes the scorecard gates on test_pass_rate_pct, not
     execute_verification_success_pct.  High test pass rate → gate pass.

  3. no_prod_data_low_test_pass_rate_blocks
     Same no-prod-data path but test pass rate below threshold → gate block.

  4. proof_pack_gate_waived_when_no_prod_data
     No prod executes and no proof paths present on completed test runs.
     proof_pack_completeness_pct_prod=0.0 but gate still passes because the
     proof-pack gate is waived when there is no prod data.

  5. prod_data_healthy_passes
     Prod executes all completed, proof paths present → gate pass, signal green.

  6. prod_data_low_verification_blocks
     Prod executes below 85% completion rate → gate block.

  7. empty_execute_window_no_crash
     No execute runs at all — only proposes.  Must not crash on zero denominators.

  8. duplicate_run_ids_blocks
     Two runs share the same id → duplicate_run_ids > 0 → gate block.
"""

import argparse
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from operator_ops.report_scorecard import _compute_scorecard

# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _recent_ts(offset_seconds: int = 0) -> str:
    """Return an ISO timestamp that falls within a 7-day window."""
    dt = datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)
    return dt.replace(microsecond=0).isoformat()


_STANDARD_THRESHOLDS: Dict[str, Any] = {
    "green_min_confidence_score": 85.0,
    "yellow_min_confidence_score": 70.0,
    "min_execute_verification_success_pct": 85.0,
    "min_test_pass_rate_pct": 85.0,
    "min_proof_pack_completeness_pct": 98.0,
    "max_duplicate_run_ids": 0,
}


def _make_run(
    run_id: str,
    mode: str = "execute",
    run_type: str = "test",
    verification_status: str = "completed",
    proof_path: Optional[str] = None,
    task: Optional[str] = None,
    ts_offset_seconds: int = 0,
) -> Dict[str, Any]:
    run: Dict[str, Any] = {
        "id": run_id,
        "mode": mode,
        "run_type": run_type,
        "verification_status": verification_status,
        "timestamp": _recent_ts(ts_offset_seconds),
        "task": task or f"task-{run_id}",
        "plan_id": f"plan-{run_id}",
    }
    if proof_path is not None:
        run["proof_path"] = proof_path
    return run


def _make_fixture_root(
    tmp: Path,
    runs: List[Dict[str, Any]],
    thresholds: Optional[Dict[str, Any]] = None,
    test_reports: Optional[List[Dict[str, Any]]] = None,
) -> Path:
    """Write all fixture files into tmp and return tmp as the scorecard root."""
    cfg_dir = tmp / "config"
    idx_dir = tmp / "artifacts" / "index"
    tests_dir = tmp / "artifacts" / "tests"
    for d in (cfg_dir, idx_dir, tests_dir):
        d.mkdir(parents=True, exist_ok=True)

    guardrails: Dict[str, Any] = {
        "scorecard": {
            "stage_gate_fail_closed": True,
            "default_window_days": 7,
            "fail_closed_on": ["yellow", "red"],
            "thresholds": thresholds or _STANDARD_THRESHOLDS,
        }
    }
    (cfg_dir / "guardrails.json").write_text(
        json.dumps(guardrails), encoding="utf-8"
    )
    (idx_dir / "run_index.json").write_text(
        json.dumps({"runs": runs}), encoding="utf-8"
    )

    for i, report in enumerate(test_reports or []):
        fname = f"go_no_go_fixture_{i:03d}.json"
        (tests_dir / fname).write_text(json.dumps(report), encoding="utf-8")

    return tmp


def _test_report(
    total: int = 100,
    passed: int = 96,
    failed: int = 4,
    suite: str = "scorecard_regression",
) -> Dict[str, Any]:
    return {
        "generated_at": _utc_now_iso(),
        "suite": suite,
        "total": total,
        "passed": passed,
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_gate_blocked_runs_excluded_from_denominator() -> TestResult:
    """
    Regression: 280 completed test executes + 80 gate-blocked (empty
    verification_status).  Old code: 280/360=77.8% → block.
    Fixed code: 280/280=100% → pass, gate_blocked=80.
    """
    name = "gate_blocked_runs_excluded_from_denominator"
    with tempfile.TemporaryDirectory() as td:
        runs: List[Dict[str, Any]] = []
        for i in range(280):
            runs.append(
                _make_run(
                    f"ok-{i:04d}",
                    verification_status="completed",
                    proof_path=f"/proof/ok-{i:04d}.json",
                )
            )
        for i in range(80):
            runs.append(
                _make_run(
                    f"blocked-{i:04d}",
                    verification_status="",  # gate-blocked: kernel rejected before execute
                )
            )

        root = _make_fixture_root(
            Path(td),
            runs,
            test_reports=[_test_report(total=100, passed=96, failed=4)],
        )
        result = _compute_scorecard(root, days=7)

    m = result["metrics"]
    gate = result["stage_gate"]

    errors: List[str] = []
    if int(m["execute_total_gate_blocked"]) != 80:
        errors.append(
            f"expected execute_total_gate_blocked=80, got {m['execute_total_gate_blocked']}"
        )
    if float(m["execute_verification_success_pct"]) != 100.0:
        errors.append(
            f"expected execute_verification_success_pct=100.0, "
            f"got {m['execute_verification_success_pct']}"
        )
    if gate["status"] != "pass":
        errors.append(
            f"expected gate=pass, got {gate['status']} "
            f"(reasons: {gate.get('blocked_by', [])})"
        )

    if errors:
        return TestResult(name, False, "; ".join(errors))
    return TestResult(
        name, True,
        "gate_blocked=80 excluded; execute_verification_success_pct=100.0; gate=pass"
    )


def test_no_prod_data_high_test_pass_rate_passes() -> TestResult:
    """
    No prod executes in window (all run_type=test).  Gate should use
    test_pass_rate_pct as proxy.  With 96% pass rate: gate=pass.
    """
    name = "no_prod_data_high_test_pass_rate_passes"
    with tempfile.TemporaryDirectory() as td:
        runs = [
            _make_run(f"te-{i:03d}", run_type="test", verification_status="completed")
            for i in range(20)
        ]
        root = _make_fixture_root(
            Path(td),
            runs,
            test_reports=[_test_report(total=100, passed=96, failed=4)],
        )
        result = _compute_scorecard(root, days=7)

    m = result["metrics"]
    gate = result["stage_gate"]

    errors: List[str] = []
    if int(m["execute_total_prod"]) != 0:
        errors.append(f"expected execute_total_prod=0, got {m['execute_total_prod']}")
    if float(m["test_pass_rate_pct"]) < 85.0:
        errors.append(f"expected test_pass_rate_pct>=85, got {m['test_pass_rate_pct']}")
    if gate["status"] != "pass":
        errors.append(
            f"expected gate=pass, got {gate['status']} "
            f"(reasons: {gate.get('blocked_by', [])})"
        )

    if errors:
        return TestResult(name, False, "; ".join(errors))
    return TestResult(
        name, True,
        f"no_prod_data; test_pass_rate_pct={m['test_pass_rate_pct']}; gate=pass"
    )


def test_no_prod_data_low_test_pass_rate_blocks() -> TestResult:
    """
    No prod executes.  Test pass rate 70% < 85% threshold → gate must block.
    """
    name = "no_prod_data_low_test_pass_rate_blocks"
    with tempfile.TemporaryDirectory() as td:
        runs = [
            _make_run(f"te-{i:03d}", run_type="test", verification_status="completed")
            for i in range(10)
        ]
        root = _make_fixture_root(
            Path(td),
            runs,
            test_reports=[_test_report(total=100, passed=70, failed=30)],
        )
        result = _compute_scorecard(root, days=7)

    m = result["metrics"]
    gate = result["stage_gate"]

    errors: List[str] = []
    if float(m["test_pass_rate_pct"]) >= 85.0:
        errors.append(
            f"expected test_pass_rate_pct<85, got {m['test_pass_rate_pct']}"
        )
    if gate["status"] != "block":
        errors.append(f"expected gate=block, got {gate['status']}")

    if errors:
        return TestResult(name, False, "; ".join(errors))
    return TestResult(
        name, True,
        f"test_pass_rate_pct={m['test_pass_rate_pct']}; gate=block as expected"
    )


def test_proof_pack_gate_waived_when_no_prod_data() -> TestResult:
    """
    No prod executes and no proof paths on completed test runs.
    proof_pack_completeness_pct_prod=0.0 but gate should still pass because
    the proof-pack gate is waived in no-prod-data mode.
    """
    name = "proof_pack_gate_waived_when_no_prod_data"
    with tempfile.TemporaryDirectory() as td:
        runs = [
            # no proof_path field at all
            _make_run(f"te-{i:03d}", run_type="test", verification_status="completed")
            for i in range(15)
        ]
        root = _make_fixture_root(
            Path(td),
            runs,
            test_reports=[_test_report(total=100, passed=96, failed=4)],
        )
        result = _compute_scorecard(root, days=7)

    m = result["metrics"]
    gate = result["stage_gate"]

    errors: List[str] = []
    # proof_pack_completeness_pct_prod must be 0.0 (no prod runs, so 0/0)
    if float(m["proof_pack_completeness_pct_prod"]) != 0.0:
        errors.append(
            f"expected proof_pack_completeness_pct_prod=0.0, "
            f"got {m['proof_pack_completeness_pct_prod']}"
        )
    # gate must pass — proof-pack gate is waived for no-prod-data path
    if gate["status"] != "pass":
        errors.append(
            f"expected gate=pass (proof gate waived), got {gate['status']} "
            f"(reasons: {gate.get('blocked_by', [])})"
        )

    if errors:
        return TestResult(name, False, "; ".join(errors))
    return TestResult(
        name, True,
        "proof_pack_completeness_pct_prod=0.0 but gate=pass; proof gate waived"
    )


def test_prod_data_healthy_passes() -> TestResult:
    """
    Prod executes all completed with proof paths and high test pass rate → gate
    pass, signal green.
    """
    name = "prod_data_healthy_passes"
    with tempfile.TemporaryDirectory() as td:
        runs = [
            _make_run(
                f"pr-{i:03d}",
                run_type="prod",
                verification_status="completed",
                proof_path=f"/proof/pr-{i:03d}.json",
            )
            for i in range(50)
        ]
        root = _make_fixture_root(
            Path(td),
            runs,
            test_reports=[_test_report(total=100, passed=96, failed=4)],
        )
        result = _compute_scorecard(root, days=7)

    m = result["metrics"]
    gate = result["stage_gate"]

    errors: List[str] = []
    if float(m["execute_verification_success_pct_prod"]) < 85.0:
        errors.append(
            f"expected exec_verification_success_pct_prod>=85, "
            f"got {m['execute_verification_success_pct_prod']}"
        )
    if float(m["proof_pack_completeness_pct_prod"]) < 98.0:
        errors.append(
            f"expected proof_pack_completeness_pct_prod>=98, "
            f"got {m['proof_pack_completeness_pct_prod']}"
        )
    if gate["status"] != "pass":
        errors.append(
            f"expected gate=pass, got {gate['status']} "
            f"(reasons: {gate.get('blocked_by', [])})"
        )
    if m.get("stage_signal") != "green":
        errors.append(f"expected signal=green, got {m.get('stage_signal')}")

    if errors:
        return TestResult(name, False, "; ".join(errors))
    return TestResult(
        name, True,
        f"exec_verif_pct_prod=100.0; proof_pack_pct_prod=100.0; gate=pass; signal=green"
    )


def test_prod_data_low_verification_blocks() -> TestResult:
    """
    Prod executes: 30 completed + 20 failed = 60% < 85% threshold → gate block.
    """
    name = "prod_data_low_verification_blocks"
    with tempfile.TemporaryDirectory() as td:
        runs = [
            _make_run(
                f"pr-ok-{i:02d}",
                run_type="prod",
                verification_status="completed",
                proof_path=f"/proof/pr-ok-{i:02d}.json",
            )
            for i in range(30)
        ] + [
            _make_run(
                f"pr-fail-{i:02d}",
                run_type="prod",
                verification_status="failed",
            )
            for i in range(20)
        ]
        root = _make_fixture_root(
            Path(td),
            runs,
            test_reports=[_test_report(total=100, passed=96, failed=4)],
        )
        result = _compute_scorecard(root, days=7)

    m = result["metrics"]
    gate = result["stage_gate"]

    expected_pct = round(30 / 50 * 100, 2)  # 60.0

    errors: List[str] = []
    if float(m["execute_verification_success_pct_prod"]) != expected_pct:
        errors.append(
            f"expected exec_verification_success_pct_prod={expected_pct}, "
            f"got {m['execute_verification_success_pct_prod']}"
        )
    if gate["status"] != "block":
        errors.append(f"expected gate=block, got {gate['status']}")

    if errors:
        return TestResult(name, False, "; ".join(errors))
    return TestResult(
        name, True,
        f"exec_verif_pct_prod={expected_pct}; gate=block as expected"
    )


def test_empty_execute_window_no_crash() -> TestResult:
    """
    Only propose-mode runs — no executes at all.  Primary assertion: no crash
    from zero denominators.  With execute_verification_success_pct=0.0 the
    confidence score is expected to fall below the yellow floor (0.60*96 +
    0.40*0 = 57.6), so the gate correctly blocks — the kernel has never
    been exercised in execute mode.
    """
    name = "empty_execute_window_no_crash"
    with tempfile.TemporaryDirectory() as td:
        runs = [
            _make_run(f"prop-{i:02d}", mode="propose", run_type="test")
            for i in range(10)
        ]
        root = _make_fixture_root(
            Path(td),
            runs,
            test_reports=[_test_report(total=100, passed=96, failed=4)],
        )
        try:
            result = _compute_scorecard(root, days=7)
        except Exception as exc:
            return TestResult(name, False, f"raised exception: {exc}")

    m = result["metrics"]

    errors: List[str] = []
    if int(m["execute_total"]) != 0:
        errors.append(f"expected execute_total=0, got {m['execute_total']}")
    if int(m["execute_total_gate_blocked"]) != 0:
        errors.append(
            f"expected execute_total_gate_blocked=0, "
            f"got {m['execute_total_gate_blocked']}"
        )
    # Confidence must be a finite number — no NaN or exception.
    confidence = float(m["confidence_score"])
    if confidence != confidence:  # NaN check
        errors.append("confidence_score is NaN")
    # With no executes, execute_verification_success_pct=0.0 drags confidence
    # below the yellow floor.  Gate=block is the correct and expected outcome.
    if result["stage_gate"]["status"] != "block":
        errors.append(
            f"expected gate=block (low confidence due to no executes), "
            f"got {result['stage_gate']['status']}"
        )

    if errors:
        return TestResult(name, False, "; ".join(errors))
    return TestResult(
        name, True,
        f"execute_total=0; no crash; confidence={confidence}; gate=block (correct — no execute history)"
    )


def test_duplicate_run_ids_blocks() -> TestResult:
    """
    Two execute runs share the same id → duplicate_run_ids=1 > 0 → gate block.
    """
    name = "duplicate_run_ids_blocks"
    with tempfile.TemporaryDirectory() as td:
        runs = [
            _make_run(
                "shared-id",
                run_type="prod",
                verification_status="completed",
                proof_path="/proof/dup-a.json",
            ),
            _make_run(
                "shared-id",  # same id — duplicate
                run_type="prod",
                verification_status="completed",
                proof_path="/proof/dup-b.json",
            ),
        ] + [
            _make_run(
                f"pr-{i:02d}",
                run_type="prod",
                verification_status="completed",
                proof_path=f"/proof/pr-{i:02d}.json",
            )
            for i in range(10)
        ]
        root = _make_fixture_root(
            Path(td),
            runs,
            test_reports=[_test_report(total=100, passed=96, failed=4)],
        )
        result = _compute_scorecard(root, days=7)

    m = result["metrics"]
    gate = result["stage_gate"]

    errors: List[str] = []
    if int(m["duplicate_run_ids"]) < 1:
        errors.append(
            f"expected duplicate_run_ids>=1, got {m['duplicate_run_ids']}"
        )
    if gate["status"] != "block":
        errors.append(f"expected gate=block, got {gate['status']}")

    if errors:
        return TestResult(name, False, "; ".join(errors))
    return TestResult(
        name, True,
        f"duplicate_run_ids={m['duplicate_run_ids']}; gate=block as expected"
    )


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------


def run_suite(root: Path) -> List[TestResult]:
    """root is the project root — used only for writing the report artifact."""
    tests = [
        test_gate_blocked_runs_excluded_from_denominator,
        test_no_prod_data_high_test_pass_rate_passes,
        test_no_prod_data_low_test_pass_rate_blocks,
        test_proof_pack_gate_waived_when_no_prod_data,
        test_prod_data_healthy_passes,
        test_prod_data_low_verification_blocks,
        test_empty_execute_window_no_crash,
        test_duplicate_run_ids_blocks,
    ]
    return [t() for t in tests]


def write_report(root: Path, results: List[TestResult]) -> Path:
    out_dir = root / "artifacts" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = out_dir / f"go_no_go_{stamp}_scorecard_regression.json"
    payload = {
        "generated_at": _utc_now_iso(),
        "suite": "scorecard_regression",
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "results": [
            {"name": r.name, "passed": r.passed, "detail": r.detail}
            for r in results
        ],
    }
    report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]

    results = run_suite(root)
    report = write_report(root, results)

    print("Operator Kernel Scorecard Regression Tests")
    for row in results:
        status = "PASS" if row.passed else "FAIL"
        print(f"- {status}: {row.name} | {row.detail}")
    print(f"- report: {report}")

    if args.strict and any(not r.passed for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
