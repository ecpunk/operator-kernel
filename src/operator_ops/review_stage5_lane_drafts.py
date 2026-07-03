"""
Stage 5 lane draft review and promotion tool.

Workflow:
  1. list      — show pending draft groups ranked by score desc, newest first.
  2. review    — show full detail for a specific inspect stamp.
  3. approve   — promote a draft into the lane catalog (dry-run by default).

Promotion writes:
  - config/lanes/{action_id}.json          (lane spec)
  - config/lane_catalog.json               (keyword mappings entry)
  - config/lane_lifecycle.json             (sets state=candidate)
  - artifacts/stage5/draft_review_{stamp}.json  (decision record)

Approval requires --approval-token (min length from execution guardrails).
Placeholder service/endpoint must be replaced before promotion.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse


PLACEHOLDER_SERVICES: Set[str] = {"", "candidate_service", "myservice"}
PLACEHOLDER_ENDPOINTS: Set[str] = {"", "http://127.0.0.1:8080"}
KNOWN_STRATEGIES = ("evidence_driven", "template_first", "nearest_lane")
DEFAULT_STRATEGY = "evidence_driven"
DEFAULT_KEYWORD_PRIORITY = 100


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _is_placeholder_service(value: str) -> bool:
    return str(value).strip().lower() in PLACEHOLDER_SERVICES


def _is_placeholder_endpoint(value: str) -> bool:
    return str(value).strip().lower() in PLACEHOLDER_ENDPOINTS


def _extract_strategy(stem: str) -> Optional[str]:
    for s in KNOWN_STRATEGIES:
        if stem.endswith(f"_{s}"):
            return s
    return None


def _extract_stamp_from_stem(stem: str, strategy: str) -> str:
    # stem: lane_draft_{inspect_stamp}_{strategy}
    prefix = "lane_draft_"
    inner = stem[len(prefix):]
    # strip strategy suffix
    suffix = f"_{strategy}"
    if inner.endswith(suffix):
        inner = inner[: -len(suffix)]
    return inner


# ---------------------------------------------------------------------------
# Load draft artifacts grouped by inspect stamp
# ---------------------------------------------------------------------------

def _load_draft_groups(root: Path, days: int = 0) -> Dict[str, Dict[str, Any]]:
    """Return dict of inspect_stamp -> group dict with per-strategy drafts."""
    drafts_dir = root / "artifacts" / "lane_drafts"
    groups: Dict[str, Dict[str, Any]] = {}

    if not drafts_dir.exists():
        return groups

    cutoff: Optional[datetime] = None
    if days > 0:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=timezone.utc)
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    for path in sorted(drafts_dir.glob("lane_draft_[0-9]*.json")):
        strategy = _extract_strategy(path.stem)
        if strategy is None:
            continue

        payload = _load_json(path, {})
        if not payload:
            continue

        generated_at_str = str(payload.get("generated_at", "")).strip()
        if not generated_at_str:
            continue

        try:
            ts = datetime.fromisoformat(generated_at_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if cutoff is not None and ts < cutoff:
            continue

        inspect_stamp = payload.get("inspect_stamp") or _extract_stamp_from_stem(path.stem, strategy)
        task = str(payload.get("task", "")).strip()

        if inspect_stamp not in groups:
            groups[inspect_stamp] = {
                "inspect_stamp": inspect_stamp,
                "task": task,
                "generated_at": generated_at_str,
                "strategies": {},
            }

        spec = payload.get("candidate_lane_spec", {})
        groups[inspect_stamp]["strategies"][strategy] = {
            "strategy": strategy,
            "schema_valid": bool(payload.get("schema_valid", False)),
            "schema_errors": list(payload.get("schema_errors", [])),
            "action_id": str(spec.get("action_id", "")),
            "service": str(spec.get("service", "")),
            "endpoint": str(spec.get("endpoint", "")),
            "candidate_lane_spec": spec,
            "path": str(path),
        }

    return groups


def _load_guardrails(root: Path) -> Dict[str, Any]:
    g = _load_json(root / "config" / "guardrails.json", {})
    return {
        "token_min_length": int(g.get("execution", {}).get("token_min_length", 8)),
    }


def _load_review_registry(root: Path) -> Dict[str, Any]:
    path = root / "artifacts" / "stage5" / "draft_review_registry.json"
    d = _load_json(path, {})
    if not isinstance(d, dict):
        return {}
    return d


def _save_review_registry(root: Path, registry: Dict[str, Any]) -> None:
    path = root / "artifacts" / "stage5" / "draft_review_registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, registry)


def _already_reviewed(registry: Dict[str, Any], stamp: str) -> Optional[str]:
    """Returns decision string if stamp already reviewed, else None."""
    entry = registry.get(stamp)
    if not entry:
        return None
    return str(entry.get("decision", ""))


# ---------------------------------------------------------------------------
# Scoring heuristic (mirrors evaluate_stage5_lane_drafts.py approach)
# ---------------------------------------------------------------------------

def _score_draft(entry: Dict[str, Any]) -> float:
    score = 0.0
    if entry.get("schema_valid"):
        score += 40.0
    service = str(entry.get("service", ""))
    if not _is_placeholder_service(service):
        score += 30.0
    endpoint = str(entry.get("endpoint", ""))
    if not _is_placeholder_endpoint(endpoint):
        score += 20.0
    action_id = str(entry.get("action_id", ""))
    if action_id and not action_id.startswith("candidate_"):
        score += 10.0
    return score


# ---------------------------------------------------------------------------
# list command
# ---------------------------------------------------------------------------

def _cmd_list(root: Path, days: int, limit: int, show_reviewed: bool) -> None:
    groups = _load_draft_groups(root, days)
    registry = _load_review_registry(root)

    ranked: List[Tuple[str, Dict[str, Any], float]] = []
    for stamp, group in groups.items():
        primary_entry = group["strategies"].get(DEFAULT_STRATEGY) or next(
            iter(group["strategies"].values()), {}
        )
        score = _score_draft(primary_entry)
        ranked.append((stamp, group, score))

    ranked.sort(key=lambda x: (-x[2], x[0]), reverse=False)
    ranked = sorted(ranked, key=lambda x: (-x[2], x[0]))

    shown = 0
    for stamp, group, score in ranked:
        already = _already_reviewed(registry, stamp)
        if not show_reviewed and already:
            continue
        primary_entry = group["strategies"].get(DEFAULT_STRATEGY) or {}
        service = primary_entry.get("service", "?")
        schema_ok = primary_entry.get("schema_valid", False)
        reviewed_tag = f" [{already.upper()}]" if already else " [PENDING]"
        placeholder_tag = " [placeholder-service]" if _is_placeholder_service(service) else ""
        schema_tag = "" if schema_ok else " [SCHEMA-INVALID]"
        print(
            f"  {stamp}  score={score:.0f}  service={service}{placeholder_tag}"
            f"{schema_tag}{reviewed_tag}"
        )
        print(f"    task: {group['task']}")
        print(f"    strategies: {', '.join(group['strategies'].keys())}")
        print()
        shown += 1
        if limit > 0 and shown >= limit:
            break

    if shown == 0:
        if show_reviewed:
            print("  No draft groups found.")
        else:
            print("  No pending (unreviewed) draft groups found. Use --show-reviewed to include all.")


# ---------------------------------------------------------------------------
# review command
# ---------------------------------------------------------------------------

def _cmd_review(root: Path, stamp: str) -> None:
    groups = _load_draft_groups(root, days=0)
    registry = _load_review_registry(root)

    if stamp not in groups:
        print(f"ERROR: stamp not found in draft artifacts: {stamp}", file=sys.stderr)
        sys.exit(1)

    group = groups[stamp]
    already = _already_reviewed(registry, stamp)

    print(f"Draft group: {stamp}")
    print(f"  Task:           {group['task']}")
    print(f"  Generated at:   {group['generated_at']}")
    if already:
        print(f"  Already reviewed: {already.upper()}")
    print()

    for strategy in KNOWN_STRATEGIES:
        entry = group["strategies"].get(strategy)
        if not entry:
            print(f"  Strategy: {strategy}  — not present")
            continue
        spec = entry.get("candidate_lane_spec", {})
        service = entry.get("service", "")
        endpoint = entry.get("endpoint", "")
        schema_ok = entry.get("schema_valid", False)
        score = _score_draft(entry)
        placeholder_svc = _is_placeholder_service(service)
        placeholder_ep = _is_placeholder_endpoint(endpoint)

        print(f"  Strategy: {strategy}  (score={score:.0f})")
        print(f"    action_id:    {entry.get('action_id', '?')}")
        print(f"    service:      {service}{'  *** PLACEHOLDER — must override ***' if placeholder_svc else ''}")
        print(f"    endpoint:     {endpoint}{'  *** PLACEHOLDER — must override ***' if placeholder_ep else ''}")
        print(f"    schema_valid: {schema_ok}")
        if not schema_ok:
            for err in entry.get("schema_errors", []):
                print(f"      schema error: {err}")
        print(f"    lane:         {spec.get('lane', '?')}")
        print(f"    lane_class:   {spec.get('lane_class', '?')}")
        print(f"    artifact:     {entry['path']}")
        print()

    print("To approve:")
    print(f"  PYTHONPATH=src python3 src/operator_ops/review_stage5_lane_drafts.py \\")
    print(f"    --approve {stamp} \\")
    print(f"    --strategy evidence_driven \\")
    print(f"    --service <real-service-name> \\")
    print(f"    --endpoint <real-endpoint-url> \\")
    print(f"    --keywords <kw1> [<kw2> ...] \\")
    print(f"    --approval-token <token> \\")
    print(f"    --apply")


# ---------------------------------------------------------------------------
# approve command
# ---------------------------------------------------------------------------

def _validate_approval_inputs(
    group: Dict[str, Any],
    strategy: str,
    service: str,
    endpoint: str,
    keywords: List[str],
    token: str,
    token_min_length: int,
) -> List[str]:
    errors: List[str] = []

    if strategy not in KNOWN_STRATEGIES:
        errors.append(f"unknown strategy: {strategy}")
    elif strategy not in group["strategies"]:
        errors.append(f"strategy {strategy} not present in this draft group")
    else:
        entry = group["strategies"][strategy]
        if not entry.get("schema_valid"):
            errors.append(f"selected draft ({strategy}) is schema-invalid; cannot promote")

    if _is_placeholder_service(service):
        errors.append("--service must be a real service name (not a placeholder)")

    if not endpoint.strip():
        errors.append("--endpoint is required")

    if not keywords:
        errors.append("--keywords is required (at least one keyword for catalog mapping)")

    if not token or len(token) < token_min_length:
        errors.append(f"--approval-token must be at least {token_min_length} characters")

    return errors


def _endpoint_base(url: str) -> str:
    """Return scheme+host+port from a URL, e.g. 'http://<HOST>:8123'."""
    try:
        p = urlparse(url)
        base = f"{p.scheme}://{p.hostname}"
        if p.port:
            base += f":{p.port}"
        return base
    except Exception:
        return url


def _rewrite_probe_commands(probes: List[Dict[str, Any]], old_base: str, new_base: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Replace old_base with new_base in every probe command string.
    Returns (updated_probes, list_of_warnings_for_probes_that_still_differ)."""
    warnings: List[str] = []
    updated: List[Dict[str, Any]] = []
    for probe in probes:
        p = dict(probe)
        cmd = str(p.get("command", ""))
        if old_base and old_base in cmd:
            p["command"] = cmd.replace(old_base, new_base)
        elif new_base not in cmd:
            # Probe references something we couldn't automatically rewrite
            warnings.append(
                f"probe '{p.get('name', '?')}' command does not reference the declared endpoint "
                f"({new_base}); review manually: {cmd[:120]}"
            )
        updated.append(p)
    return updated, warnings


