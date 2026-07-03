import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from operator_kernel.lane_catalog import load_lane_spec
from operator_runtime.observation_engine import generate_recommendations, load_signature_rules


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run_command(command: List[str], mutating: bool = False) -> Dict[str, Any]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return {
        "timestamp": _utc_now_iso(),
        "command": " ".join(command),
        "mutating": mutating,
        "exit_code": result.returncode,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
    }


def _blocked_result(
    reason: str,
    checks: List[str],
    rollback: List[str],
    notes: List[str],
) -> Dict[str, Any]:
    return {
        "precheck": {
            "status": "failed",
            "items": [reason],
        },
        "command_transcript": [],
        "verification": {
            "status": "failed",
            "checks": checks,
            "probe_results": [],
            "required_probe_failures": [],
        },
        "rollback": rollback,
        "execution_notes": notes,
        "observations": [],
        "recommendations": [],
    }


def execute_media_lane(plan: Dict[str, Any], apply: bool, lanes_dir: Path) -> Dict[str, Any]:
    action_id = plan.get("action_id", "")
    transcript: List[Dict[str, Any]] = []

    if not action_id:
        return {
            "precheck": {
                "status": "failed",
                "items": ["Missing action_id on approved plan."],
            },
            "command_transcript": transcript,
            "verification": {
                "status": "failed",
                "checks": ["No execution occurred due to missing action_id."],
            },
            "rollback": plan.get("rollback", []),
            "execution_notes": ["Execution blocked by action allowlist."],
        }

    try:
        spec = load_lane_spec(action_id, lanes_dir)
    except FileNotFoundError as exc:
        return {
            "precheck": {
                "status": "failed",
                "items": [str(exc)],
            },
            "command_transcript": transcript,
            "verification": {
                "status": "failed",
                "checks": ["No execution occurred due to missing lane spec."],
            },
            "rollback": plan.get("rollback", []),
            "execution_notes": ["Execution blocked by lane spec lookup."],
        }

    service = spec.get("service", "")
    endpoint = spec.get("endpoint", "")
    log_tail = int(spec.get("log_tail", 40))
    retries = int(spec.get("warmup_probe_retries", 4))
    sleep_seconds = int(spec.get("warmup_probe_sleep_seconds", 3))
    ok_prefixes = list(spec.get("http_ok_prefixes", ["HTTP_2", "HTTP_3"]))
    curl_insecure = "-k " if endpoint.startswith("https://") else ""

    precheck_items = [
        "Plan action_id is allowlisted.",
        f"Execution lane is media_incident:{service}.",
        f"Apply flag: {apply}",
    ]

    transcript.append(
        _run_command(["docker", "ps", "--filter", f"name={service}", "--format", "{{.Names}}\t{{.Status}}"])
    )
    transcript.append(
        _run_command(
            [
                "bash",
                "-lc",
                f"curl -sS {curl_insecure}-m 5 -o /dev/null -w 'HTTP_%{{http_code}}' {endpoint} || true",
            ]
        )
    )
    log_row = _run_command(["docker", "logs", "--tail", str(log_tail), service])
    transcript.append(log_row)

    execution_notes: List[str] = []
    if apply:
        transcript.append(_run_command(["docker", "restart", service], mutating=True))
        execution_notes.append(f"Applied mutating step: docker restart {service}")
    else:
        execution_notes.append("Dry run only: restart command not executed")

    transcript.append(
        _run_command(["docker", "ps", "--filter", f"name={service}", "--format", "{{.Names}}\t{{.Status}}"])
    )
    transcript.append(
        _run_command(
            [
                "bash",
                "-lc",
                f"for i in $(seq 1 {retries}); do out=$(curl -sS {curl_insecure}-m 5 -o /dev/null -w 'HTTP_%{{http_code}}' {endpoint} || true); echo \"$out\"; [ \"$out\" != \"HTTP_000\" ] && break; sleep {sleep_seconds}; done | tail -n 1",
            ]
        )
    )

    verification_checks = list(spec.get("verify", plan.get("verify", [])))
    verify_probes = list(spec.get("verify_probes", []))

    container_ok = any(
        row.get("command", "").startswith("docker ps")
        and row.get("exit_code", 1) == 0
        and service in (row.get("stdout", ""))
        for row in transcript
    )
    http_rows = [row for row in transcript if "HTTP_" in row.get("stdout", "")]
    http_ok = False
    if http_rows:
        last_http = http_rows[-1].get("stdout", "")
        http_ok = any(last_http.startswith(prefix) for prefix in ok_prefixes)

    required_probe_failures: List[str] = []
    probe_results: List[Dict[str, Any]] = []
    for probe in verify_probes:
        name = str(probe.get("name", "")).strip() or "unnamed_probe"
        cmd = str(probe.get("command", "")).strip()
        expected = str(probe.get("expected_substring", ""))
        required = bool(probe.get("required", True))
        if not cmd:
            if required:
                required_probe_failures.append(name)
            probe_results.append(
                {
                    "name": name,
                    "required": required,
                    "status": "failed",
                    "reason": "missing command",
                }
            )
            verification_checks.append(f"verify probe '{name}' failed: missing command")
            continue

        row = _run_command(["bash", "-lc", cmd])
        transcript.append(row)
        stdout = str(row.get("stdout", ""))
        ok = int(row.get("exit_code", 1)) == 0
        if expected:
            ok = ok and (expected in stdout)

        if required and not ok:
            required_probe_failures.append(name)

        probe_results.append(
            {
                "name": name,
                "required": required,
                "status": "passed" if ok else "failed",
                "expected_substring": expected,
            }
        )
        verification_checks.append(
            f"verify probe '{name}' {'passed' if ok else 'failed'}"
        )

    verification_status = "completed" if (container_ok and http_ok and not required_probe_failures) else "failed"

    observations: List[str] = []
    log_stdout = log_row.get("stdout", "")
    warning_lines = [line.strip() for line in log_stdout.splitlines() if "[Warn]" in line]
    if warning_lines:
        observations.append(f"Detected {len(warning_lines)} warning log lines in recent tail.")
        observations.extend(warning_lines[:3])

    signatures_path = lanes_dir.parent / "observation_signatures.json"
    signature_rules = []
    if signatures_path.exists():
        signature_rules = load_signature_rules(signatures_path)
    recommendations = generate_recommendations(observations, signature_rules)

    return {
        "precheck": {
            "status": "completed",
            "items": precheck_items,
        },
        "command_transcript": transcript,
        "verification": {
            "status": verification_status,
            "checks": verification_checks,
            "probe_results": probe_results,
            "required_probe_failures": required_probe_failures,
        },
        "rollback": list(spec.get("rollback", plan.get("rollback", []))),
        "execution_notes": execution_notes,
        "observations": observations,
        "recommendations": recommendations,
    }


