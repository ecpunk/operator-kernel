import argparse
import fnmatch
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


def _load_policy(root: Path) -> Dict[str, Any]:
    guardrails = _load_json(root / "config" / "guardrails.json", {})
    execution = dict(guardrails.get("execution", {}))
    learning = dict(guardrails.get("learning", {}))

    return {
        "apply_requires_approval": bool(learning.get("apply_requires_approval", True)),
        "approval_token_min_length": int(
            learning.get("approval_token_min_length", execution.get("token_min_length", 8))
        ),
        "allowed_target_globs": list(
            learning.get(
                "apply_allowed_target_globs",
                [
                    "10-CONCEPT/OPERATOR_KERNEL_CONCEPT.md",
                    "artifacts/runbooks/runbook_*.md",
                ],
            )
        ),
    }


def _is_allowed_target(target_path: str, globs: List[str]) -> bool:
    return any(fnmatch.fnmatch(target_path, pat) for pat in globs)


def _select_targets(
    targets: List[Dict[str, Any]],
    indexes: List[int],
    paths: List[str],
) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    path_set = {p.strip() for p in paths if p.strip()}
    index_set = {i for i in indexes if i > 0}

    for idx, row in enumerate(targets, start=1):
        target_path = str(row.get("target_path", "")).strip()
        if idx in index_set or target_path in path_set:
            selected.append({"index": idx, **row})

    return selected


def _select_targets_by_profile(targets: List[Dict[str, Any]], profile: str) -> List[Dict[str, Any]]:
    rows = [{"index": idx, **row} for idx, row in enumerate(targets, start=1)]
    name = profile.strip().lower()
    if name in {"none", ""}:
        return []
    if name == "concept_only":
        return [r for r in rows if str(r.get("target_path", "")).strip() == "10-CONCEPT/OPERATOR_KERNEL_CONCEPT.md"]
    if name == "runbooks_only":
        return [
            r
            for r in rows
            if str(r.get("target_path", "")).strip().startswith("artifacts/runbooks/runbook_")
        ]
    if name == "top3":
        return rows[:3]
    if name == "top5":
        return rows[:5]
    if name == "all":
        return rows
    return []


def _apply_change(text: str, row: Dict[str, Any]) -> Dict[str, Any]:
    change_type = str(row.get("change_type", "")).strip()
    anchor = str(row.get("anchor_hint", "")).strip()
    content = str(row.get("proposed_content", "")).strip()

    if not content:
        return {"status": "failed", "reason": "empty proposed content", "new_text": text}
    if content in text:
        return {"status": "already_present", "reason": "content already exists", "new_text": text}

    if change_type == "append_section":
        suffix = "\n\n" + content + "\n"
        return {"status": "applied", "reason": "appended section", "new_text": text.rstrip() + suffix}

    if change_type == "insert_section":
        if not anchor:
            return {"status": "failed", "reason": "missing anchor_hint", "new_text": text}
        pos = text.find(anchor)
        if pos == -1:
            suffix = "\n\n" + content + "\n"
            return {
                "status": "applied_with_fallback",
                "reason": "anchor not found; appended to file end",
                "new_text": text.rstrip() + suffix,
            }
        insert_at = pos + len(anchor)
        new_text = text[:insert_at] + "\n\n" + content + "\n" + text[insert_at:]
        return {"status": "applied", "reason": "inserted after anchor", "new_text": new_text}

    return {"status": "failed", "reason": f"unsupported change_type: {change_type}", "new_text": text}


