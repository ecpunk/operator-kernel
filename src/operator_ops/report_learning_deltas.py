import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from operator_kernel.lane_catalog import resolve_action_id


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_lane_spec(root: Path, action_id: str) -> Dict[str, Any]:
    path = root / "config" / "lanes" / f"{action_id}.json"
    return _load_json(path, {})


def _task_to_delta_rows(root: Path, suggestions: List[Dict[str, Any]], max_tasks: int) -> List[Dict[str, Any]]:
    catalog_path = root / "config" / "lane_catalog.json"
    rows: List[Dict[str, Any]] = []
    for row in suggestions:
        if str(row.get("type", "")).strip() != "doc_update":
            continue
        task = str(row.get("id", "")).replace("task_recurrence::", "", 1).strip()
        if not task:
            continue
        action_id = resolve_action_id(task, catalog_path)
        rows.append(
            {
                "task": task,
                "evidence_count": int(row.get("evidence_count", 0)),
                "action_id": action_id,
            }
        )
        if len(rows) >= max_tasks:
            break
    return rows


def _build_concept_delta(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    lines: List[str] = []
    lines.append("## Suggested Recurring Incident Patterns (Review Draft)")
    lines.append("")
    lines.append("These patterns were extracted from recent operator runs and are proposed for documentation review only.")
    lines.append("")
    for row in rows:
        task = row["task"]
        count = int(row["evidence_count"])
        action_id = str(row.get("action_id", "")).strip()
        if action_id:
            lines.append(f"- Trigger pattern: `{task}` ({count} recent occurrences)")
            lines.append(f"  - Suggested lane mapping check: `{action_id}`")
            lines.append(f"  - Suggested runbook reference: `artifacts/runbooks/runbook_{action_id}.md`")
        else:
            lines.append(f"- Trigger pattern: `{task}` ({count} recent occurrences)")
            lines.append("  - Suggested lane mapping check: unresolved (review catalog keywords)")
    lines.append("")
    return {
        "target_path": "10-CONCEPT/OPERATOR_KERNEL_CONCEPT.md",
        "change_type": "append_section",
        "anchor_hint": "## 16. Deferred Learning Loop (Post-V1)",
        "proposed_content": "\n".join(lines),
    }


def _build_runbook_deltas(root: Path, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deltas: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        action_id = str(row.get("action_id", "")).strip()
        if not action_id or action_id in seen:
            continue
        seen.add(action_id)

        lane = _load_lane_spec(root, action_id)
        service = str(lane.get("service", "")).strip() or "unknown"
        endpoint = str(lane.get("endpoint", "")).strip() or "unknown"
        probes = list(lane.get("verify_probes", []))

        trigger_lines = [
            f"## Suggested Trigger Coverage (Review Draft)",
            "",
            "Proposed recurring triggers from recent runs:",
        ]
        for task_row in rows:
            if str(task_row.get("action_id", "")).strip() == action_id:
                trigger_lines.append(
                    f"- `{task_row['task']}` ({int(task_row['evidence_count'])} occurrences)"
                )

        trigger_lines.append("")
        trigger_lines.append("Suggested verification anchors:")
        trigger_lines.append(f"- Service: `{service}`")
        trigger_lines.append(f"- Endpoint: `{endpoint}`")
        if probes:
            for probe in probes:
                pname = str(probe.get("name", "")).strip() or "unnamed_probe"
                expected = str(probe.get("expected_substring", "")).strip()
                if expected:
                    trigger_lines.append(f"- Required probe `{pname}` expects `{expected}`")
                else:
                    trigger_lines.append(f"- Required probe `{pname}` (no substring expectation)")
        else:
            trigger_lines.append("- No verify_probes defined in lane spec")
        trigger_lines.append("")

        deltas.append(
            {
                "target_path": f"artifacts/runbooks/runbook_{action_id}.md",
                "change_type": "insert_section",
                "anchor_hint": "## Verification Baseline",
                "proposed_content": "\n".join(trigger_lines),
            }
        )

    return deltas


def _write_artifacts(root: Path, payload: Dict[str, Any]) -> Dict[str, str]:
    out_dir = root / "artifacts" / "learning"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = out_dir / f"learning_deltas_{stamp}.json"
    latest = out_dir / "learning_deltas_latest.json"
    summary = out_dir / f"learning_deltas_review_{stamp}.md"

    text = json.dumps(payload, indent=2) + "\n"
    snapshot.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")

    lines: List[str] = []
    lines.append("# Learning Delta Review Draft")
    lines.append("")
    lines.append(f"Generated: {payload.get('generated_at', '')}")
    lines.append(f"Source: {payload.get('source_learning_artifact', '')}")
    lines.append("")
    for idx, target in enumerate(payload.get("targets", []), start=1):
        lines.append(f"## {idx}. {target.get('target_path', '')}")
        lines.append("")
        lines.append(f"Change type: {target.get('change_type', '')}")
        lines.append(f"Anchor hint: {target.get('anchor_hint', '')}")
        lines.append("")
        lines.append(target.get("proposed_content", ""))
        lines.append("")
    summary.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    return {
        "snapshot": str(snapshot),
        "latest": str(latest),
        "review_summary": str(summary),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--learning-artifact",
        default="artifacts/learning/learning_suggestions_latest.json",
        help="Path to learning suggestions artifact",
    )
    parser.add_argument("--max-tasks", type=int, default=5, help="Max recurring tasks to include")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    learning_path = root / args.learning_artifact
    learning = _load_json(learning_path, {})
    suggestions = list(learning.get("suggestions", []))

    rows = _task_to_delta_rows(root, suggestions, max(1, args.max_tasks))
    targets = [_build_concept_delta(rows)]
    targets.extend(_build_runbook_deltas(root, rows))

    payload = {
        "generated_at": _utc_now_iso(),
        "review_only": True,
        "source_learning_artifact": str(learning_path),
        "selected_task_rows": rows,
        "targets": targets,
        "metrics": {
            "selected_tasks_total": len(rows),
            "targets_total": len(targets),
        },
    }

    paths = _write_artifacts(root, payload)

    print("Operator Kernel Learning Delta Draft")
    print(f"- review_only: {payload['review_only']}")
    print(f"- selected_tasks_total: {payload['metrics']['selected_tasks_total']}")
    print(f"- targets_total: {payload['metrics']['targets_total']}")
    print(f"- snapshot: {paths['snapshot']}")
    print(f"- latest: {paths['latest']}")
    print(f"- review_summary: {paths['review_summary']}")


if __name__ == "__main__":
    main()