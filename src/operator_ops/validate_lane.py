"""Lane-authoring quality gate.

Validates lane spec JSON files against the schema *and* a set of
higher-level checklist rules that the schema alone cannot enforce.
Intended to run before merge or onboarding so authoring mistakes are
caught early.

Usage:
    # Validate one lane
    python src/operator_ops/validate_lane.py --lane config/lanes/plex_safe_recovery_v1.json

    # Validate all lanes in config/lanes/ (skips _template)
    python src/operator_ops/validate_lane.py --all

Exit code 0 = all checks passed, 1 = one or more failures.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEMPLATE_PLACEHOLDERS = {
    "myservice",
    "candidate_service",
    "myservice_safe_recovery_v1",
}
TEMPLATE_ENDPOINTS = {
    "http://127.0.0.1:8080",
}
TEMPLATE_DESCRIPTIONS = {
    "safely recover service availability using non-destructive checks and optional controlled restart.",
}

# Commands that should never appear in verify_probes (destructive/mutating).
DANGEROUS_PATTERNS = [
    r"\brm\s+-",
    r"\bdocker\s+rm\b",
    r"\bdocker\s+stop\b",
    r"\bdocker\s+kill\b",
    r"\bdocker\s+restart\b",
    r"\bsystemctl\s+(stop|restart|disable)\b",
    r"\breboot\b",
    r"\bshutdown\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
]


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def _check_schema(spec: Dict[str, Any], schema: Dict[str, Any], _root: Path) -> List[str]:
    """Run the project's own schema validator."""
    from operator_kernel.schema_validator import validate_data_against_schema
    return validate_data_against_schema(spec, schema)


def _check_filename_match(spec: Dict[str, Any], spec_path: Path) -> List[str]:
    expected = spec_path.stem
    action_id = spec.get("action_id", "")
    if action_id != expected:
        return [f"action_id '{action_id}' does not match filename '{spec_path.name}' (expected '{expected}')"]
    return []


