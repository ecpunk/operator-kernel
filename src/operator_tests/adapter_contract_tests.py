import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from operator_kernel.adapter_contract import normalize_adapter_payload
from operator_kernel.lane_catalog import resolve_action_id
from operator_kernel.schema_validator import validate_data_against_schema
from operator_notify import notify_event


@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_schema(root: Path) -> Dict:
    path = root / "config" / "schemas" / "adapter_payload.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_schema_voice_and_mobile(root: Path) -> TestResult:
    schema = _load_schema(root)
    rows = [
        {
            "adapter": "voice",
            "channel": "voice",
            "utterance": "Plex playback failing on LAN",
            "timestamp": _utc_now_iso(),
            "device_id": "voice-mic-01",
        },
        {
            "adapter": "mobile",
            "channel": "text",
            "utterance": "Plex playback failing on LAN",
            "timestamp": _utc_now_iso(),
            "device_id": "mobile-app-01",
        },
    ]
    for row in rows:
        errs = validate_data_against_schema(row, schema)
        if errs:
            return TestResult("schema_voice_and_mobile", False, "; ".join(errs))
    return TestResult("schema_voice_and_mobile", True, "voice and mobile payloads pass schema")


def test_normalization_parity(root: Path) -> TestResult:
    catalog = root / "config" / "lane_catalog.json"
    voice = normalize_adapter_payload(
        {
            "adapter": "voice",
            "channel": "voice",
            "utterance": "Plex and Sonarr both not responding",
            "timestamp": _utc_now_iso(),
        }
    )
    mobile = normalize_adapter_payload(
        {
            "adapter": "mobile",
            "channel": "text",
            "utterance": "Plex and Sonarr both not responding",
            "timestamp": _utc_now_iso(),
        }
    )
    v_action = resolve_action_id(str(voice.get("task", "")), catalog)
    m_action = resolve_action_id(str(mobile.get("task", "")), catalog)
    if v_action != m_action:
        return TestResult(
            "normalization_parity",
            False,
            f"voice action={v_action} mobile action={m_action}",
        )
    v_norm = str(voice.get("task_normalized", ""))
    m_norm = str(mobile.get("task_normalized", ""))
    if v_norm != m_norm:
        return TestResult(
            "normalization_parity",
            False,
            f"normalized task mismatch voice='{v_norm}' mobile='{m_norm}'",
        )
    return TestResult("normalization_parity", True, f"parity action={v_action}")


def test_parity_phrase_matrix(root: Path) -> TestResult:
    catalog = root / "config" / "lane_catalog.json"
    rows = [
        (
            "plex_simple",
            "Plex playback failing on LAN",
            "  plex playback failing on lan  ",
            "plex_safe_recovery_v1",
        ),
        (
            "sonarr_voice_like",
            "Uh sonarr web ui is kind of down right now",
            "sonarr web ui is kind of down right now",
            "sonarr_safe_recovery_v1",
        ),
        (
            "home_assistant_alias",
            "Home Assistant dashboard unavailable",
            "homeassistant dashboard unavailable",
            "homeassistant_safe_recovery_v1",
        ),
        (
            "sab_abbrev",
            "sab queue appears stalled",
            "SAB queue appears stalled",
            "sabnzbd_safe_recovery_v1",
        ),
        (
            "mixed_service_order",
            "Plex and Sonarr both not responding",
            "plex and sonarr both not responding",
            "plex_safe_recovery_v1",
        ),
        (
            "no_lane_match",
            "show me a quick system summary",
            "show me a quick system summary",
            "",
        ),
    ]

    failures: List[str] = []
    for name, voice_text, mobile_text, expected_action in rows:
        voice = normalize_adapter_payload(
            {
                "adapter": "voice",
                "channel": "voice",
                "utterance": voice_text,
                "timestamp": _utc_now_iso(),
            }
        )
        mobile = normalize_adapter_payload(
            {
                "adapter": "mobile",
                "channel": "text",
                "utterance": mobile_text,
                "timestamp": _utc_now_iso(),
            }
        )

        v_action = resolve_action_id(str(voice.get("task", "")), catalog)
        m_action = resolve_action_id(str(mobile.get("task", "")), catalog)

        if v_action != m_action:
            failures.append(f"{name}: voice action={v_action} mobile action={m_action}")
            continue

        if v_action != expected_action:
            failures.append(f"{name}: expected action={expected_action} got={v_action}")

    if failures:
        return TestResult("parity_phrase_matrix", False, "; ".join(failures))
    return TestResult(
        "parity_phrase_matrix",
        True,
        "voice/mobile phrase matrix resolved with matching action semantics",
    )