def _write_artifact(root: Path, payload: Dict[str, Any]) -> Dict[str, str]:
    out_dir = root / "artifacts" / "learning"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = out_dir / f"learning_delta_apply_{stamp}.json"
    latest = out_dir / "learning_delta_apply_latest.json"

    text = json.dumps(payload, indent=2) + "\n"
    snapshot.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return {"snapshot": str(snapshot), "latest": str(latest)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--delta-artifact",
        default="artifacts/learning/learning_deltas_latest.json",
        help="Learning delta artifact path",
    )
    parser.add_argument(
        "--select-index",
        type=int,
        action="append",
        default=[],
        help="1-based target index from delta artifact (repeatable)",
    )
    parser.add_argument(
        "--select-target",
        action="append",
        default=[],
        help="Exact target_path from delta artifact (repeatable)",
    )
    parser.add_argument(
        "--profile",
        default="none",
        choices=["none", "concept_only", "runbooks_only", "top3", "top5", "all"],
        help="Policy-driven selection profile for delta targets",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="List available targets with indexes and exit without applying",
    )
    parser.add_argument("--apply", action="store_true", help="Apply selected targets (default is dry-run)")
    parser.add_argument(
        "--approval-token",
        default="",
        help="Required for --apply when policy demands approval",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    policy = _load_policy(root)

    delta_path = root / args.delta_artifact
    payload = _load_json(delta_path, {})
    review_only = bool(payload.get("review_only", True))
    targets = list(payload.get("targets", []))

    if args.list_targets:
        print("Operator Kernel Learning Delta Targets")
        print(f"- delta_artifact: {delta_path}")
        for idx, row in enumerate(targets, start=1):
            print(
                f"- {idx}: {str(row.get('target_path', '')).strip()} "
                f"| change_type={str(row.get('change_type', '')).strip()} "
                f"| anchor_hint={str(row.get('anchor_hint', '')).strip()}"
            )
        return

    selected = _select_targets(targets, args.select_index, args.select_target)
    if not selected:
        selected = _select_targets_by_profile(targets, args.profile)
    if not selected:
        raise SystemExit(
            "no targets selected; use --select-index, --select-target, or --profile"
        )

    if args.apply and review_only and policy["apply_requires_approval"]:
        token = str(args.approval_token).strip()
        if len(token) < int(policy["approval_token_min_length"]):
            raise SystemExit(
                f"approval token required with min length {policy['approval_token_min_length']} for --apply"
            )

    results: List[Dict[str, Any]] = []
    writes_performed = 0
    for row in selected:
        target_path = str(row.get("target_path", "")).strip()
        if not target_path:
            results.append(
                {
                    "index": row.get("index", 0),
                    "target_path": target_path,
                    "status": "failed",
                    "reason": "missing target_path",
                }
            )
            continue

        allowed = _is_allowed_target(target_path, policy["allowed_target_globs"])
        if not allowed:
            results.append(
                {
                    "index": row.get("index", 0),
                    "target_path": target_path,
                    "status": "blocked",
                    "reason": "target not allowlisted by policy",
                }
            )
            continue

        abs_target = root / target_path
        if not abs_target.exists():
            results.append(
                {
                    "index": row.get("index", 0),
                    "target_path": target_path,
                    "status": "failed",
                    "reason": "target file missing",
                }
            )
            continue

        current = abs_target.read_text(encoding="utf-8")
        outcome = _apply_change(current, row)
        status = str(outcome.get("status", "failed"))

        if args.apply and status in {"applied", "applied_with_fallback"}:
            abs_target.write_text(str(outcome.get("new_text", current)), encoding="utf-8")
            writes_performed += 1

        results.append(
            {
                "index": row.get("index", 0),
                "target_path": target_path,
                "status": "planned" if (not args.apply and status.startswith("applied")) else status,
                "reason": str(outcome.get("reason", "")),
                "change_type": str(row.get("change_type", "")),
                "anchor_hint": str(row.get("anchor_hint", "")),
            }
        )

    summary = {
        "applied": sum(1 for r in results if r.get("status") in {"applied", "applied_with_fallback"}),
        "planned": sum(1 for r in results if r.get("status") == "planned"),
        "already_present": sum(1 for r in results if r.get("status") == "already_present"),
        "blocked": sum(1 for r in results if r.get("status") == "blocked"),
        "failed": sum(1 for r in results if r.get("status") == "failed"),
    }

    run_payload = {
        "generated_at": _utc_now_iso(),
        "delta_artifact": str(delta_path),
        "mode": "apply" if args.apply else "dry_run",
        "review_only_input": review_only,
        "policy": policy,
        "selected_total": len(selected),
        "writes_performed": writes_performed,
        "summary": summary,
        "results": results,
    }
    artifacts = _write_artifact(root, run_payload)

    print("Operator Kernel Learning Delta Apply")
    print(f"- mode: {run_payload['mode']}")
    print(f"- selected_total: {run_payload['selected_total']}")
    print(f"- selection_profile: {args.profile}")
    print(f"- writes_performed: {writes_performed}")
    print(f"- planned: {summary['planned']}")
    print(f"- already_present: {summary['already_present']}")
    print(f"- blocked: {summary['blocked']}")
    print(f"- failed: {summary['failed']}")
    print(f"- snapshot: {artifacts['snapshot']}")
    print(f"- latest: {artifacts['latest']}")

    if args.apply and (summary["blocked"] > 0 or summary["failed"] > 0):
        raise SystemExit(2)


if __name__ == "__main__":
    main()