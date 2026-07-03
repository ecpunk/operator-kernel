import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_latest_test_result(root: Path, suite: str) -> Dict[str, Any]:
    tests_dir = root / "artifacts" / "tests"
    rows = sorted(tests_dir.glob(f"{suite}_*.json"))
    if not rows:
        return {"status": "unknown", "path": "", "generated_at": ""}

    latest = rows[-1]
    payload = _load_json(latest, {})
    failed = int(payload.get("failed", 0) or 0)
    return {
        "status": "pass" if failed == 0 else "fail",
        "path": str(latest),
        "generated_at": str(payload.get("generated_at", "")),
        "total": int(payload.get("total", 0) or 0),
        "passed": int(payload.get("passed", 0) or 0),
        "failed": failed,
    }


def _compute_readiness(root: Path) -> Dict[str, Any]:
    scorecard = _load_json(root / "artifacts" / "scorecards" / "stage_scorecard_latest.json", {})
    service = _load_json(root / "artifacts" / "service_scorecards" / "service_scorecards_latest.json", {})
    soak = _load_json(root / "artifacts" / "soak" / "stage2_soak_snapshot_latest.json", {})
    learning = _load_json(root / "artifacts" / "learning" / "learning_suggestions_latest.json", {})

    batch2 = _load_latest_test_result(root, "broad_spectrum_batch2")
    bootstrap = _load_latest_test_result(root, "stage3_bootstrap")

    checks = {
        "stage_scorecard_gate": str(scorecard.get("stage_gate", {}).get("status", "")) == "pass",
        "service_scorecard_gate": str(service.get("stage_gate", {}).get("status", "")) == "pass",
        "stage2_exit_gate": str(soak.get("exit_gate", {}).get("status", "")) == "pass",
        "batch2_suite": str(batch2.get("status", "")) == "pass",
        "bootstrap_suite": str(bootstrap.get("status", "")) == "pass",
    }
    alerts = [name for name, ok in checks.items() if not ok]
    status = "ready" if len(alerts) == 0 else "not_ready"

    return {
        "generated_at": _utc_now_iso(),
        "status": status,
        "checks": checks,
        "alerts": alerts,
        "metrics": {
            "learning_suggestions_total": int(learning.get("metrics", {}).get("suggestions_total", 0) or 0),
            "batch2_failed": int(batch2.get("failed", 0) or 0),
            "bootstrap_failed": int(bootstrap.get("failed", 0) or 0),
            "soak_snapshot_count_within_window": int(
                soak.get("checks", {}).get("soak_snapshot_count_within_window", 0) or 0
            ),
        },
        "sources": {
            "stage_scorecard_latest": str(root / "artifacts" / "scorecards" / "stage_scorecard_latest.json"),
            "service_scorecards_latest": str(
                root / "artifacts" / "service_scorecards" / "service_scorecards_latest.json"
            ),
            "stage2_soak_latest": str(root / "artifacts" / "soak" / "stage2_soak_snapshot_latest.json"),
            "learning_suggestions_latest": str(
                root / "artifacts" / "learning" / "learning_suggestions_latest.json"
            ),
            "batch2_latest": str(batch2.get("path", "")),
            "bootstrap_latest": str(bootstrap.get("path", "")),
        },
    }


def _write_artifacts(root: Path, payload: Dict[str, Any]) -> Dict[str, str]:
    out_dir = root / "artifacts" / "stage3"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = out_dir / f"stage3_readiness_{stamp}.json"
    latest = out_dir / "stage3_readiness_latest.json"
    text = json.dumps(payload, indent=2) + "\n"
    snapshot.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return {"snapshot": str(snapshot), "latest": str(latest)}


