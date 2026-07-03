import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat()


def _parse_iso(ts: str) -> datetime:
    value = datetime.fromisoformat(ts)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _is_recent(ts: str, days: int) -> bool:
    if days <= 0:
        return True
    return _parse_iso(ts) >= (_utc_now() - timedelta(days=days))


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _pct(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return round((num / den) * 100.0, 2)


def _load_guardrails_service_scorecard_config(root: Path) -> Dict[str, Any]:
    payload = _load_json(root / "config" / "guardrails.json", {})
    cfg = dict(payload.get("service_scorecards", {}))
    thresholds = dict(cfg.get("thresholds", {}))
    cfg["thresholds"] = {
        "green_min_verification_success_prod_pct": float(
            thresholds.get("green_min_verification_success_prod_pct", 90.0)
        ),
        "yellow_min_verification_success_prod_pct": float(
            thresholds.get("yellow_min_verification_success_prod_pct", 70.0)
        ),
        "min_execute_prod_total": int(thresholds.get("min_execute_prod_total", 1)),
    }
    cfg["stage_gate_fail_closed"] = bool(cfg.get("stage_gate_fail_closed", True))
    cfg["default_window_days"] = int(cfg.get("default_window_days", 7))
    cfg["ignore_action_ids"] = list(cfg.get("ignore_action_ids", ["none"]))
    onboarding = dict(cfg.get("onboarding", {}))
    cfg["onboarding"] = {
        "candidate_gate_exempt": bool(onboarding.get("candidate_gate_exempt", True)),
    }
    cfg["fail_closed_on"] = list(cfg.get("fail_closed_on", ["yellow", "red"]))
    return cfg


def _load_lane_lifecycle(root: Path) -> Dict[str, str]:
    payload = _load_json(root / "config" / "lane_lifecycle.json", {"actions": {}})
    out: Dict[str, str] = {}
    actions = dict(payload.get("actions", {}))
    for action_id, row in actions.items():
        state = str(dict(row).get("state", "active")).strip().lower()
        if state not in {"candidate", "active"}:
            state = "active"
        out[str(action_id).strip()] = state
    return out


def _promote_candidates(root: Path, cards: List[Dict[str, Any]]) -> List[str]:
    """Promote candidate lanes to active once they have >= 1 prod completed execution.
    Writes back to lane_lifecycle.json. Returns list of promoted action_ids.
    """
    lc_path = root / "config" / "lane_lifecycle.json"
    payload = _load_json(lc_path, {"actions": {}})
    actions = dict(payload.get("actions", {}))
    promoted: List[str] = []
    for card in cards:
        action = str(card.get("action_id", "")).strip()
        if not action or action == "none":
            continue
        if str(card.get("lifecycle_state", "")).strip().lower() != "candidate":
            continue
        if int(card.get("verification_completed_prod_total", 0)) >= 1:
            if action in actions:
                actions[action]["state"] = "active"
            else:
                actions[action] = {"state": "active"}
            promoted.append(action)
    if promoted:
        payload["actions"] = actions
        lc_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return promoted


def _compute_signal(success_prod: float, execute_prod_total: int, cfg: Dict[str, Any]) -> str:
    t = cfg["thresholds"]
    if execute_prod_total < int(t["min_execute_prod_total"]):
        return "yellow"
    if success_prod < float(t["yellow_min_verification_success_prod_pct"]):
        return "red"
    if success_prod < float(t["green_min_verification_success_prod_pct"]):
        return "yellow"
    return "green"


def _compute_stage_gate(payload: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    fail_closed_on = {str(v).strip().lower() for v in cfg.get("fail_closed_on", [])}
    ignored_actions = {str(v).strip() for v in cfg.get("ignore_action_ids", [])}
    fail_closed_enabled = bool(cfg.get("stage_gate_fail_closed", True))
    candidate_exempt = bool(cfg.get("onboarding", {}).get("candidate_gate_exempt", True))
    blocked_by: List[str] = []
    for row in payload.get("services", []):
        sig = str(row.get("stage_signal", "")).strip().lower()
        action = str(row.get("action_id", "")).strip() or "none"
        lifecycle_state = str(row.get("lifecycle_state", "active")).strip().lower()
        if action in ignored_actions:
            continue
        if candidate_exempt and lifecycle_state == "candidate":
            continue
        if sig in fail_closed_on:
            blocked_by.append(f"{action}:{sig}")
    blocked = fail_closed_enabled and len(blocked_by) > 0
    return {
        "enabled": fail_closed_enabled,
        "status": "block" if blocked else "pass",
        "block_on": sorted(fail_closed_on),
        "blocked_by": blocked_by,
    }


def _compute_service_scorecards(root: Path, days: int) -> Dict[str, Any]:
    cfg = _load_guardrails_service_scorecard_config(root)
    lifecycle = _load_lane_lifecycle(root)
    payload = _load_json(root / "artifacts" / "index" / "run_index.json", {"runs": []})
    runs = [r for r in payload.get("runs", []) if str(r.get("timestamp", "")).strip()]
    rows: List[Dict[str, Any]] = []
    for run in runs:
        try:
            if _is_recent(str(run.get("timestamp", "")), days):
                row = dict(run)
                row.setdefault("run_type", "prod")
                rows.append(row)
        except ValueError:
            continue

    by_action: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        action = str(row.get("action_id", "")).strip() or "none"
        by_action.setdefault(action, []).append(row)

    cards: List[Dict[str, Any]] = []
    for action, action_rows in sorted(by_action.items()):
        executes = [r for r in action_rows if str(r.get("mode", "")) == "execute"]
        executes_prod = [
            r for r in executes if str(r.get("run_type", "prod")).strip().lower() != "test"
        ]
        completes = sum(
            1 for r in executes if str(r.get("verification_status", "")) == "completed"
        )
        fails = sum(
            1 for r in executes if str(r.get("verification_status", "")) == "failed"
        )
        completes_prod = sum(
            1 for r in executes_prod if str(r.get("verification_status", "")) == "completed"
        )
        fails_prod = sum(
            1 for r in executes_prod if str(r.get("verification_status", "")) == "failed"
        )

        blocked_approval = sum(
            1 for r in action_rows if str(r.get("status", "")).strip() == "blocked_approval"
        )
        blocked_risk = sum(
            1 for r in action_rows if str(r.get("status", "")).strip() == "blocked_risk_policy"
        )

        success_prod = _pct(completes_prod, len(executes_prod))
        signal = _compute_signal(success_prod, len(executes_prod), cfg)
        lifecycle_state = lifecycle.get(action, "active")
        gate_exempt = bool(
            lifecycle_state == "candidate"
            and cfg.get("onboarding", {}).get("candidate_gate_exempt", True)
        )

        cards.append(
            {
                "action_id": action,
                "runs_total": len(action_rows),
                "execute_total": len(executes),
                "execute_prod_total": len(executes_prod),
                "verification_completed_total": completes,
                "verification_failed_total": fails,
                "verification_success_pct": _pct(completes, len(executes)),
                "verification_completed_prod_total": completes_prod,
                "verification_failed_prod_total": fails_prod,
                "verification_success_prod_pct": success_prod,
                "blocked_approval_total": blocked_approval,
                "blocked_risk_policy_total": blocked_risk,
                "stage_signal": signal,
                "lifecycle_state": lifecycle_state,
                "gate_exempt": gate_exempt,
            }
        )

    promoted = _promote_candidates(root, cards)
    if promoted:
        # Reload lifecycle states after promotion so cards reflect updated states.
        lifecycle = _load_lane_lifecycle(root)
        for card in cards:
            action = str(card.get("action_id", "")).strip()
            if action in promoted:
                card["lifecycle_state"] = lifecycle.get(action, "active")
                card["gate_exempt"] = False

    out = {
        "generated_at": _utc_now_iso(),
        "window_days": days,
        "services": cards,
        "promoted_to_active": promoted,
        "stage_gate": {},
        "policy": {
            "service_scorecards": cfg,
        },
        "sources": {
            "run_index": str(root / "artifacts" / "index" / "run_index.json"),
        },
    }
    out["stage_gate"] = _compute_stage_gate(out, cfg)
    return out


def _write_artifacts(root: Path, payload: Dict[str, Any]) -> Dict[str, str]:
    out_dir = root / "artifacts" / "service_scorecards"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = out_dir / f"service_scorecards_{stamp}.json"
    latest = out_dir / "service_scorecards_latest.json"
    text = json.dumps(payload, indent=2) + "\n"
    snapshot.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return {"snapshot": str(snapshot), "latest": str(latest)}


def _export_to_dashboard(payload: Dict[str, Any]) -> Path:
    dashboard_path = Path("/opt/stack/services/apps/dashboard/status.json")
    data = _load_json(dashboard_path, {})
    data["operator_kernel_service_scorecards"] = {
        "updated_at": payload.get("generated_at", ""),
        "window_days": payload.get("window_days", 0),
        "services": payload.get("services", []),
        "stage_gate": payload.get("stage_gate", {}),
    }
    dashboard_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return dashboard_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None, help="Rolling window in days")
    parser.add_argument(
        "--export-dashboard",
        action="store_true",
        help="Write service scorecard summary into apps/dashboard/status.json",
    )
    parser.add_argument(
        "--enforce-gate",
        action="store_true",
        help="Return non-zero when fail-closed service stage gate blocks progression.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    cfg = _load_guardrails_service_scorecard_config(root)
    window_days = int(args.days) if args.days is not None else int(cfg.get("default_window_days", 7))
    payload = _compute_service_scorecards(root, window_days)
    paths = _write_artifacts(root, payload)

    print("Operator Kernel Service Scorecards")
    print(f"- window_days: {window_days}")
    print(f"- services_total: {len(payload['services'])}")
    for row in payload["services"]:
        print(
            "- "
            f"{row['action_id']} | signal={row['stage_signal']} | "
            f"execute_prod_total={row['execute_prod_total']} | "
            f"verification_success_prod_pct={row['verification_success_prod_pct']}"
        )
    print(f"- snapshot: {paths['snapshot']}")
    print(f"- latest: {paths['latest']}")
    print(f"- stage_gate_status: {payload.get('stage_gate', {}).get('status', 'unknown')}")
    if payload.get("promoted_to_active"):
        print(f"- promoted_to_active: {' '.join(payload['promoted_to_active'])}")

    if args.export_dashboard:
        dashboard = _export_to_dashboard(payload)
        print(f"- dashboard_export: {dashboard}")

    if args.enforce_gate and payload.get("stage_gate", {}).get("status") == "block":
        print("- gate_enforcement: blocked by service scorecard policy")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
