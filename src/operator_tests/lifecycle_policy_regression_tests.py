import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple


@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str


def _run(root: Path, args: List[str]) -> Tuple[int, str, str]:
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
    return proc.returncode, (proc.stdout or ""), (proc.stderr or "")


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _force_zero_prod_executes(run_index: Dict[str, Any], action_id: str) -> Dict[str, Any]:
    payload = dict(run_index)
    rows = [dict(r) for r in payload.get("runs", [])]
    changed = 0
    for row in rows:
        if str(row.get("action_id", "")).strip() == action_id and str(row.get("mode", "")) == "execute":
            if str(row.get("run_type", "prod")).strip().lower() == "prod":
                row["run_type"] = "test"
                changed += 1
    payload["runs"] = rows
    payload["_changed"] = changed
    return payload


def test_candidate_exempt_passes(root: Path, action_id: str) -> TestResult:
    cfg_path = root / "config" / "guardrails.json"
    lifecycle_path = root / "config" / "lane_lifecycle.json"
    run_index_path = root / "artifacts" / "index" / "run_index.json"

    original_cfg = _load_json(cfg_path)
    original_lifecycle = _load_json(lifecycle_path)
    original_run_index = _load_json(run_index_path)

    try:
        forced = _force_zero_prod_executes(original_run_index, action_id)
        if int(forced.get("_changed", 0)) <= 0:
            return TestResult("candidate_exempt_passes", False, "no prod execute rows to mutate for test")
        forced.pop("_changed", None)
        _write_json(run_index_path, forced)

        cfg = dict(original_cfg)
        svc = dict(cfg.get("service_scorecards", {}))
        onboarding = dict(svc.get("onboarding", {}))
        onboarding["candidate_gate_exempt"] = True
        svc["onboarding"] = onboarding
        cfg["service_scorecards"] = svc
        _write_json(cfg_path, cfg)

        lifecycle = dict(original_lifecycle)
        actions = dict(lifecycle.get("actions", {}))
        actions[action_id] = {"state": "candidate"}
        lifecycle["actions"] = actions
        _write_json(lifecycle_path, lifecycle)

        code, out, err = _run(
            root,
            ["python3", "src/operator_ops/report_service_scorecards.py", "--days", "7", "--enforce-gate"],
        )
        if code == 0:
            return TestResult("candidate_exempt_passes", True, "candidate lane did not block gate")
        return TestResult("candidate_exempt_passes", False, (err or out).strip())
    finally:
        _write_json(cfg_path, original_cfg)
        _write_json(lifecycle_path, original_lifecycle)
        _write_json(run_index_path, original_run_index)


def test_active_blocks_when_no_prod(root: Path, action_id: str) -> TestResult:
    lifecycle_path = root / "config" / "lane_lifecycle.json"
    run_index_path = root / "artifacts" / "index" / "run_index.json"

    original_lifecycle = _load_json(lifecycle_path)
    original_run_index = _load_json(run_index_path)

    try:
        forced = _force_zero_prod_executes(original_run_index, action_id)
        if int(forced.get("_changed", 0)) <= 0:
            return TestResult("active_blocks_when_no_prod", False, "no prod execute rows to mutate for test")
        forced.pop("_changed", None)
        _write_json(run_index_path, forced)

        lifecycle = dict(original_lifecycle)
        actions = dict(lifecycle.get("actions", {}))
        actions[action_id] = {"state": "active"}
        lifecycle["actions"] = actions
        _write_json(lifecycle_path, lifecycle)

        code, out, err = _run(
            root,
            ["python3", "src/operator_ops/report_service_scorecards.py", "--days", "7", "--enforce-gate"],
        )
        if code == 2:
            return TestResult("active_blocks_when_no_prod", True, "active lane blocked gate as expected")
        return TestResult("active_blocks_when_no_prod", False, (err or out).strip())
    finally:
        _write_json(lifecycle_path, original_lifecycle)
        _write_json(run_index_path, original_run_index)


def run_suite(root: Path, action_id: str) -> List[TestResult]:
    return [
        test_candidate_exempt_passes(root, action_id),
        test_active_blocks_when_no_prod(root, action_id),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action-id", default="homeassistant_safe_recovery_v1")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    results = run_suite(root, args.action_id)

    print("Operator Kernel Lifecycle Policy Regression Tests")
    for row in results:
        status = "PASS" if row.passed else "FAIL"
        print(f"- {status}: {row.name} | {row.detail}")

    if args.strict and any(not r.passed for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
