import hashlib
import json
from pathlib import Path
from typing import Dict, List, Any


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_source_config(config_path: Path) -> Dict[str, Any]:
    raw = config_path.read_text(encoding="utf-8")
    return json.loads(raw)


def build_preflight_context(config_path: Path) -> List[Dict[str, Any]]:
    config = load_source_config(config_path)
    result: List[Dict[str, Any]] = []

    for item in config.get("sources", []):
        src_path = Path(item["path"])
        required = bool(item.get("required", False))

        if not src_path.exists():
            result.append(
                {
                    "id": item["id"],
                    "path": str(src_path),
                    "required": required,
                    "status": "missing",
                    "sha256": None,
                    "preview": None,
                }
            )
            continue

        text = src_path.read_text(encoding="utf-8", errors="replace")
        preview = "\n".join(text.splitlines()[:20])

        result.append(
            {
                "id": item["id"],
                "path": str(src_path),
                "required": required,
                "status": "loaded",
                "sha256": _sha256_text(text),
                "preview": preview,
            }
        )

    return result


def preflight_ok(context_rows: List[Dict[str, Any]]) -> bool:
    for row in context_rows:
        if row["required"] and row["status"] != "loaded":
            return False
    return True
