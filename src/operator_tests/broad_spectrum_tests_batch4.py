import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from operator_runtime.context_loader import build_preflight_context


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


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_guardrails_budget_policy_present(root: Path) -> TestResult:
    guardrails = _load_json(root / "config" / "guardrails.json")
    budgets = guardrails.get("budgets", {})
    prompt = int(budgets.get("max_prompt_tokens", 0) or 0)
    daily = int(budgets.get("max_daily_tokens", 0) or 0)

    if prompt > 0 and daily > 0:
        return TestResult(
            "guardrails_budget_policy_present",
            "cost_policy",
            True,
            f"budget caps present (prompt={prompt}, daily={daily})",
        )
    return TestResult(
        "guardrails_budget_policy_present",
        "cost_policy",
        False,
        "budget caps missing or non-positive in guardrails",
    )


def test_budget_schema_fail_closed(root: Path) -> TestResult:
    guardrails_path = root / "config" / "guardrails.json"
    original = _load_json(guardrails_path)
    try:
        mutated = dict(original)
        budgets = dict(mutated.get("budgets", {}))
        budgets.pop("max_daily_tokens", None)
        mutated["budgets"] = budgets
        _write_json(guardrails_path, mutated)

        code, out, err = _run_cmd(
            root,
            ["python3", "src/operator_runtime/main.py", "--mode", "inspect", "--task", "Plex playback failing on LAN"],
        )
        combined = (out or "") + "\n" + (err or "")
        if "Schema validation failure" in combined and "missing required key 'max_daily_tokens'" in combined:
            return TestResult(
                "budget_schema_fail_closed",
                "cost_policy",
                True,
                "runtime failed closed on malformed budget policy",
            )
        return TestResult(
            "budget_schema_fail_closed",
            "cost_policy",
            False,
            combined.strip(),
        )
    finally:
        _write_json(guardrails_path, original)


def test_provider_budget_status_structured(root: Path) -> TestResult:
    status_path = Path("/opt/stack/services/apps/dashboard/provider_budget_status.json")
    if not status_path.exists():
        return TestResult(
            "provider_budget_status_structured",
            "cost_policy",
            False,
            "provider_budget_status.json missing",
        )

    payload = _load_json(status_path)
    thresholds = payload.get("thresholds", {})
    alerts = payload.get("alerts", [])
    providers = payload.get("providers", {})

    has_thresholds = "warn_percent" in thresholds and "critical_percent" in thresholds
    has_alert_shape = all(
        isinstance(a, dict)
        and "provider" in a
        and "level" in a
        and "percent" in a
        and "message" in a
        for a in alerts
    )
    has_provider_fields = all(
        isinstance(v, dict) and "spent" in v and "budget" in v and "percent" in v
        for v in providers.values()
    )

    if has_thresholds and has_alert_shape and has_provider_fields:
        return TestResult(
            "provider_budget_status_structured",
            "cost_policy",
            True,
            "provider budget status is explicit and structured",
        )

    return TestResult(
        "provider_budget_status_structured",
        "cost_policy",
        False,
        "provider budget status missing thresholds/alert/provider structure",
    )


def test_context_optional_overflow_stability(root: Path) -> TestResult:
    cfg_path = root / "config" / "context_sources.json"
    original = _load_json(cfg_path)
    try:
        mutated = dict(original)
        sources = list(mutated.get("sources", []))
        for idx in range(40):
            sources.append(
                {
                    "id": f"optional_overflow_{idx}",
                    "path": f"/opt/stack/services/does/not/exist_optional_{idx}.md",
                    "required": False,
                }
            )
        mutated["sources"] = sources
        _write_json(cfg_path, mutated)

        code, out, err = _run_cmd(
            root,
            ["python3", "src/operator_runtime/main.py", "--mode", "inspect", "--task", "Plex playback failing on LAN"],
        )
        if code == 0 and "Inspect Contract" in out and "Preflight failed" not in out:
            return TestResult(
                "context_optional_overflow_stability",
                "context_boundary",
                True,
                "runtime remained stable with high optional context-source count",
            )
        return TestResult(
            "context_optional_overflow_stability",
            "context_boundary",
            False,
            err.strip() or out.strip(),
        )
    finally:
        _write_json(cfg_path, original)


