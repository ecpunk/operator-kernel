import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _registry_path(root: Path) -> Path:
    return root / "artifacts" / "stage3" / "stage3_incident_registry.json"


def _new_id() -> str:
    return "inc_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_registry(root: Path) -> Dict[str, Any]:
    return _load_json(_registry_path(root), {"version": 1, "incidents": []})


def _save_registry(root: Path, payload: Dict[str, Any]) -> None:
    _save_json(_registry_path(root), payload)


def _find_incident(rows: List[Dict[str, Any]], incident_id: str) -> Dict[str, Any] | None:
    for row in rows:
        if str(row.get("incident_id", "")).strip() == incident_id:
            return row
    return None


def _open_from_latest(root: Path) -> int:
    readiness = _load_json(root / "artifacts" / "stage3" / "stage3_readiness_latest.json", {})
    status = str(readiness.get("status", "")).strip()
    if status != "not_ready":
        print("- no incident opened: readiness is not in not_ready state")
        return 0

    registry = _load_registry(root)
    incidents = list(registry.get("incidents", []))
    active = [i for i in incidents if str(i.get("state", "")).strip() in {"open", "ack"}]
    if active:
        latest = active[-1]
        print(f"- incident already active: {latest.get('incident_id','')}")
        return 0

    row = {
        "incident_id": _new_id(),
        "state": "open",
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "status_snapshot": status,
        "alerts": list(readiness.get("alerts", [])),
        "checks": dict(readiness.get("checks", {})),
        "notes": [],
    }
    incidents.append(row)
    registry["incidents"] = incidents
    _save_registry(root, registry)
    print(f"- opened: {row['incident_id']}")
    return 0


def _update_state(root: Path, incident_id: str, new_state: str, note: str) -> int:
    registry = _load_registry(root)
    incidents = list(registry.get("incidents", []))
    target = _find_incident(incidents, incident_id)
    if not target:
        print(f"- incident not found: {incident_id}")
        return 2

    target["state"] = new_state
    target["updated_at"] = _utc_now_iso()
    notes = list(target.get("notes", []))
    if note.strip():
        notes.append({"timestamp": _utc_now_iso(), "note": note.strip(), "state": new_state})
    target["notes"] = notes
    _save_registry(root, registry)
    print(f"- updated: {incident_id} -> {new_state}")
    return 0


def _list_incidents(root: Path, include_resolved: bool) -> int:
    registry = _load_registry(root)
    incidents = list(registry.get("incidents", []))
    rows = incidents if include_resolved else [i for i in incidents if str(i.get("state", "")) != "resolved"]
    print("Stage 3 Incident Registry")
    print(f"- total: {len(rows)}")
    for row in rows[-20:]:
        print(
            f"- {row.get('incident_id','')} | state={row.get('state','')} "
            f"| updated_at={row.get('updated_at','')}"
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["list", "open_from_latest", "ack", "resolve"], required=True)
    parser.add_argument("--incident-id", default="", help="Incident id for ack/resolve")
    parser.add_argument("--note", default="", help="Optional note to append")
    parser.add_argument("--include-resolved", action="store_true", help="Include resolved incidents in list")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    if args.action == "list":
        raise SystemExit(_list_incidents(root, args.include_resolved))
    if args.action == "open_from_latest":
        raise SystemExit(_open_from_latest(root))
    if args.action == "ack":
        if not args.incident_id.strip():
            raise SystemExit("--incident-id is required for ack")
        raise SystemExit(_update_state(root, args.incident_id.strip(), "ack", args.note))
    if args.action == "resolve":
        if not args.incident_id.strip():
            raise SystemExit("--incident-id is required for resolve")
        raise SystemExit(_update_state(root, args.incident_id.strip(), "resolved", args.note))


if __name__ == "__main__":
    main()