import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .schema_validator import validate_data_against_schema


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_adapter_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    utterance = str(payload.get("utterance", "")).strip()
    adapter = str(payload.get("adapter", "")).strip().lower()
    channel = str(payload.get("channel", "")).strip().lower()

    normalized = {
        "adapter": adapter,
        "channel": channel,
        "task": utterance,
        "task_normalized": " ".join(utterance.split()).strip(),
        "received_at": str(payload.get("timestamp", "")).strip() or _utc_now_iso(),
        "metadata": dict(payload.get("metadata", {})),
    }

    if str(payload.get("locale", "")).strip():
        normalized["locale"] = str(payload.get("locale", "")).strip()
    if str(payload.get("device_id", "")).strip():
        normalized["device_id"] = str(payload.get("device_id", "")).strip()
    return normalized


def _validate_payload(root: Path, payload: Dict[str, Any]) -> list[str]:
    schema = _load_json(root / "config" / "schemas" / "adapter_payload.schema.json", {})
    return validate_data_against_schema(payload, schema)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True, help="JSON payload from adapter")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    payload = json.loads(args.payload)
    errors = _validate_payload(root, payload)
    if errors:
        print("Adapter Contract Validation")
        for err in errors:
            print(f"- {err}")
        raise SystemExit(2)

    normalized = normalize_adapter_payload(payload)
    print("Adapter Contract Normalized")
    print(json.dumps(normalized, indent=2))


if __name__ == "__main__":
    main()