def execute_lane(plan: Dict[str, Any], apply: bool, lanes_dir: Path) -> Dict[str, Any]:
    action_id = str(plan.get("action_id", "")).strip()
    if not action_id:
        return _blocked_result(
            reason="Missing action_id on approved plan.",
            checks=["No execution occurred due to missing action_id."],
            rollback=list(plan.get("rollback", [])),
            notes=["Execution blocked by action allowlist."],
        )

    try:
        spec = load_lane_spec(action_id, lanes_dir)
    except FileNotFoundError as exc:
        return _blocked_result(
            reason=str(exc),
            checks=["No execution occurred due to missing lane spec."],
            rollback=list(plan.get("rollback", [])),
            notes=["Execution blocked by lane spec lookup."],
        )

    lane_class = str(spec.get("lane_class", "")).strip() or "http_service_recovery"
    if lane_class == "http_service_recovery":
        return execute_media_lane(plan, apply=apply, lanes_dir=lanes_dir)

    return _blocked_result(
        reason=f"Unsupported lane_class '{lane_class}' for action_id '{action_id}'.",
        checks=[f"No execution occurred due to unsupported lane_class: {lane_class}."],
        rollback=list(spec.get("rollback", plan.get("rollback", []))),
        notes=["Execution blocked by lane_class dispatcher policy."],
    )
