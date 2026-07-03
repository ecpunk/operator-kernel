import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _type_ok(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return (isinstance(value, int) or isinstance(value, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    return True


def _validate(value: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    errors: List[str] = []

    expected_type = schema.get("type")
    if expected_type and not _type_ok(value, expected_type):
        errors.append(f"{path}: expected type {expected_type}")
        return errors

    enum_values = schema.get("enum")
    if enum_values is not None and value not in enum_values:
        errors.append(f"{path}: value {value!r} not in enum {enum_values}")

    if expected_type == "object":
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required key '{key}'")

        properties = schema.get("properties", {})
        for key, child_schema in properties.items():
            if key in value:
                errors.extend(_validate(value[key], child_schema, f"{path}.{key}"))

    if expected_type == "array":
        item_schema = schema.get("items")
        if item_schema:
            for idx, item in enumerate(value):
                errors.extend(_validate(item, item_schema, f"{path}[{idx}]"))

    return errors


def validate_data_against_schema(data: Any, schema: Dict[str, Any]) -> List[str]:
    return _validate(data, schema)


def validate_json_file(data_path: Path, schema_path: Path) -> Tuple[bool, List[str]]:
    data = json.loads(data_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = validate_data_against_schema(data, schema)
    return (len(errors) == 0, errors)


def validate_runtime_configs(root: Path) -> List[str]:
    cfg = root / "config"
    schemas = cfg / "schemas"

    checks = [
        (cfg / "context_sources.json", schemas / "context_sources.schema.json"),
        (cfg / "guardrails.json", schemas / "guardrails.schema.json"),
        (cfg / "notify.json", schemas / "notify.schema.json"),
        (cfg / "pricing_models.json", schemas / "pricing_models.schema.json"),
        (cfg / "lane_catalog.json", schemas / "lane_catalog.schema.json"),
        (cfg / "lane_lifecycle.json", schemas / "lane_lifecycle.schema.json"),
        (cfg / "observation_signatures.json", schemas / "observation_signatures.schema.json"),
        (cfg / "state_writer_targets.json", schemas / "state_writer_targets.schema.json"),
        (cfg / "migration_policy.json", schemas / "migration_policy.schema.json"),
    ]

    errors: List[str] = []
    for data_path, schema_path in checks:
        ok, errs = validate_json_file(data_path, schema_path)
        if not ok:
            for err in errs:
                errors.append(f"{data_path}: {err}")

    lane_schema = schemas / "lane_spec.schema.json"
    lanes_dir = cfg / "lanes"
    for lane_file in sorted(lanes_dir.glob("*.json")):
        ok, errs = validate_json_file(lane_file, lane_schema)
        if not ok:
            for err in errs:
                errors.append(f"{lane_file}: {err}")

    # Fail closed when config versions do not match declared migration policy.
    migration_policy_path = cfg / "migration_policy.json"
    try:
        migration_policy = json.loads(migration_policy_path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{migration_policy_path}: unable to load migration policy ({exc})")
        return errors

    for row in migration_policy.get("policies", []):
        rel_path = str(row.get("path", "")).strip()
        required_version = row.get("required_version", None)
        policy_id = str(row.get("id", "unknown")).strip()
        if not rel_path:
            errors.append(f"{migration_policy_path}: policy '{policy_id}' has empty path")
            continue

        target_path = root / rel_path
        if not target_path.exists():
            errors.append(
                f"{migration_policy_path}: policy '{policy_id}' target missing: {target_path}"
            )
            continue

        try:
            payload = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{target_path}: invalid JSON for version policy ({exc})")
            continue

        actual_version = payload.get("version", None)
        if required_version is None:
            errors.append(
                f"{migration_policy_path}: policy '{policy_id}' missing required_version"
            )
            continue

        if actual_version != required_version:
            errors.append(
                f"{target_path}: version {actual_version!r} does not match required_version {required_version}"
            )

    return errors
