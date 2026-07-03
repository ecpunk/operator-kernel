import argparse
import json
import os
from pathlib import Path
from typing import List, Dict, Any

from operator_runtime.context_loader import build_preflight_context, preflight_ok
from operator_runtime.artifact_gc import run_artifact_gc
from operator_kernel.contract_engine import (
    evaluate_execute_risk_controls,
    load_guardrails,
    load_plan,
    plan_to_dict,
    save_plan,
    validate_mode_and_channel,
    verify_approval,
    write_proof_pack,
)
from operator_runtime.execution_lane import execute_media_lane
from operator_kernel.lane_catalog import resolve_action_id
from operator_runtime.inspect_engine import generate_inspect_report
from operator_runtime.plan_builder import build_media_incident_plan
from operator_runtime.run_index import append_run_entry
from operator_kernel.schema_validator import validate_runtime_configs
from operator_runtime.state_writer import write_canonical_state
from operator_notify import notify_event


def _severity_rank(severity: str) -> int:
    ranks = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    return ranks.get(severity.lower(), 0)


def prioritize_plan_from_recommendations(plan_record: Any, min_confidence: float) -> None:
    recs = list(plan_record.recommendations or [])
    if not recs:
        return

    for rec in recs:
        confidence = float(rec.get("confidence", 0.0))
        eligible = confidence >= min_confidence
        rec["reorder_eligible"] = eligible
        rec["reorder_reason"] = (
            f"confidence {confidence:.2f} met threshold {min_confidence:.2f}"
            if eligible
            else f"confidence {confidence:.2f} below threshold {min_confidence:.2f}"
        )

    recs.sort(
        key=lambda r: (
            float(r.get("confidence", 0.0)),
            _severity_rank(str(r.get("severity", ""))),
        ),
        reverse=True,
    )
    plan_record.recommendations = recs

    eligible_recs = [r for r in recs if bool(r.get("reorder_eligible", False))]
    if not eligible_recs:
        return

    top = eligible_recs[0]
    top["reorder_applied"] = True

    focus_step = str(top.get("focus_step", "")).strip()
    if not focus_step:
        return

    if focus_step in plan_record.steps:
        plan_record.steps = [focus_step] + [s for s in plan_record.steps if s != focus_step]
    else:
        plan_record.steps = [focus_step] + plan_record.steps


def print_context(rows: List[Dict[str, Any]]) -> None:
    print("Preflight Context Hydration")
    for row in rows:
        print(
            f"- {row['id']}: {row['status']} | required={row['required']} | path={row['path']}"
        )


def print_plan_summary(plan: Dict[str, Any]) -> None:
    print("\nPlan")
    print(f"- plan_id: {plan['plan_id']}")
    print(f"- lane: {plan['lane']}")
    print(f"- channel: {plan['channel']}")
    print(f"- risk: {plan['risk']}")
    print("- steps:")
    for step in plan.get("steps", []):
        print(f"  - {step}")
    print("- rollback:")
    for step in plan.get("rollback", []):
        print(f"  - {step}")
    print("- verify:")
    for step in plan.get("verify", []):
        print(f"  - {step}")