def _suggested_actions(alerts: list[str]) -> list[str]:
    mapping = {
        "stage_scorecard_gate": "Run report_scorecard and inspect failing thresholds.",
        "service_scorecard_gate": "Run report_service_scorecards and inspect blocked actions.",
        "stage2_exit_gate": "Run run_stage2_soak_snapshot with --enforce-exit and inspect unmet requirements.",
        "batch2_suite": "Run broad_spectrum_tests_batch2 and inspect deterministic phrase regression failures.",
        "bootstrap_suite": "Run stage3_bootstrap_tests --strict and inspect lane execute/propose drift.",
    }
    out: list[str] = []
    for key in alerts:
        out.append(mapping.get(key, f"Investigate failing check: {key}"))
    return out


def _write_incident_ticket(root: Path, payload: Dict[str, Any]) -> Dict[str, str]:
    out_dir = root / "artifacts" / "stage3"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ticket_json = out_dir / f"stage3_incident_{stamp}.json"
    ticket_md = out_dir / f"stage3_incident_{stamp}.md"
    latest_json = out_dir / "stage3_incident_latest.json"
    latest_md = out_dir / "stage3_incident_latest.md"

    ticket = {
        "generated_at": _utc_now_iso(),
        "status": str(payload.get("status", "unknown")),
        "alerts": list(payload.get("alerts", [])),
        "checks": dict(payload.get("checks", {})),
        "metrics": dict(payload.get("metrics", {})),
        "suggested_actions": _suggested_actions(list(payload.get("alerts", []))),
        "sources": dict(payload.get("sources", {})),
    }

    text = json.dumps(ticket, indent=2) + "\n"
    ticket_json.write_text(text, encoding="utf-8")
    latest_json.write_text(text, encoding="utf-8")

    lines = [
        "# Stage 3 Incident Ticket",
        "",
        f"Generated: {ticket['generated_at']}",
        f"Status: {ticket['status']}",
        "",
        "## Alerts",
    ]
    if ticket["alerts"]:
        for row in ticket["alerts"]:
            lines.append(f"- {row}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Suggested Actions")
    if ticket["suggested_actions"]:
        for row in ticket["suggested_actions"]:
            lines.append(f"- {row}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Sources")
    for key, value in ticket["sources"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    md_text = "\n".join(lines)
    ticket_md.write_text(md_text, encoding="utf-8")
    latest_md.write_text(md_text, encoding="utf-8")

    return {
        "ticket_json": str(ticket_json),
        "ticket_md": str(ticket_md),
        "latest_json": str(latest_json),
        "latest_md": str(latest_md),
    }


def _export_dashboard(payload: Dict[str, Any]) -> Path:
    dashboard_path = Path("/opt/stack/services/apps/dashboard/status.json")
    dashboard = _load_json(dashboard_path, {})
    dashboard["operator_kernel_stage3"] = payload
    dashboard_path.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")
    return dashboard_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-dashboard", action="store_true", help="Export readiness to dashboard status")
    parser.add_argument("--enforce-ready", action="store_true", help="Return non-zero when status is not_ready")
    parser.add_argument(
        "--emit-ticket",
        action="store_true",
        help="Emit incident ticket artifacts when readiness is not_ready",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    payload = _compute_readiness(root)
    paths = _write_artifacts(root, payload)

    print("Operator Kernel Stage 3 Readiness")
    print(f"- status: {payload['status']}")
    print(f"- alerts_total: {len(payload['alerts'])}")
    if payload["alerts"]:
        print(f"- alerts: {', '.join(payload['alerts'])}")
    print(f"- snapshot: {paths['snapshot']}")
    print(f"- latest: {paths['latest']}")

    if args.export_dashboard:
        dashboard = _export_dashboard(payload)
        print(f"- dashboard_export: {dashboard}")

    if args.emit_ticket and payload["status"] != "ready":
        ticket_paths = _write_incident_ticket(root, payload)
        print(f"- incident_ticket_json: {ticket_paths['ticket_json']}")
        print(f"- incident_ticket_md: {ticket_paths['ticket_md']}")

    if args.enforce_ready and payload["status"] != "ready":
        raise SystemExit(2)


if __name__ == "__main__":
    main()