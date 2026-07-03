"""
Stage 5 unattended soak operations orchestrator.

Runs in sequence:
  1. Evaluate stage5 lane draft strategies (batch scoring sweep)
  2. Report stage5 draft production metrics
  3. (Optional) Stage 3 operations health check

Writes a summary artifact to artifacts/stage5/stage5_soak_ops_{stamp}.json
and stage5_soak_ops_latest.json.

Intended for unattended / scheduled execution; all steps are read-only
except the metrics artifacts they write.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from operator_notify import notify_event


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _run(root: Path, args: List[str]) -> Dict[str, Any]:
    env = dict(os.environ)
    src_path = str(root / "src")
    existing = str(env.get("PYTHONPATH", "")).strip()
    env["PYTHONPATH"] = f"{src_path}:{existing}" if existing else src_path
    proc = subprocess.run(
        args,
        cwd=str(root),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    return {
        "command": " ".join(args),
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def _write_artifacts(root: Path, payload: Dict[str, Any]) -> Dict[str, str]:
    out_dir = root / "artifacts" / "stage5"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _stamp()
    snapshot = out_dir / f"stage5_soak_ops_{ts}.json"
    latest = out_dir / "stage5_soak_ops_latest.json"
    text = json.dumps(payload, indent=2) + "\n"
    snapshot.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return {"snapshot": str(snapshot), "latest": str(latest)}


def _notify(
    root: Path,
    event: str,
    status: str,
    message: str,
    severity: str = "info",
    milestone: str = "",
) -> None:
    try:
        notify_event(
            root=root,
            event=event,
            status=status,
            message=message,
            severity=severity,
            source="run_stage5_soak_ops",
            milestone=milestone,
        )
    except Exception:
        pass


def _extract_eval_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = json.loads(result.get("stdout", "") or "{}")
        summary = data.get("summary", {})
        return {
            "top_strategy": summary.get("top_strategy"),
            "task_count": summary.get("task_count"),
            "evidence_driven_avg_score": summary.get("strategy_summary", {})
            .get("evidence_driven", {})
            .get("avg_score"),
            "evidence_driven_win_rate": summary.get("strategy_summary", {})
            .get("evidence_driven", {})
            .get("win_rate"),
            "evidence_driven_schema_valid_rate": summary.get("strategy_summary", {})
            .get("evidence_driven", {})
            .get("schema_valid_rate"),
        }
    except (json.JSONDecodeError, AttributeError, TypeError):
        return {}


def _extract_metrics_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = json.loads(result.get("stdout", "") or "{}")
        gate = data.get("gate_progress", {})
        primary_stats = data.get("strategy_metrics", {}).get("evidence_driven", {})
        return {
            "unique_runs": gate.get("unique_runs_collected"),
            "sample_target_met": gate.get("sample_target_met"),
            "schema_valid_rate": primary_stats.get("schema_valid_rate"),
            "service_specific_rate": primary_stats.get("service_specific_rate"),
        }
    except (json.JSONDecodeError, AttributeError, TypeError):
        return {}


def _run_step(
    root: Path,
    label: str,
    cmd: List[str],
    results: List[Dict[str, Any]],
) -> bool:
    result = _run(root, cmd)
    ok = result["exit_code"] == 0
    results.append({
        "label": label,
        "exit_code": result["exit_code"],
        "ok": ok,
        "stdout": result["stdout"][:4000] if result["stdout"] else "",
        "stderr": result["stderr"][:1000] if result["stderr"] else "",
    })
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Stage 5 unattended soak operations: eval + metrics.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Lookback window for metrics report (default: 7).",
    )
    parser.add_argument(
        "--sample-min",
        type=int,
        default=30,
        help="Sample minimum for draft metrics gate check.",
    )
    parser.add_argument(
        "--tasks-file",
        default="",
        help="Optional path to JSON task list file for evaluator sweep.",
    )
    parser.add_argument(
        "--skip-stage3",
        action="store_true",
        help="Skip the Stage 3 operations health check step.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent.parent
    results: List[Dict[str, Any]] = []
    started_at = _utc_now_iso()

    _notify(root, "stage5_soak_ops", "started", "Stage 5 soak ops started.", "info", "soak_start")

    # --- Step 1: Evaluator sweep ---
    eval_cmd = [
        sys.executable,
        "src/operator_ops/evaluate_stage5_lane_drafts.py",
    ]
    if args.tasks_file:
        eval_cmd += ["--tasks-file", args.tasks_file]
    eval_ok = _run_step(root, "stage5_evaluator", eval_cmd, results)

    # --- Step 2: Draft metrics ---
    metrics_cmd = [
        sys.executable,
        "src/operator_ops/report_stage5_draft_metrics.py",
        "--days", str(args.days),
        "--sample-min", str(args.sample_min),
    ]
    metrics_ok = _run_step(root, "stage5_draft_metrics", metrics_cmd, results)

    # --- Step 3: Stage 3 health check (optional) ---
    stage3_ok = True
    if not args.skip_stage3:
        stage3_cmd = [
            sys.executable,
            "src/operator_ops/run_stage3_operations.py",
            "--days", str(args.days),
            "--delta-profile", "none",
        ]
        stage3_ok = _run_step(root, "stage3_health_check", stage3_cmd, results)

    all_ok = eval_ok and metrics_ok and stage3_ok
    status = "pass" if all_ok else "fail"

    failed = [r["label"] for r in results if not r["ok"]]
    eval_result = next((r for r in results if r["label"] == "stage5_evaluator"), {})
    metrics_result = next((r for r in results if r["label"] == "stage5_draft_metrics"), {})

    payload: Dict[str, Any] = {
        "generated_at": _utc_now_iso(),
        "started_at": started_at,
        "status": status,
        "steps_total": len(results),
        "steps_ok": sum(1 for r in results if r["ok"]),
        "failed_steps": failed,
        "evaluator_summary": _extract_eval_summary(eval_result),
        "metrics_summary": _extract_metrics_summary(metrics_result),
        "commands": results,
    }

    artifacts = _write_artifacts(root, payload)
    payload["_artifacts"] = artifacts

    if all_ok:
        _notify(
            root,
            "stage5_soak_ops",
            "success",
            f"Stage 5 soak ops complete: eval + metrics + stage3 health check all passed.",
            "info",
            "soak_complete",
        )
    else:
        _notify(
            root,
            "stage5_soak_ops",
            "failure",
            f"Stage 5 soak ops failed steps: {', '.join(failed)}",
            "error",
            "soak_failure",
        )

    print(json.dumps(payload, indent=2))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
