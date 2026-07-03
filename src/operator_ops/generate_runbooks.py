import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_lane_specs(lanes_dir: Path) -> Dict[str, Dict[str, Any]]:
    specs: Dict[str, Dict[str, Any]] = {}
    for lane_path in sorted(lanes_dir.glob("*.json")):
        payload = _load_json(lane_path, {})
        action_id = str(payload.get("action_id", "")).strip()
        if not action_id:
            continue
        payload["_path"] = str(lane_path)
        specs[action_id] = payload
    return specs


def _load_runs(root: Path) -> List[Dict[str, Any]]:
    payload = _load_json(root / "artifacts" / "index" / "run_index.json", {"runs": []})
    runs = list(payload.get("runs", []))
    for run in runs:
        run.setdefault("run_type", "prod")
    return runs


def _proofs_for_action(root: Path, runs: List[Dict[str, Any]], action_id: str) -> List[Dict[str, Any]]:
    proofs: List[Dict[str, Any]] = []
    for run in runs:
        if str(run.get("mode", "")) != "execute":
            continue
        if str(run.get("action_id", "")) != action_id:
            continue
        proof_path_raw = str(run.get("proof_path", "")).strip()
        if not proof_path_raw:
            continue

        proof_path = Path(proof_path_raw)
        if not proof_path.is_absolute():
            proof_path = root / proof_path
        if not proof_path.exists():
            continue

        payload = _load_json(proof_path, {})
        payload["_path"] = str(proof_path)
        payload["_run_type"] = str(run.get("run_type", "prod"))
        proofs.append(payload)
    return proofs


def _summarize_patterns(proofs: List[Dict[str, Any]]) -> Dict[str, Any]:
    observations = Counter()
    recommendations = Counter()
    commands = Counter()

    for proof in proofs:
        for obs in proof.get("observations", []) or []:
            line = str(obs).strip()
            if line:
                observations[line] += 1
        for rec in proof.get("recommendations", []) or []:
            text = str(rec.get("recommendation", "")).strip()
            if text:
                recommendations[text] += 1
        for row in proof.get("command_transcript", []) or []:
            cmd = str(row.get("command", "")).strip()
            if cmd:
                commands[cmd] += 1

    return {
        "top_observations": observations.most_common(5),
        "top_recommendations": recommendations.most_common(5),
        "top_commands": commands.most_common(8),
    }


def _compute_metrics(runs: List[Dict[str, Any]], action_id: str) -> Dict[str, Any]:
    executes = [
        r
        for r in runs
        if str(r.get("mode", "")) == "execute" and str(r.get("action_id", "")) == action_id
    ]
    executes_prod = [r for r in executes if str(r.get("run_type", "prod")).lower() != "test"]

    complete_total = sum(
        1 for r in executes if str(r.get("verification_status", "")).strip() == "completed"
    )
    failed_total = sum(
        1 for r in executes if str(r.get("verification_status", "")).strip() == "failed"
    )
    complete_prod = sum(
        1 for r in executes_prod if str(r.get("verification_status", "")).strip() == "completed"
    )
    failed_prod = sum(
        1 for r in executes_prod if str(r.get("verification_status", "")).strip() == "failed"
    )

    def pct(n: int, d: int) -> float:
        if d <= 0:
            return 0.0
        return round((n / d) * 100.0, 2)

    return {
        "execute_total": len(executes),
        "execute_prod_total": len(executes_prod),
        "verification_completed_total": complete_total,
        "verification_failed_total": failed_total,
        "verification_success_pct": pct(complete_total, len(executes)),
        "verification_completed_prod_total": complete_prod,
        "verification_failed_prod_total": failed_prod,
        "verification_success_prod_pct": pct(complete_prod, len(executes_prod)),
    }


def _render_runbook(action_id: str, spec: Dict[str, Any], metrics: Dict[str, Any], patterns: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"# Runbook: {action_id}")
    lines.append("")
    lines.append(f"Generated: {_utc_now_iso()}")
    lines.append(f"Source lane spec: {spec.get('_path', '')}")
    lines.append("")
    lines.append("## Scope")
    lines.append(f"Lane: {spec.get('lane', '')}")
    lines.append(f"Service: {spec.get('service', '')}")
    lines.append(f"Endpoint: {spec.get('endpoint', '')}")
    lines.append("")
    lines.append("## Verification Baseline")
    lines.append(f"All execute runs: {metrics['execute_total']}")
    lines.append(f"All execute verification success: {metrics['verification_success_pct']}%")
    lines.append(f"Operational execute runs (non-test): {metrics['execute_prod_total']}")
    lines.append(
        f"Operational execute verification success: {metrics['verification_success_prod_pct']}%"
    )
    lines.append("")
    lines.append("## Standard Verify Checks")
    for check in spec.get("verify", []):
        lines.append(f"- {check}")
    lines.append("")
    lines.append("## Standard Rollback")
    for step in spec.get("rollback", []):
        lines.append(f"- {step}")
    lines.append("")
    lines.append("## Common Command Pattern")
    for cmd, count in patterns.get("top_commands", []):
        lines.append(f"- ({count}x) `{cmd}`")
    if not patterns.get("top_commands"):
        lines.append("- No command transcript data yet")
    lines.append("")
    lines.append("## Frequent Observations")
    for obs, count in patterns.get("top_observations", []):
        lines.append(f"- ({count}x) {obs}")
    if not patterns.get("top_observations"):
        lines.append("- No recurring observation lines captured yet")
    lines.append("")
    lines.append("## Frequent Recommendations")
    for rec, count in patterns.get("top_recommendations", []):
        lines.append(f"- ({count}x) {rec}")
    if not patterns.get("top_recommendations"):
        lines.append("- No recurring recommendation lines captured yet")
    lines.append("")
    return "\n".join(lines) + "\n"


def _render_index(entries: List[Tuple[str, Dict[str, Any], Path]]) -> str:
    lines: List[str] = []
    lines.append("# Operator Kernel Runbooks")
    lines.append("")
    lines.append(f"Generated: {_utc_now_iso()}")
    lines.append("")
    lines.append("This index is generated from lane specs plus proof/run history.")
    lines.append("")
    for action_id, metrics, path in entries:
        lines.append(f"- {action_id}: {path.name}")
        lines.append(
            f"  - execute_prod_total={metrics['execute_prod_total']}, verification_success_prod_pct={metrics['verification_success_prod_pct']}"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-dir",
        default="artifacts/runbooks",
        help="Output directory for generated runbooks",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    out_dir = root / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    lanes_dir = root / "config" / "lanes"
    lane_specs = _load_lane_specs(lanes_dir)
    runs = _load_runs(root)

    index_rows: List[Tuple[str, Dict[str, Any], Path]] = []
    for action_id, spec in sorted(lane_specs.items()):
        action_runs = [r for r in runs if str(r.get("action_id", "")) == action_id]
        proofs = _proofs_for_action(root, action_runs, action_id)
        metrics = _compute_metrics(runs, action_id)
        patterns = _summarize_patterns(proofs)

        out_path = out_dir / f"runbook_{action_id}.md"
        out_path.write_text(
            _render_runbook(action_id, spec, metrics, patterns),
            encoding="utf-8",
        )
        index_rows.append((action_id, metrics, out_path))
        print(f"- wrote: {out_path}")

    index_path = out_dir / "RUNBOOKS.md"
    index_path.write_text(_render_index(index_rows), encoding="utf-8")
    print(f"- wrote: {index_path}")


if __name__ == "__main__":
    main()