def test_context_preview_cap_under_stress(root: Path) -> TestResult:
    cfg_path = root / "config" / "context_sources.json"
    original = _load_json(cfg_path)
    temp_file = root / "artifacts" / "tests" / "context_stress_preview_source.txt"
    try:
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.write_text("\n".join(f"line_{i}" for i in range(120)) + "\n", encoding="utf-8")

        mutated = dict(original)
        sources = list(mutated.get("sources", []))
        sources.append(
            {
                "id": "preview_cap_stress",
                "path": str(temp_file),
                "required": False,
            }
        )
        mutated["sources"] = sources
        _write_json(cfg_path, mutated)

        rows = build_preflight_context(cfg_path)
        row = next((r for r in rows if r.get("id") == "preview_cap_stress"), None)
        if not row:
            return TestResult("context_preview_cap_under_stress", "context_boundary", False, "preview test row missing")

        preview = str(row.get("preview", ""))
        preview_lines = len(preview.splitlines()) if preview else 0
        if preview_lines <= 20:
            return TestResult(
                "context_preview_cap_under_stress",
                "context_boundary",
                True,
                f"preview stayed bounded at {preview_lines} lines",
            )
        return TestResult(
            "context_preview_cap_under_stress",
            "context_boundary",
            False,
            f"preview exceeded expected cap: {preview_lines}",
        )
    finally:
        _write_json(cfg_path, original)


def test_context_required_missing_under_overflow(root: Path) -> TestResult:
    cfg_path = root / "config" / "context_sources.json"
    original = _load_json(cfg_path)
    try:
        mutated = dict(original)
        sources = list(mutated.get("sources", []))
        for idx in range(30):
            sources.append(
                {
                    "id": f"optional_noise_{idx}",
                    "path": f"/opt/stack/services/not-real/noise_{idx}.md",
                    "required": False,
                }
            )
        sources.append(
            {
                "id": "required_missing_overflow",
                "path": "/opt/stack/services/not-real/required_missing_overflow.md",
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
                "propose",
                "--channel",
                "voice",
                "--task",
                "Sonarr not responding on web",
            ],
        )
        if code == 0 and "Preflight failed: required context missing" in out:
            return TestResult(
                "context_required_missing_under_overflow",
                "context_boundary",
                True,
                "required context failure still blocks under high optional-source noise",
            )
        return TestResult(
            "context_required_missing_under_overflow",
            "context_boundary",
            False,
            err.strip() or out.strip(),
        )
    finally:
        _write_json(cfg_path, original)


def run_suite(root: Path) -> List[TestResult]:
    tests = [
        test_guardrails_budget_policy_present,
        test_budget_schema_fail_closed,
        test_provider_budget_status_structured,
        test_context_optional_overflow_stability,
        test_context_preview_cap_under_stress,
        test_context_required_missing_under_overflow,
    ]
    results: List[TestResult] = []
    for test in tests:
        results.append(test(root))
    return results


def write_report(root: Path, results: List[TestResult]) -> Path:
    out_dir = root / "artifacts" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = out_dir / f"broad_spectrum_batch4_{stamp}.json"

    payload = {
        "generated_at": _utc_now_iso(),
        "suite": "broad_spectrum_batch4",
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

    print("Broad Spectrum Test Suite Batch 4")
    for result in results:
        state = "PASS" if result.passed else "FAIL"
        print(f"- {state} {result.category}.{result.name}: {result.detail}")
    print(f"- summary: {sum(1 for r in results if r.passed)}/{len(results)} passed")
    print(f"- report: {report}")

    if any(not r.passed for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
