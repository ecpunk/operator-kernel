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
    path.parent.mkdir(parents=True, exist_ok=True)
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
    plan_path = _extract(r"- path:\s*(\S+)", out)
    if not plan_id or not token or not plan_path:
        return None, "propose output missing plan details"

    p = Path(plan_path)
    if not p.exists():
        return None, f"plan file not found: {p}"

    plan = _load_json(p)
    plan["_approval_token"] = token
    plan["_path"] = str(p)
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


def _build_doc_delta_suggestions(root: Path, days: int, min_count: int) -> Path:
    run_index_path = root / "artifacts" / "index" / "run_index.json"
    payload = _load_json(run_index_path) if run_index_path.exists() else {"runs": []}
    runs = list(payload.get("runs", []))

    task_counts: Dict[str, int] = {}
    for run in runs:
        task = str(run.get("task", "")).strip()
        if not task:
            continue
        task_counts[task] = task_counts.get(task, 0) + 1

    recurring = [
        {"task": task, "count": count}
        for task, count in sorted(task_counts.items(), key=lambda x: x[1], reverse=True)
        if count >= min_count
    ]

    suggestions = []
    for row in recurring:
        suggestions.append(
            {
                "type": "doc_delta_suggestion",
                "task": row["task"],
                "count": row["count"],
                "suggested_target": "projects/operator-kernel/10-CONCEPT/OPERATOR_KERNEL_CONCEPT.md",
                "suggested_change": (
                    "Add or refine troubleshooting guidance for recurring task pattern: "
                    + row["task"]
                ),
            }
        )

    out_dir = root / "artifacts" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"doc_delta_suggestions_batch5_{stamp}.json"
    _write_json(
        out,
        {
            "generated_at": _utc_now_iso(),
            "window_days": days,
            "min_count": min_count,
            "suggestions": suggestions,
        },
    )
    return out


def test_recurrence_doc_delta_simulation(root: Path) -> TestResult:
    for _ in range(3):
        plan, detail = _propose(root, "Sonarr not responding on web")
        if not plan:
            return TestResult("recurrence_doc_delta_simulation", "recurrence_doc_delta", False, detail)

    suggestion_path = _build_doc_delta_suggestions(root, days=30, min_count=3)
    payload = _load_json(suggestion_path)
    suggestions = list(payload.get("suggestions", []))
    has_sonarr = any("Sonarr not responding on web" in str(s.get("task", "")) for s in suggestions)

    if suggestions and has_sonarr:
        return TestResult(
            "recurrence_doc_delta_simulation",
            "recurrence_doc_delta",
            True,
            f"doc-delta suggestions generated: {suggestion_path}",
        )
    return TestResult(
        "recurrence_doc_delta_simulation",
        "recurrence_doc_delta",
        False,
        "expected recurring-task suggestion was not generated",
    )


def test_lane_onboarding_resolution(root: Path) -> TestResult:
    catalog_path = root / "config" / "lane_catalog.json"
    plex_lane_path = root / "config" / "lanes" / "plex_safe_recovery_v1.json"
    new_lane_path = root / "config" / "lanes" / "plex_alt_safe_recovery_v1.json"

    original_catalog = _load_json(catalog_path)
    try:
        catalog = dict(original_catalog)
        mappings = list(catalog.get("mappings", []))
        mappings.append({"keywords": ["clawplexalt"], "priority": 300, "action_id": "plex_alt_safe_recovery_v1"})
        catalog["mappings"] = mappings
        _write_json(catalog_path, catalog)

        lane = _load_json(plex_lane_path)
        lane["action_id"] = "plex_alt_safe_recovery_v1"
        _write_json(new_lane_path, lane)

        plan, detail = _propose(root, "clawplexalt web ui unavailable")
        if not plan:
            return TestResult("lane_onboarding_resolution", "lane_onboarding", False, detail)

        if str(plan.get("action_id", "")) == "plex_alt_safe_recovery_v1":
            return TestResult(
                "lane_onboarding_resolution",
                "lane_onboarding",
                True,
                "newly onboarded lane keyword resolved correctly",
            )
        return TestResult(
            "lane_onboarding_resolution",
            "lane_onboarding",
            False,
            f"unexpected action_id: {plan.get('action_id','')}",
        )
    finally:
        _write_json(catalog_path, original_catalog)
        if new_lane_path.exists():
            new_lane_path.unlink()