def _classify_mode_intent_example(task: str) -> str:
    lowered = task.lower()
    inspect_markers = ["status", "what", "why", "show", "check", "inspect"]
    propose_markers = ["propose", "plan", "recommend", "suggest", "would you"]
    execute_markers = ["fix", "restart", "apply", "execute", "do it", "run"]

    if any(token in lowered for token in propose_markers):
        return "propose"
    if any(token in lowered for token in execute_markers):
        return "execute"
    if any(token in lowered for token in inspect_markers):
        return "inspect"
    return "inspect"


def test_mode_intent_examples_parity(root: Path) -> TestResult:
    catalog = root / "config" / "lane_catalog.json"
    rows = [
        (
            "inspect",
            "What is wrong with Plex right now",
            "what is wrong with plex right now",
            "inspect",
        ),
        (
            "propose",
            "Propose a fix path for Sonarr web ui down",
            "propose a fix path for sonarr web ui down",
            "propose",
        ),
        (
            "execute",
            "Fix Plex now",
            "fix plex now",
            "execute",
        ),
    ]

    failures: List[str] = []
    for name, voice_text, mobile_text, expected_mode in rows:
        voice = normalize_adapter_payload(
            {
                "adapter": "voice",
                "channel": "voice",
                "utterance": voice_text,
                "timestamp": _utc_now_iso(),
            }
        )
        mobile = normalize_adapter_payload(
            {
                "adapter": "mobile",
                "channel": "text",
                "utterance": mobile_text,
                "timestamp": _utc_now_iso(),
            }
        )

        v_mode = _classify_mode_intent_example(str(voice.get("task", "")))
        m_mode = _classify_mode_intent_example(str(mobile.get("task", "")))
        if v_mode != m_mode:
            failures.append(f"{name}: voice mode={v_mode} mobile mode={m_mode}")
            continue
        if v_mode != expected_mode:
            failures.append(f"{name}: expected mode={expected_mode} got={v_mode}")

        v_action = resolve_action_id(str(voice.get("task", "")), catalog)
        m_action = resolve_action_id(str(mobile.get("task", "")), catalog)
        if v_action != m_action:
            failures.append(f"{name}: voice action={v_action} mobile action={m_action}")

    if failures:
        return TestResult("mode_intent_examples_parity", False, "; ".join(failures))
    return TestResult(
        "mode_intent_examples_parity",
        True,
        "inspect/propose/execute examples classify and resolve consistently across voice/mobile",
    )


def run_suite(root: Path) -> List[TestResult]:
    tests = [
        test_schema_voice_and_mobile,
        test_normalization_parity,
        test_parity_phrase_matrix,
        test_mode_intent_examples_parity,
    ]
    return [t(root) for t in tests]


def write_report(root: Path, results: List[TestResult]) -> Path:
    out_dir = root / "artifacts" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = out_dir / f"adapter_contract_{stamp}.json"
    payload = {
        "generated_at": _utc_now_iso(),
        "suite": "adapter_contract",
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
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    try:
        notify_event(
            root=root,
            source="operator_tests.adapter_contract_tests",
            event="test_suite",
            status="start",
            message="adapter contract test suite started",
            milestone="suite_start",
            details={"suite": "adapter_contract", "strict": bool(args.strict)},
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
                source="operator_tests.adapter_contract_tests",
                event="test_case",
                status="failure",
                message=f"adapter contract test failed: {row.name}",
                severity="warning",
                milestone="test_case_failed",
                details={"test": row.name, "detail": row.detail},
            )
        except Exception:
            pass

    print("Operator Kernel Adapter Contract Tests")
    for row in results:
        status = "PASS" if row.passed else "FAIL"
        print(f"- {status}: {row.name} | {row.detail}")
    print(f"- report: {report}")

    try:
        failed_count = sum(1 for r in results if not r.passed)
        notify_event(
            root=root,
            source="operator_tests.adapter_contract_tests",
            event="test_suite",
            status="complete" if failed_count == 0 else "failure",
            message="adapter contract test suite completed",
            severity="success" if failed_count == 0 else "error",
            milestone="suite_complete",
            details={
                "suite": "adapter_contract",
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