def _check_no_placeholders(spec: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    service = str(spec.get("service", "")).strip().lower()
    service_is_placeholder = service in TEMPLATE_PLACEHOLDERS or not service
    if service_is_placeholder:
        errors.append(f"service '{spec.get('service')}' looks like a placeholder — set to the real Docker service name")

    action_id = str(spec.get("action_id", "")).strip().lower()
    if action_id in TEMPLATE_PLACEHOLDERS:
        errors.append(f"action_id '{spec.get('action_id')}' is a template placeholder")

    endpoint = str(spec.get("endpoint", "")).strip()
    if not endpoint:
        errors.append("endpoint is empty — set to the real service URL")
    elif endpoint in TEMPLATE_ENDPOINTS and service_is_placeholder:
        # Only flag the template-default endpoint when the service is also
        # a placeholder.  Real services (e.g. sabnzbd) may legitimately
        # listen on 127.0.0.1:8080.
        errors.append(f"endpoint '{endpoint}' looks like a placeholder — set to the real service URL")

    desc = str(spec.get("description", "")).strip().lower().rstrip(".")
    if desc in TEMPLATE_DESCRIPTIONS:
        errors.append("description is the template default — write a service-specific description")

    return errors


def _check_endpoint_url(spec: Dict[str, Any]) -> List[str]:
    endpoint = str(spec.get("endpoint", "")).strip()
    if not endpoint:
        return ["endpoint is empty"]
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https"):
        return [f"endpoint scheme '{parsed.scheme}' is not http or https"]
    if not parsed.hostname:
        return ["endpoint has no hostname"]
    return []


def _check_verify_rollback(spec: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    verify = spec.get("verify", [])
    if not verify:
        errors.append("verify list is empty — at least one verification step is required")
    rollback = spec.get("rollback", [])
    if not rollback:
        errors.append("rollback list is empty — at least one rollback step is required")
    return errors


def _check_probe_safety(spec: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for probe in spec.get("verify_probes", []):
        cmd = str(probe.get("command", ""))
        name = probe.get("name", "?")
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                errors.append(f"verify_probe '{name}' contains dangerous command pattern: {pattern}")
    return errors


def _check_probe_required_flag(spec: Dict[str, Any]) -> List[str]:
    probes = spec.get("verify_probes", [])
    if not probes:
        return []
    if not any(probe.get("required") is True for probe in probes):
        return ["no verify_probe is marked required=true — at least one should be required"]
    return []


def _check_sane_tunables(spec: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    retries = spec.get("warmup_probe_retries", 0)
    if isinstance(retries, int) and retries < 1:
        errors.append(f"warmup_probe_retries={retries} is too low (minimum 1)")

    sleep_s = spec.get("warmup_probe_sleep_seconds", 0)
    if isinstance(sleep_s, int) and sleep_s < 1:
        errors.append(f"warmup_probe_sleep_seconds={sleep_s} is too low (minimum 1)")

    log_tail = spec.get("log_tail", 0)
    if isinstance(log_tail, int) and log_tail < 10:
        errors.append(f"log_tail={log_tail} is too low (minimum 10 for useful context)")

    return errors


def _check_catalog_registered(spec: Dict[str, Any], root: Path) -> List[str]:
    action_id = spec.get("action_id", "")
    catalog_path = root / "config" / "lane_catalog.json"
    if not catalog_path.exists():
        return ["lane_catalog.json not found — cannot verify catalog registration"]
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    registered = {
        str(m.get("action_id", ""))
        for m in catalog.get("mappings", [])
    }
    if action_id not in registered:
        return [f"action_id '{action_id}' is not registered in lane_catalog.json — add a mapping so the lane is resolvable"]
    return []


def _check_lifecycle_registered(spec: Dict[str, Any], root: Path) -> List[str]:
    action_id = spec.get("action_id", "")
    lifecycle_path = root / "config" / "lane_lifecycle.json"
    if not lifecycle_path.exists():
        return ["lane_lifecycle.json not found — cannot verify lifecycle registration"]
    lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    if action_id not in lifecycle.get("actions", {}):
        return [f"action_id '{action_id}' is not registered in lane_lifecycle.json — add an entry with state candidate or active"]
    return []


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def validate_lane(spec_path: Path, root: Path) -> Tuple[bool, List[str]]:
    """Run all quality-gate checks on a single lane spec file.

    Returns (passed, list-of-error-strings).
    """
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return False, [f"cannot read lane spec: {exc}"]

    schema_path = root / "config" / "schemas" / "lane_spec.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return False, [f"cannot read lane schema: {exc}"]

    errors: List[str] = []
    errors.extend(_check_schema(spec, schema, root))
    errors.extend(_check_filename_match(spec, spec_path))
    errors.extend(_check_no_placeholders(spec))
    errors.extend(_check_endpoint_url(spec))
    errors.extend(_check_verify_rollback(spec))
    errors.extend(_check_probe_safety(spec))
    errors.extend(_check_probe_required_flag(spec))
    errors.extend(_check_sane_tunables(spec))
    errors.extend(_check_catalog_registered(spec, root))
    errors.extend(_check_lifecycle_registered(spec, root))

    return (len(errors) == 0, errors)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lane-authoring quality gate — validate lane specs before merge or run."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--lane", type=str, help="Path to a single lane spec JSON file")
    group.add_argument("--all", action="store_true", help="Validate all lanes in config/lanes/ (skips _template)")
    parser.add_argument("--root", type=str, default=None, help="Project root (auto-detected if omitted)")
    args = parser.parse_args()

    if args.root:
        root = Path(args.root).resolve()
    else:
        root = Path(__file__).resolve().parents[2]

    if args.all:
        lanes_dir = root / "config" / "lanes"
        spec_paths = sorted(
            p for p in lanes_dir.glob("*.json")
            if not p.name.startswith("_")
        )
    else:
        spec_paths = [Path(args.lane).resolve()]

    total = 0
    failed = 0
    for spec_path in spec_paths:
        total += 1
        passed, errors = validate_lane(spec_path, root)
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {spec_path.name}")
        if errors:
            failed += 1
            for e in errors:
                print(f"       - {e}")

    print()
    print(f"{total} lane(s) checked, {total - failed} passed, {failed} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