def test_lane_onboarding_dry_run_execute(root: Path) -> TestResult:
    catalog_path = root / "config" / "lane_catalog.json"
    plex_lane_path = root / "config" / "lanes" / "plex_safe_recovery_v1.json"
    new_lane_path = root / "config" / "lanes" / "plex_alt_safe_recovery_v1.json"

    original_catalog = _load_json(catalog_path)
    try:
        catalog = dict(original_catalog)
        mappings = list(catalog.get("mappings", []))
        mappings.append({"keywords": ["clawplexalt"], "priority": 300, "action_id": "plex_alt_safe_recovery_v1"})
        catalog["mappings"] = mappings
        _write_json(catalog_path, catalog)

        lane = _load_json(plex_lane_path)
        lane["action_id"] = "plex_alt_safe_recovery_v1"
        _write_json(new_lane_path, lane)

        plan, detail = _propose(root, "clawplexalt web ui unavailable")
        if not plan:
            return TestResult("lane_onboarding_dry_run_execute", "lane_onboarding", False, detail)

        code, out, err = _execute(
            root,
            "clawplexalt web ui unavailable",
            str(plan.get("plan_id", "")),
            str(plan.get("_approval_token", "")),
            apply=False,
        )
        if code != 0:
            return TestResult("lane_onboarding_dry_run_execute", "lane_onboarding", False, err.strip() or out.strip())

        proof_path = _extract(r"- proof_pack:\s*(\S+)", out)
        if proof_path and Path(proof_path).exists():
            return TestResult(
                "lane_onboarding_dry_run_execute",
                "lane_onboarding",
                True,
                "onboarded lane executed in dry-run and produced proof",
            )
        return TestResult(
            "lane_onboarding_dry_run_execute",
            "lane_onboarding",
            False,
            "dry-run execute missing proof output",
        )
    finally:
        _write_json(catalog_path, original_catalog)
        if new_lane_path.exists():
            new_lane_path.unlink()


def test_lane_onboarding_invalid_spec_fail_closed(root: Path) -> TestResult:
    catalog_path = root / "config" / "lane_catalog.json"
    new_lane_path = root / "config" / "lanes" / "plex_alt_safe_recovery_v1.json"

    original_catalog = _load_json(catalog_path)
    try:
        catalog = dict(original_catalog)
        mappings = list(catalog.get("mappings", []))
        mappings.append({"keywords": ["clawplexalt"], "priority": 300, "action_id": "plex_alt_safe_recovery_v1"})
        catalog["mappings"] = mappings
        _write_json(catalog_path, catalog)

        invalid_lane = {
            "action_id": "plex_alt_safe_recovery_v1",
            "lane": "media_incident",
            "service": "plex",
            "log_tail": 10,
            "http_ok_prefixes": ["HTTP_2"],
            "warmup_probe_retries": 2,
            "warmup_probe_sleep_seconds": 1,
            "rollback": ["rollback"],
            "verify": ["verify"]
        }
        _write_json(new_lane_path, invalid_lane)

        code, out, err = _run_cmd(
            root,
            ["python3", "src/operator_runtime/main.py", "--mode", "inspect", "--task", "clawplexalt issue"],
        )
        combined = (out or "") + "\n" + (err or "")
        if "Schema validation failure" in combined and "missing required key 'endpoint'" in combined:
            return TestResult(
                "lane_onboarding_invalid_spec_fail_closed",
                "lane_onboarding",
                True,
                "invalid onboarded lane spec failed closed at runtime validation",
            )
        return TestResult(
            "lane_onboarding_invalid_spec_fail_closed",
            "lane_onboarding",
            False,
            combined.strip(),
        )
    finally:
        _write_json(catalog_path, original_catalog)
        if new_lane_path.exists():
            new_lane_path.unlink()


def run_suite(root: Path) -> List[TestResult]:
    tests = [
        test_recurrence_doc_delta_simulation,
        test_lane_onboarding_resolution,
        test_lane_onboarding_dry_run_execute,
        test_lane_onboarding_invalid_spec_fail_closed,
    ]
    results: List[TestResult] = []
    for test in tests:
        results.append(test(root))
    return results


def write_report(root: Path, results: List[TestResult]) -> Path:
    out_dir = root / "artifacts" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = out_dir / f"broad_spectrum_batch5_{stamp}.json"

    _write_json(
        report,
        {
            "generated_at": _utc_now_iso(),
            "suite": "broad_spectrum_batch5",
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
        },
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args()

    root = Path(args.root).resolve()
    results = run_suite(root)
    report = write_report(root, results)

    print("Broad Spectrum Test Suite Batch 5")
    for result in results:
        state = "PASS" if result.passed else "FAIL"
        print(f"- {state} {result.category}.{result.name}: {result.detail}")
    print(f"- summary: {sum(1 for r in results if r.passed)}/{len(results)} passed")
    print(f"- report: {report}")

    if any(not r.passed for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