def _load_usage_hint(args: Any) -> Dict[str, Any]:
    # Usage hint can come from file/env and be overridden by explicit CLI flags.
    hint: Dict[str, Any] = {
        "provider": "",
        "model": "",
        "input_tokens": 0,
        "output_tokens": 0,
    }

    usage_json_path = str(getattr(args, "usage_json", "") or "").strip()
    if not usage_json_path:
        usage_json_path = str(os.environ.get("INSPECT_USAGE_JSON", "")).strip()

    if usage_json_path:
        path = Path(usage_json_path)
        if not path.exists():
            raise ValueError(f"usage json file not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("usage json must be an object")

        hint["provider"] = str(payload.get("provider", "") or "").strip()
        hint["model"] = str(payload.get("model", "") or "").strip()
        hint["input_tokens"] = int(payload.get("input_tokens", 0) or 0)
        hint["output_tokens"] = int(payload.get("output_tokens", 0) or 0)

    # Explicit CLI values override file/env defaults.
    if str(getattr(args, "provider", "") or "").strip():
        hint["provider"] = str(args.provider).strip()
    if str(getattr(args, "model", "") or "").strip():
        hint["model"] = str(args.model).strip()
    if int(getattr(args, "input_tokens", 0) or 0) > 0:
        hint["input_tokens"] = int(args.input_tokens)
    if int(getattr(args, "output_tokens", 0) or 0) > 0:
        hint["output_tokens"] = int(args.output_tokens)

    return hint


def _emit_notify(
    root: Path,
    mode: str,
    status: str,
    message: str,
    severity: str = "info",
    milestone: str = "",
    details: Dict[str, Any] | None = None,
    run_type: str = "",
) -> None:
    try:
        notify_event(
            root=root,
            source="operator_runtime.main",
            event=mode,
            status=status,
            message=message,
            severity=severity,
            milestone=milestone,
            details=details or {},
            run_type=run_type,
        )
    except Exception:
        # Notifications must never block contract execution.
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, help="Operator task request")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["inspect", "propose", "execute"],
        help="Contract mode",
    )
    parser.add_argument(
        "--channel",
        default="text",
        choices=["text", "voice"],
        help="Input channel. Voice is simulated ingress for now.",
    )
    parser.add_argument("--plan-id", help="Required for execute mode")
    parser.add_argument("--approval-token", help="Required for execute mode")
    parser.add_argument("--provider", default="", help="Optional provider name for inspect cost calibration")
    parser.add_argument("--model", default="", help="Optional model name for inspect cost calibration")
    parser.add_argument("--input-tokens", type=int, default=0, help="Optional provider input token usage for inspect")
    parser.add_argument("--output-tokens", type=int, default=0, help="Optional provider output token usage for inspect")
    parser.add_argument(
        "--usage-json",
        default="",
        help="Optional path to JSON file with provider/model/input_tokens/output_tokens for inspect cost calibration",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute mutating action for approved plan. If omitted, execute mode is dry-run.",
    )
    parser.add_argument(
        "--run-type",
        default="prod",
        choices=["prod", "test", "drill"],
        help="Run classification tag for scorecard and stage-gate reporting.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    config_path = root / "config" / "context_sources.json"
    guardrails_path = root / "config" / "guardrails.json"
    plans_dir = root / "artifacts" / "plans"
    proof_dir = root / "artifacts" / "proofs"
    lane_catalog_path = root / "config" / "lane_catalog.json"
    lanes_dir = root / "config" / "lanes"

    runtime_schema_errors = validate_runtime_configs(root)
    _emit_notify(
        root,
        mode=args.mode,
        status="start",
        message=f"runtime request received ({args.mode})",
        milestone="run_start",
        details={"channel": args.channel, "run_type": args.run_type},
        run_type=args.run_type,
    )
    if runtime_schema_errors:
        print("\nSchema validation failure")
        for err in runtime_schema_errors:
            print(f"- {err}")
        _emit_notify(
            root,
            mode=args.mode,
            status="failure",
            message="schema validation failed",
            severity="error",
            milestone="schema_validation_failed",
            details={"error_count": len(runtime_schema_errors)},
            run_type=args.run_type,
        )
        return

    context_rows = build_preflight_context(config_path)
    print_context(context_rows)

    if not preflight_ok(context_rows):
        print("\nPreflight failed: required context missing. Stop before planning.")
        _emit_notify(
            root,
            mode=args.mode,
            status="failure",
            message="preflight failed: required context missing",
            severity="error",
            milestone="preflight_failed",
            run_type=args.run_type,
        )
        return

    guardrails = load_guardrails(guardrails_path)
    validation_errors = validate_mode_and_channel(args.mode, args.channel, guardrails)
    if validation_errors:
        print("\nGuardrail failure")
        for err in validation_errors:
            print(f"- {err}")
        _emit_notify(
            root,
            mode=args.mode,
            status="failure",
            message="guardrail validation failed",
            severity="error",
            milestone="guardrail_failed",
            details={"error_count": len(validation_errors)},
            run_type=args.run_type,
        )
        return

    print("\nTask")
    print(f"- request: {args.task}")
    print(f"- mode: {args.mode}")
    print(f"- channel: {args.channel}")

    if args.mode == "inspect":
        try:
            usage_hint = _load_usage_hint(args)
        except Exception as exc:
            print("\nGuardrail failure")
            print(f"- invalid inspect usage hint: {exc}")
            _emit_notify(
                root,
                mode="inspect",
                status="failure",
                message="invalid inspect usage hint",
                severity="error",
                milestone="usage_hint_invalid",
                details={"error": str(exc)},
                run_type=args.run_type,
            )
            return

        action_id = resolve_action_id(args.task, lane_catalog_path)
        report, artifact_path = generate_inspect_report(
            root=root,
            task=args.task,
            action_id=action_id,
            lanes_dir=lanes_dir,
            context_rows=context_rows,
            usage_hint=usage_hint,
        )

        print("\nInspect Contract")
        print("- read-only triage only")
        print("- no mutating commands")
        print("- output: ranked hypotheses with evidence references")
        print(f"- branch: {report.get('branch')}")
        print(f"- lane_match: {report.get('lane_match') or 'none'}")
        print(f"- token_estimate: {report.get('token_estimate')}")
        print(f"- estimated_cost_usd: {report.get('estimated_cost_usd')}")
        print(f"- cost_source: {report.get('cost_source')}")
        if report.get("provider") and report.get("model"):
            print(f"- provider_model: {report.get('provider')}/{report.get('model')}")
        print(f"- inspect_artifact: {artifact_path}")

        print("\nInspect Hypotheses")
        for item in report.get("hypotheses", []):
            print(
                f"- [{item.get('rank')}|c={item.get('confidence', 0):.2f}] "
                f"{item.get('claim', '')}"
            )
            print(f"  sources: {', '.join(item.get('sources', []))}")

        print("\nInspect Evidence")
        for ev in report.get("evidence", [])[:6]:
            print(
                f"- {ev.get('source_id')}:{ev.get('line')} "
                f"{ev.get('snippet', '')}"
            )

        lane_drafts = list(report.get("lane_drafts", []))
        if lane_drafts:
            print("\nStage 5 Lane Draft Candidates")
            for draft in lane_drafts:
                validity = "valid" if bool(draft.get("schema_valid", False)) else "invalid"
                rec = " [recommended]" if bool(draft.get("recommended", False)) else ""
                print(
                    f"- {draft.get('strategy')}: {validity}{rec} | "
                    f"score={draft.get('score', 0)} | "
                    f"action_id={draft.get('action_id','')} | "
                    f"service={draft.get('service','')}"
                )
                print(f"  artifact: {draft.get('artifact_path','')}")
                print(
                    f"  recommendation_eligible: {bool(draft.get('recommendation_eligible', False))}"
                )
                blockers = list(draft.get("recommendation_blockers", []))
                if blockers:
                    print(f"  recommendation_blockers: {', '.join(blockers)}")
                if draft.get("schema_errors"):
                    print(f"  schema_errors: {len(draft.get('schema_errors', []))}")

        cited_sources: List[str] = []
        seen_sources = set()
        for item in report.get("hypotheses", []):
            for src in item.get("sources", []):
                src_id = str(src).strip()
                if not src_id or src_id in seen_sources:
                    continue
                seen_sources.add(src_id)
                cited_sources.append(src_id)

        stage4_errors: List[str] = []
        if (
            guardrails.stage4_enforce_open_ended_thresholds
            and report.get("branch") == "open_ended"
        ):
            traceability_ratio = float(report.get("traceability_ratio", 0.0))
            if traceability_ratio < guardrails.stage4_min_traceability_ratio:
                stage4_errors.append(
                    "open-ended inspect traceability_ratio "
                    f"{traceability_ratio:.4f} below policy minimum "
                    f"{guardrails.stage4_min_traceability_ratio:.4f}"
                )

            token_estimate = int(report.get("token_estimate", 0) or 0)
            if token_estimate > guardrails.stage4_max_open_ended_token_estimate:
                stage4_errors.append(
                    "open-ended inspect token_estimate "
                    f"{token_estimate} exceeds policy maximum "
                    f"{guardrails.stage4_max_open_ended_token_estimate}"
                )

            estimated_cost = float(report.get("estimated_cost_usd", 0.0) or 0.0)
            if estimated_cost > guardrails.stage4_max_open_ended_estimated_cost_usd:
                stage4_errors.append(
                    "open-ended inspect estimated_cost_usd "
                    f"{estimated_cost:.6f} exceeds policy maximum "
                    f"{guardrails.stage4_max_open_ended_estimated_cost_usd:.6f}"
                )

        if stage4_errors:
            print("\nGuardrail failure")
            for err in stage4_errors:
                print(f"- {err}")

            append_run_entry(
                root,
                mode="inspect",
                run_type=args.run_type,
                task=args.task,
                plan_id=str(report.get("inspect_id", "")),
                action_id="",
                status="blocked_stage4_policy",
                max_entries=guardrails.artifacts.max_run_index_entries,
                proof_path=str(artifact_path),
                recommendation_count=len(report.get("hypotheses", [])),
                inspect_branch=str(report.get("branch", "")),
                inspect_token_estimate=int(report.get("token_estimate", 0)),
                inspect_estimated_cost_usd=float(report.get("estimated_cost_usd", 0.0)),
                inspect_cited_sources=cited_sources,
                inspect_cost_source=str(report.get("cost_source", "")),
                inspect_provider=str(report.get("provider", "")),
                inspect_model=str(report.get("model", "")),
                inspect_input_tokens=int(report.get("input_tokens", 0) or 0),
                inspect_output_tokens=int(report.get("output_tokens", 0) or 0),
            )
            _emit_notify(
                root,
                mode="inspect",
                status="failure",
                message="inspect blocked by stage4 policy thresholds",
                severity="warning",
                milestone="blocked_stage4_policy",
                details={
                    "branch": str(report.get("branch", "")),
                    "token_estimate": int(report.get("token_estimate", 0) or 0),
                    "estimated_cost_usd": float(report.get("estimated_cost_usd", 0.0) or 0.0),
                    "errors": stage4_errors,
                },
                run_type=args.run_type,
            )
            return

        append_run_entry(
            root,
            mode="inspect",
            run_type=args.run_type,
            task=args.task,
            plan_id=str(report.get("inspect_id", "")),
            action_id=action_id if report.get("branch") == "lane_matched" else "",
            status="inspected",
            max_entries=guardrails.artifacts.max_run_index_entries,
            proof_path=str(artifact_path),
            recommendation_count=len(report.get("hypotheses", [])),
            inspect_branch=str(report.get("branch", "")),
            inspect_token_estimate=int(report.get("token_estimate", 0)),
            inspect_estimated_cost_usd=float(report.get("estimated_cost_usd", 0.0)),
            inspect_cited_sources=cited_sources,
            inspect_cost_source=str(report.get("cost_source", "")),
            inspect_provider=str(report.get("provider", "")),
            inspect_model=str(report.get("model", "")),
            inspect_input_tokens=int(report.get("input_tokens", 0) or 0),
            inspect_output_tokens=int(report.get("output_tokens", 0) or 0),
        )
        _emit_notify(
            root,
            mode="inspect",
            status="complete",
            message="inspect completed",
            severity="success",
            milestone="inspect_complete",
            details={
                "branch": str(report.get("branch", "")),
                "token_estimate": int(report.get("token_estimate", 0) or 0),
                "estimated_cost_usd": float(report.get("estimated_cost_usd", 0.0) or 0.0),
                "cost_source": str(report.get("cost_source", "")),
            },
            run_type=args.run_type,
        )
        return

    if args.mode == "propose":
        plan_record = build_media_incident_plan(args.task, args.channel, args.mode)
        plan_record.action_id = resolve_action_id(args.task, lane_catalog_path)

        # Safe live observation pass in propose mode (read-only only).
        if plan_record.action_id:
            preview_result = execute_media_lane(
                plan_to_dict(plan_record), apply=False, lanes_dir=lanes_dir
            )
            plan_record.recommendations = preview_result.get("recommendations", [])
            prioritize_plan_from_recommendations(
                plan_record, guardrails.recommendation_reorder_min_confidence
            )

        plan_path = save_plan(plan_record, plans_dir)
        plan = load_plan(plan_record.plan_id, plans_dir)

        print_plan_summary(plan)
        print("\nProposal Artifact")
        print(f"- path: {plan_path}")
        print("- execute requires both --plan-id and --approval-token")
        print("- execute defaults to dry-run; add --apply to run mutating steps")
        print(f"- action_id: {plan.get('action_id') or 'none'}")
        if plan.get("recommendations"):
            print("- recommendations:")
            for rec in plan.get("recommendations", [])[:3]:
                print(
                    f"  - [{rec.get('severity','info')}|c={rec.get('confidence',0):.2f}] {rec.get('recommendation','')}"
                )
                if "reorder_reason" in rec:
                    state = "eligible" if rec.get("reorder_eligible") else "advisory"
                    applied = " applied" if rec.get("reorder_applied") else ""
                    print(f"    reorder: {state}{applied}; {rec.get('reorder_reason')}")
        print(f"- approval_token_for_test: {plan['approval_token']}")

        append_run_entry(
            root,
            mode="propose",
            run_type=args.run_type,
            task=plan["task"],
            plan_id=plan["plan_id"],
            action_id=plan.get("action_id", ""),
            status="proposed",
            max_entries=guardrails.artifacts.max_run_index_entries,
            recommendation_count=len(plan.get("recommendations", [])),
        )
        _emit_notify(
            root,
            mode="propose",
            status="complete",
            message="proposal generated",
            severity="success",
            milestone="proposal_complete",
            details={
                "plan_id": str(plan.get("plan_id", "")),
                "action_id": str(plan.get("action_id", "")),
                "recommendation_count": len(plan.get("recommendations", [])),
            },
            run_type=args.run_type,
        )

        gc_summary = run_artifact_gc(
            root,
            max_plan_files=guardrails.artifacts.max_plan_files,
            max_plan_age_days=guardrails.artifacts.max_plan_age_days,
            max_proof_files=guardrails.artifacts.max_proof_files,
            max_proof_age_days=guardrails.artifacts.max_proof_age_days,
            soft_deprecate_enabled=guardrails.artifacts.soft_deprecate_enabled,
            archive_retention_days=guardrails.artifacts.archive_retention_days,
        )
        print("- artifact_gc:")
        print(
            "  - mode="
            f"{'soft_deprecate' if gc_summary['soft_deprecate_enabled'] else 'hard_delete'}"
        )
        print(
            "  - plans_archived="
            f"{gc_summary['plans_archived']} (age={gc_summary['plans_archived_by_age']}, "
            f"count={gc_summary['plans_archived_by_count']})"
        )
        print(
            "  - proofs_archived="
            f"{gc_summary['proofs_archived']} (age={gc_summary['proofs_archived_by_age']}, "
            f"count={gc_summary['proofs_archived_by_count']})"
        )
        print(
            "  - hard_deleted="
            f"plans={gc_summary['plans_deleted']}, proofs={gc_summary['proofs_deleted']}"
        )
        print(
            "  - archive_purged="
            f"{gc_summary['archive_purged']}"
        )
        return

    if args.mode == "execute":
        if not args.plan_id:
            print("\nGuardrail failure")
            print("- --plan-id is required for execute mode")
            append_run_entry(
                root,
                mode="execute",
                run_type=args.run_type,
                task=args.task,
                plan_id="",
                action_id="",
                status="blocked_missing_plan_id",
                max_entries=guardrails.artifacts.max_run_index_entries,
            )
            _emit_notify(
                root,
                mode="execute",
                status="failure",
                message="execute blocked: missing plan id",
                severity="error",
                milestone="blocked_missing_plan_id",
                run_type=args.run_type,
            )
            return

        plan = load_plan(args.plan_id, plans_dir)
        approval_errors = verify_approval(
            args.approval_token or "", guardrails, plan.get("approval_token", "")
        )
        if approval_errors:
            print("\nGuardrail failure")
            for err in approval_errors:
                print(f"- {err}")
            append_run_entry(
                root,
                mode="execute",
                run_type=args.run_type,
                task=args.task,
                plan_id=str(plan.get("plan_id", "")),
                action_id=str(plan.get("action_id", "")),
                status="blocked_approval",
                max_entries=guardrails.artifacts.max_run_index_entries,
            )
            _emit_notify(
                root,
                mode="execute",
                status="failure",
                message="execute blocked: approval failed",
                severity="warning",
                milestone="blocked_approval",
                details={"error_count": len(approval_errors)},
                run_type=args.run_type,
            )
            return

        risk_policy_errors = evaluate_execute_risk_controls(plan, guardrails, args.apply)
        if risk_policy_errors:
            print("\nGuardrail failure")
            for err in risk_policy_errors:
                print(f"- {err}")
            append_run_entry(
                root,
                mode="execute",
                run_type=args.run_type,
                task=args.task,
                plan_id=str(plan.get("plan_id", "")),
                action_id=str(plan.get("action_id", "")),
                status="blocked_risk_policy",
                max_entries=guardrails.artifacts.max_run_index_entries,
            )
            _emit_notify(
                root,
                mode="execute",
                status="failure",
                message="execute blocked: risk policy",
                severity="warning",
                milestone="blocked_risk_policy",
                details={"error_count": len(risk_policy_errors)},
                run_type=args.run_type,
            )
            return

        execution_result = execute_media_lane(plan, apply=args.apply, lanes_dir=lanes_dir)
        proof_path = write_proof_pack(plan, proof_dir, execution_result)

        print("\nExecute Contract")
        print(f"- plan_id: {plan['plan_id']}")
        print("- approval: validated")
        print(f"- action_id: {plan.get('action_id') or 'none'}")
        print(f"- apply: {args.apply}")
        print(f"- proof_pack: {proof_path}")
        print("- status: execute contract complete")

        append_run_entry(
            root,
            mode="execute",
            run_type=args.run_type,
            task=plan["task"],
            plan_id=plan["plan_id"],
            action_id=plan.get("action_id", ""),
            status="executed",
            max_entries=guardrails.artifacts.max_run_index_entries,
            proof_path=str(proof_path),
            verification_status=execution_result.get("verification", {}).get("status", "unknown"),
            recommendation_count=len(execution_result.get("recommendations", [])),
        )

        state_result = write_canonical_state(
            root,
            plan=plan,
            proof_path=proof_path,
            execution_result=execution_result,
            apply=args.apply,
        )
        print("- state_writer:")
        print(f"  - enabled={state_result.get('enabled', False)}")
        updated_targets = state_result.get("updated", [])
        print(f"  - targets_updated={len(updated_targets)}")
        for target in updated_targets:
            print(f"    - {target}")

        gc_summary = run_artifact_gc(
            root,
            max_plan_files=guardrails.artifacts.max_plan_files,
            max_plan_age_days=guardrails.artifacts.max_plan_age_days,
            max_proof_files=guardrails.artifacts.max_proof_files,
            max_proof_age_days=guardrails.artifacts.max_proof_age_days,
            soft_deprecate_enabled=guardrails.artifacts.soft_deprecate_enabled,
            archive_retention_days=guardrails.artifacts.archive_retention_days,
        )
        print("- artifact_gc:")
        print(
            "  - mode="
            f"{'soft_deprecate' if gc_summary['soft_deprecate_enabled'] else 'hard_delete'}"
        )
        print(
            "  - plans_archived="
            f"{gc_summary['plans_archived']} (age={gc_summary['plans_archived_by_age']}, "
            f"count={gc_summary['plans_archived_by_count']})"
        )
        print(
            "  - proofs_archived="
            f"{gc_summary['proofs_archived']} (age={gc_summary['proofs_archived_by_age']}, "
            f"count={gc_summary['proofs_archived_by_count']})"
        )
        print(
            "  - hard_deleted="
            f"plans={gc_summary['plans_deleted']}, proofs={gc_summary['proofs_deleted']}"
        )
        print(
            "  - archive_purged="
            f"{gc_summary['archive_purged']}"
        )
        _emit_notify(
            root,
            mode="execute",
            status="complete",
            message="execute contract complete",
            severity="success",
            milestone="execute_complete",
            details={
                "plan_id": str(plan.get("plan_id", "")),
                "action_id": str(plan.get("action_id", "")),
                "apply": bool(args.apply),
                "verification_status": str(execution_result.get("verification", {}).get("status", "unknown")),
            },
            run_type=args.run_type,
        )
        return


if __name__ == "__main__":
    main()