def _build_promoted_spec(
    entry: Dict[str, Any],
    service: str,
    endpoint: str,
    action_id_override: Optional[str],
) -> Tuple[Dict[str, Any], List[str]]:
    """Returns (promoted_spec, probe_warnings)."""
    spec = dict(entry.get("candidate_lane_spec", {}))
    old_endpoint = str(spec.get("endpoint", ""))
    spec["service"] = service
    spec["endpoint"] = endpoint
    if action_id_override:
        spec["action_id"] = action_id_override

    # Propagate the real endpoint into every verify_probe command.
    # The draft may have baked in a different host/port from inspect evidence.
    old_base = _endpoint_base(old_endpoint) if old_endpoint else ""
    new_base = _endpoint_base(endpoint)
    probes = list(spec.get("verify_probes", []))
    if probes:
        probes, warnings = _rewrite_probe_commands(probes, old_base, new_base)
        spec["verify_probes"] = probes
    else:
        warnings = []

    return spec, warnings


def _safe_action_id(service: str, strategy: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", service.lower()).strip("_")
    suffix = {"evidence_driven": "v1", "template_first": "v1", "nearest_lane": "v1"}.get(strategy, "v1")
    return f"{slug}_safe_recovery_{suffix}"


def _cmd_approve(
    root: Path,
    stamp: str,
    strategy: str,
    service: str,
    endpoint: str,
    keywords: List[str],
    keyword_priority: int,
    token: str,
    action_id_override: Optional[str],
    dry_run: bool,
) -> None:
    groups = _load_draft_groups(root, days=0)
    guardrails = _load_guardrails(root)
    registry = _load_review_registry(root)

    if stamp not in groups:
        print(f"ERROR: stamp not found: {stamp}", file=sys.stderr)
        sys.exit(1)

    group = groups[stamp]
    already = _already_reviewed(registry, stamp)
    if already and already != "rejected":
        print(f"ERROR: stamp {stamp} already reviewed with decision: {already}", file=sys.stderr)
        sys.exit(1)

    errors = _validate_approval_inputs(
        group, strategy, service, endpoint, keywords,
        token, guardrails["token_min_length"],
    )
    if errors:
        for e in errors:
            print(f"VALIDATION ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    entry = group["strategies"][strategy]
    final_action_id = (
        action_id_override.strip()
        if action_id_override and action_id_override.strip()
        else _safe_action_id(service, strategy)
    )

    promoted_spec, probe_warnings = _build_promoted_spec(entry, service, endpoint, final_action_id)

    # Paths that will be written
    lane_spec_path = root / "config" / "lanes" / f"{final_action_id}.json"
    catalog_path = root / "config" / "lane_catalog.json"
    lifecycle_path = root / "config" / "lane_lifecycle.json"
    review_artifact_path = (
        root / "artifacts" / "stage5" / f"draft_review_{stamp}.json"
    )

    # Build catalog update
    catalog = _load_json(catalog_path, {"version": 1, "resolution_policy": {}, "mappings": []})
    existing_ids = {m.get("action_id") for m in catalog.get("mappings", [])}

    new_mapping = {
        "action_id": final_action_id,
        "keywords": keywords,
        "priority": keyword_priority,
    }

    # Decision artifact
    decision_artifact = {
        "version": 1,
        "generated_at": _utc_now_iso(),
        "inspect_stamp": stamp,
        "task": group["task"],
        "strategy": strategy,
        "decision": "approved",
        "dry_run": dry_run,
        "promoted_action_id": final_action_id,
        "service": service,
        "endpoint": endpoint,
        "keywords": keywords,
        "keyword_priority": keyword_priority,
        "promoted_spec": promoted_spec,
        "source_artifact": entry["path"],
    }

    if probe_warnings:
        print("PROBE WARNINGS (review verify_probes before onboarding):")
        for w in probe_warnings:
            print(f"  WARNING: {w}")
        print()

    if dry_run:
        print(f"DRY RUN — would promote stamp {stamp} using strategy {strategy}")
        print()
        print(f"  action_id:    {final_action_id}")
        print(f"  service:      {service}")
        print(f"  endpoint:     {endpoint}")
        print(f"  keywords:     {keywords}")
        print(f"  priority:     {keyword_priority}")
        if promoted_spec.get("verify_probes"):
            print(f"  verify_probes (after endpoint propagation):")
            for p in promoted_spec["verify_probes"]:
                print(f"    [{p.get('name','?')}] {p.get('command','?')[:120]}")
        print()
        if final_action_id in existing_ids:
            print(f"  WARNING: action_id {final_action_id} already exists in lane catalog mapping")
        if lane_spec_path.exists():
            print(f"  WARNING: lane spec already exists: {lane_spec_path}")
        print()
        print(f"  Would write:")
        print(f"    {lane_spec_path}")
        print(f"    {catalog_path}  (append mapping)")
        print(f"    {lifecycle_path}  (set candidate)")
        print(f"    {review_artifact_path}")
        print()
        print("  Re-run with --apply to execute.")
        return

    # --- Apply ---
    if final_action_id in existing_ids:
        print(f"ERROR: action_id {final_action_id} already in lane catalog — use --action-id to choose a different id", file=sys.stderr)
        sys.exit(1)

    if lane_spec_path.exists():
        print(f"ERROR: lane spec already exists: {lane_spec_path} — use --action-id to choose a different id", file=sys.stderr)
        sys.exit(1)

    # Write lane spec
    _write_json(lane_spec_path, promoted_spec)

    # Update catalog
    mappings = list(catalog.get("mappings", []))
    mappings.append(new_mapping)
    catalog["mappings"] = mappings
    _write_json(catalog_path, catalog)

    # Update lifecycle (candidate state)
    lifecycle = _load_json(lifecycle_path, {"version": 1, "actions": {}})
    actions = dict(lifecycle.get("actions", {}))
    actions[final_action_id] = {"state": "candidate"}
    lifecycle["actions"] = actions
    _write_json(lifecycle_path, lifecycle)

    # Write review artifact
    _write_json(review_artifact_path, decision_artifact)

    # Update registry
    registry[stamp] = {
        "decision": "approved",
        "decided_at": _utc_now_iso(),
        "promoted_action_id": final_action_id,
        "strategy": strategy,
    }
    _save_review_registry(root, registry)

    print(f"PROMOTED: {final_action_id}")
    print(f"  Lane spec written:     {lane_spec_path}")
    print(f"  Catalog updated:       {catalog_path}")
    print(f"  Lifecycle set:         candidate  ({lifecycle_path})")
    print(f"  Decision artifact:     {review_artifact_path}")
    print()
    print("Next steps:")
    print(f"  1. Edit {lane_spec_path} to fill in real verify_probes for the service.")
    print(f"  2. Run lane onboarding tests: PYTHONPATH=src python3 src/operator_ops/run_lane_onboarding.py --action-id {final_action_id}")
    print(f"  3. Promote to active after first successful prod execution.")


# ---------------------------------------------------------------------------
# reject command
# ---------------------------------------------------------------------------

def _cmd_reject(root: Path, stamp: str, reason: str) -> None:
    groups = _load_draft_groups(root, days=0)
    registry = _load_review_registry(root)

    if stamp not in groups:
        print(f"ERROR: stamp not found: {stamp}", file=sys.stderr)
        sys.exit(1)

    already = _already_reviewed(registry, stamp)
    if already and already != "rejected":
        print(f"ERROR: stamp already reviewed with decision: {already}", file=sys.stderr)
        sys.exit(1)

    registry[stamp] = {
        "decision": "rejected",
        "decided_at": _utc_now_iso(),
        "reason": reason,
    }
    _save_review_registry(root, registry)

    review_artifact_path = root / "artifacts" / "stage5" / f"draft_review_{stamp}.json"
    _write_json(review_artifact_path, {
        "version": 1,
        "generated_at": _utc_now_iso(),
        "inspect_stamp": stamp,
        "task": groups[stamp]["task"],
        "decision": "rejected",
        "reason": reason,
    })

    print(f"REJECTED: {stamp}")
    print(f"  Reason: {reason}")
    print(f"  Decision artifact: {review_artifact_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Review and promote Stage 5 candidate lane drafts into the lane catalog.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List pending drafts
  PYTHONPATH=src python3 src/operator_ops/review_stage5_lane_drafts.py --list

  # Show full detail for one stamp
  PYTHONPATH=src python3 src/operator_ops/review_stage5_lane_drafts.py --review 20260310T060048Z

  # Dry-run approve
  PYTHONPATH=src python3 src/operator_ops/review_stage5_lane_drafts.py \\
    --approve 20260310T060048Z --strategy evidence_driven \\
    --service plex --endpoint http://127.0.0.1:32400/web \\
    --keywords plex media --approval-token mytoken123

  # Apply promotion
  PYTHONPATH=src python3 src/operator_ops/review_stage5_lane_drafts.py \\
    --approve 20260310T060048Z --strategy evidence_driven \\
    --service plex --endpoint http://127.0.0.1:32400/web \\
    --keywords plex media --approval-token mytoken123 --apply

  # Reject
  PYTHONPATH=src python3 src/operator_ops/review_stage5_lane_drafts.py \\
    --reject 20260310T060048Z --reason "synthetic task, not a real service"
""",
    )

    parser.add_argument("--list", action="store_true", dest="do_list", help="List pending draft groups.")
    parser.add_argument("--show-reviewed", action="store_true", help="Include already-reviewed drafts in --list.")
    parser.add_argument("--days", type=int, default=0, help="Lookback window (0=all).")
    parser.add_argument("--limit", type=int, default=20, help="Max groups to show in list.")
    parser.add_argument("--review", metavar="STAMP", help="Show detail for one inspect stamp.")
    parser.add_argument("--approve", metavar="STAMP", help="Approve and promote a draft.")
    parser.add_argument("--reject", metavar="STAMP", help="Reject a draft.")
    parser.add_argument("--reason", default="", help="Reason string for --reject.")
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY, choices=KNOWN_STRATEGIES, help="Strategy to promote.")
    parser.add_argument("--service", default="", help="Real service name (required for --approve).")
    parser.add_argument("--endpoint", default="", help="Real service endpoint URL (required for --approve).")
    parser.add_argument("--keywords", nargs="+", default=[], help="Lane catalog keywords for this lane.")
    parser.add_argument("--keyword-priority", type=int, default=DEFAULT_KEYWORD_PRIORITY, help="Catalog mapping priority.")
    parser.add_argument("--action-id", default="", dest="action_id", help="Override the promoted action_id.")
    parser.add_argument("--approval-token", default="", dest="approval_token", help="Approval token (required for --approve with --apply).")
    parser.add_argument("--apply", action="store_true", help="Actually write changes (default: dry-run).")

    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent.parent

    if args.do_list:
        _cmd_list(root, args.days, args.limit, args.show_reviewed)
    elif args.review:
        _cmd_review(root, args.review)
    elif args.approve:
        _cmd_approve(
            root=root,
            stamp=args.approve,
            strategy=args.strategy,
            service=args.service,
            endpoint=args.endpoint,
            keywords=args.keywords,
            keyword_priority=args.keyword_priority,
            token=args.approval_token,
            action_id_override=args.action_id if args.action_id else None,
            dry_run=not args.apply,
        )
    elif args.reject:
        _cmd_reject(root, args.reject, args.reason)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
