import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from operator_kernel.lane_catalog import load_lane_spec
from operator_kernel.schema_validator import validate_data_against_schema


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _token_estimate(text: str) -> int:
    return max(1, len(re.findall(r"[A-Za-z0-9_]+", text)))


def _cost_estimate_usd(token_count: int) -> float:
    # Stage 4 baseline estimate for budget tracking; threshold is finalized later.
    return round(token_count * 0.000002, 6)


def _load_pricing_config(root: Path) -> Dict[str, Any]:
    path = root / "config" / "pricing_models.json"
    if not path.exists():
        return {"version": 1, "providers": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _provider_cost_from_usage(
    pricing: Dict[str, Any],
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> Optional[float]:
    providers = pricing.get("providers", {})
    provider_cfg = providers.get(provider, {}) if isinstance(providers, dict) else {}
    models = provider_cfg.get("models", {}) if isinstance(provider_cfg, dict) else {}
    model_cfg = models.get(model, {}) if isinstance(models, dict) else {}

    in_rate = model_cfg.get("input_per_million_usd")
    out_rate = model_cfg.get("output_per_million_usd")
    if in_rate is None or out_rate is None:
        return None

    in_cost = (float(input_tokens) / 1_000_000.0) * float(in_rate)
    out_cost = (float(output_tokens) / 1_000_000.0) * float(out_rate)
    return round(in_cost + out_cost, 6)


def _task_keywords(task: str) -> List[str]:
    lowered = task.lower()
    words = re.findall(r"[a-z0-9]{4,}", lowered)
    stop = {
        "after",
        "before",
        "please",
        "check",
        "that",
        "this",
        "with",
        "from",
        "into",
        "over",
        "under",
        "about",
        "while",
        "where",
        "when",
        "what",
        "show",
        "need",
        "help",
    }
    seen = set()
    out: List[str] = []
    for w in words:
        if w in stop:
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out[:8]


def _collect_evidence(context_rows: List[Dict[str, Any]], task: str) -> List[Dict[str, Any]]:
    keywords = _task_keywords(task)
    evidence: List[Dict[str, Any]] = []

    for row in context_rows:
        if row.get("status") != "loaded":
            continue
        source_id = str(row.get("id", ""))
        source_path = Path(str(row.get("path", "")))
        if not source_id or not source_path.exists():
            continue

        text = source_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()

        matches = 0
        for idx, line in enumerate(lines, start=1):
            line_l = line.lower()
            hit_terms = [k for k in keywords if k in line_l]
            if not hit_terms:
                continue
            matches += 1
            evidence.append(
                {
                    "source_id": source_id,
                    "path": str(source_path),
                    "line": idx,
                    "snippet": line.strip()[:220],
                    "hit_terms": hit_terms,
                    "match_count": len(hit_terms),
                }
            )
            if matches >= 3:
                break

    if evidence:
        evidence.sort(key=lambda e: (e["match_count"], -int(e["line"])), reverse=True)
        return evidence[:6]

    # Fallback: still provide traceable evidence from trusted sources when no direct term match exists.
    for row in context_rows:
        if row.get("status") != "loaded":
            continue
        source_id = str(row.get("id", ""))
        source_path = str(row.get("path", ""))
        preview = str(row.get("preview") or "").splitlines()
        if not source_id:
            continue
        snippet = preview[0].strip()[:220] if preview else "source loaded but no preview lines available"
        evidence.append(
            {
                "source_id": source_id,
                "path": source_path,
                "line": 1,
                "snippet": snippet,
                "hit_terms": [],
                "match_count": 0,
            }
        )
        if len(evidence) >= 3:
            break

    return evidence


def _build_hypotheses(
    task: str,
    action_id: str,
    lane_service: str,
    evidence: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    citations = [e["source_id"] for e in evidence if e.get("source_id")]
    unique_citations = []
    seen = set()
    for src in citations:
        if src in seen:
            continue
        seen.add(src)
        unique_citations.append(src)

    detail = "lane-matched" if action_id else "open-ended"
    service_hint = f" for service '{lane_service}'" if lane_service else ""

    h1 = {
        "rank": 1,
        "confidence": 0.72 if action_id else 0.58,
        "claim": (
            f"{detail} inspect indicates the request can be triaged read-only"
            f"{service_hint} using trusted context evidence."
        ),
        "sources": unique_citations[:3],
    }

    h2 = {
        "rank": 2,
        "confidence": 0.63 if action_id else 0.51,
        "claim": (
            "Context evidence supports running propose mode next if you want a "
            "bounded, executable plan; inspect output remains non-mutating."
        ),
        "sources": unique_citations[:3],
    }

    return [h1, h2]


def _traceability_metrics(
    hypotheses: List[Dict[str, Any]], evidence: List[Dict[str, Any]]
) -> Dict[str, Any]:
    evidence_ids = {
        str(item.get("source_id", "")).strip()
        for item in evidence
        if str(item.get("source_id", "")).strip()
    }
    total = len(hypotheses)
    traceable = 0
    uncited = 0

    for hyp in hypotheses:
        sources = [str(s).strip() for s in hyp.get("sources", []) if str(s).strip()]
        if not sources:
            uncited += 1
            continue
        if all(src in evidence_ids for src in sources):
            traceable += 1

    ratio = (traceable / total) if total else 0.0
    return {
        "hypotheses_total": total,
        "hypotheses_traceable": traceable,
        "hypotheses_uncited": uncited,
        "traceability_ratio": ratio,
    }


def _write_inspect_artifact(root: Path, report: Dict[str, Any]) -> Path:
    out_dir = root / "artifacts" / "inspect"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"inspect_{report['stamp']}.json"
    latest = out_dir / "inspect_latest.json"
    payload = dict(report)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    latest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _task_slug(task: str) -> str:
    words = re.findall(r"[a-z0-9]+", task.lower())
    keep = [w for w in words if len(w) >= 3][:6]
    if not keep:
        return "candidate"
    return "_".join(keep)


def _load_lane_schema(root: Path) -> Dict[str, Any]:
    path = root / "config" / "schemas" / "lane_spec.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_stage5_draft_policy(root: Path) -> Dict[str, Any]:
    path = root / "config" / "guardrails.json"
    if not path.exists():
        return {
            "primary_strategy": "evidence_driven",
            "fallback_strategy": "template_first",
            "advisory_only_strategies": ["nearest_lane"],
            "min_recommend_score": 60.0,
            "require_schema_valid": True,
            "require_non_placeholder_service": True,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    cfg = dict(payload.get("stage5_drafts", {})) if isinstance(payload, dict) else {}
    return {
        "primary_strategy": str(cfg.get("primary_strategy", "evidence_driven")),
        "fallback_strategy": str(cfg.get("fallback_strategy", "template_first")),
        "advisory_only_strategies": list(cfg.get("advisory_only_strategies", ["nearest_lane"])),
        "min_recommend_score": float(cfg.get("min_recommend_score", 60.0)),
        "require_schema_valid": bool(cfg.get("require_schema_valid", True)),
        "require_non_placeholder_service": bool(cfg.get("require_non_placeholder_service", True)),
    }


def _load_all_lane_specs(lanes_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(lanes_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows.append(payload)
    return rows


def _normalize_service_name(value: str) -> str:
    token = re.sub(r"[^a-z0-9_-]+", "_", str(value).strip().lower())
    token = re.sub(r"_+", "_", token).strip("_")
    return token


def _service_alias_map(lane_specs: List[Dict[str, Any]]) -> Dict[str, str]:
    aliases: Dict[str, str] = {
        "postgresql": "postgres",
        "postgres": "postgres",
        "jellyfin": "jellyfin",
        "nginx": "nginx",
        "redis": "redis",
        "kafka": "kafka",
        "ldap": "ldap",
        "vpn": "vpn",
        "docker": "docker",
        "backup": "backup",
        "filesystem": "filesystem",
        "home automation": "homeassistant",
        "minecraft": "minecraft",
        "mariadb": "mysql",
        "mongodb": "mongo",
        "home_assistant": "homeassistant",
        "homeassistant": "homeassistant",
        "sab": "sabnzbd",
        "sabnzbd": "sabnzbd",
        "openvpn": "vpn",
        "wireguard": "vpn",
    }
    for row in lane_specs:
        svc = _normalize_service_name(str(row.get("service", "")))
        if svc:
            aliases[svc] = svc
    return aliases


def _guess_service(task: str, evidence: List[Dict[str, Any]], lane_specs: List[Dict[str, Any]]) -> str:
    alias_map = _service_alias_map(lane_specs)
    strong_hits: Counter[str] = Counter()
    weak_hits: Counter[str] = Counter()

    # Strong signal: explicit whole-word service mention in task text.
    lowered_task = task.lower()
    for alias, canonical in alias_map.items():
        if not alias:
            continue
        pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
        if re.search(pattern, lowered_task):
            strong_hits[canonical] += 3

    # Secondary signal: tokens and URLs from evidence lines.
    text_parts: List[str] = []
    for ev in evidence:
        text_parts.append(str(ev.get("source_id", "")))
        text_parts.append(str(ev.get("path", "")))
        text_parts.append(str(ev.get("snippet", "")))
    text_blob = "\n".join(text_parts).lower()

    for alias, canonical in alias_map.items():
        if not alias:
            continue
        pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
        if re.search(pattern, text_blob):
            weak_hits[canonical] += 1

    merged: Counter[str] = Counter()
    for k, v in strong_hits.items():
        merged[k] += v
    for k, v in weak_hits.items():
        merged[k] += v

    if not merged:
        return ""

    best = merged.most_common(1)[0][0]
    return _normalize_service_name(best)


def _service_defaults(service: str, lane_specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    for row in lane_specs:
        if str(row.get("service", "")).strip().lower() == service:
            return {
                "endpoint": str(row.get("endpoint", "http://127.0.0.1:8080")),
                "probe": list(row.get("verify_probes", []))[:1],
            }
    return {
        "endpoint": "http://127.0.0.1:8080",
        "probe": [
            {
                "name": "service_identity_marker",
                "command": "curl -sS -m 5 http://127.0.0.1:8080 | head -c 12000",
                "expected_substring": "Service",
                "required": True,
            }
        ],
    }


def _best_lane_match(task: str, lane_specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    task_words = set(_task_keywords(task))
    generic_words = {
        "safe",
        "recovery",
        "issue",
        "issues",
        "startup",
        "investigate",
        "unknown",
        "propose",
    }
    task_words_specific = {w for w in task_words if w not in generic_words}
    lowered_task = task.lower()
    best_score = -1
    best: Dict[str, Any] = {}
    best_service_hit = False
    best_specific_overlap = 0

    for row in lane_specs:
        action_words = set(re.findall(r"[a-z0-9]+", str(row.get("action_id", "")).lower()))
        service = str(row.get("service", "")).strip().lower()
        lane = str(row.get("lane", "")).strip().lower()
        overlap_specific = len(task_words_specific.intersection(action_words))
        overlap_generic = len(task_words.intersection(action_words))
        service_hit = bool(service and service in lowered_task)
        service_bonus = 2 if service_hit else 0
        lane_bonus = 1 if lane and lane in lowered_task else 0
        overlap = overlap_specific * 2 + overlap_generic
        score = overlap + service_bonus + lane_bonus
        if score > best_score:
            best_score = score
            best = row
            best_service_hit = service_hit
            best_specific_overlap = overlap_specific

    if best_score <= 0:
        return {}
    if not best_service_hit and best_specific_overlap <= 0:
        return {}
    return best


def _write_lane_draft_artifact(
    root: Path,
    inspect_stamp: str,
    strategy: str,
    artifact: Dict[str, Any],
) -> Path:
    out_dir = root / "artifacts" / "lane_drafts"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"lane_draft_{inspect_stamp}_{strategy}.json"
    latest = out_dir / f"lane_draft_latest_{strategy}.json"
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    latest.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path


def _generate_lane_drafts(
    root: Path,
    task: str,
    evidence: List[Dict[str, Any]],
    lanes_dir: Path,
    inspect_stamp: str,
) -> List[Dict[str, Any]]:
    policy = _load_stage5_draft_policy(root)
    schema = _load_lane_schema(root)
    lane_specs = _load_all_lane_specs(lanes_dir)
    template_path = lanes_dir / "_template_lane.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    service_guess = _guess_service(task, evidence, lane_specs)
    service_guess = _normalize_service_name(service_guess)
    defaults = _service_defaults(service_guess, lane_specs)
    slug = _task_slug(task)

    # Strategy 1: template-first scaffold.
    s1 = dict(template)
    s1.update(
        {
            "action_id": f"{service_guess or 'candidate'}_{slug}_draft_v1",
            "lane": "media_incident",
            "lane_class": "http_service_recovery",
            "description": f"Candidate lane draft generated from open-ended inspect for task: {task}",
            "service": service_guess or "candidate_service",
            "endpoint": str(defaults.get("endpoint", "http://127.0.0.1:8080")),
            "verify_probes": list(defaults.get("probe", [])),
        }
    )

    # Strategy 2: evidence-driven probe and endpoint hints.
    found_url = ""
    for ev in evidence:
        text = f"{ev.get('snippet','')} {ev.get('path','')}"
        m = re.search(r"https?://[A-Za-z0-9\.:/_-]+", text)
        if m:
            found_url = m.group(0)
            break
    probe_text = service_guess.title() if service_guess else "Service"
    s2 = dict(template)
    s2.update(
        {
            "action_id": f"{service_guess or 'candidate'}_{slug}_evidence_v1",
            "lane": "media_incident",
            "lane_class": "http_service_recovery",
            "description": "Evidence-driven candidate lane draft from open-ended inspect evidence.",
            "service": service_guess or "candidate_service",
            "endpoint": found_url or str(defaults.get("endpoint", "http://127.0.0.1:8080")),
            "verify_probes": [
                {
                    "name": "evidence_identity_probe",
                    "command": f"curl -sS -m 5 {(found_url or defaults.get('endpoint', 'http://127.0.0.1:8080'))} | head -c 12000",
                    "expected_substring": probe_text,
                    "required": True,
                }
            ],
        }
    )

    # Strategy 3: nearest-lane adaptation.
    nearest = _best_lane_match(task, lane_specs)
    s3 = dict(nearest) if nearest else dict(template)
    base_service = service_guess or str(s3.get("service", "candidate_service"))
    s3.update(
        {
            "action_id": f"{base_service}_{slug}_adapt_v1",
            "lane_class": "http_service_recovery",
            "description": f"Nearest-lane adaptation draft based on task: {task}",
            "service": base_service,
        }
    )

    rows = [
        ("template_first", s1),
        ("evidence_driven", s2),
        ("nearest_lane", s3),
    ]

    out: List[Dict[str, Any]] = []
    for strategy, candidate in rows:
        errors = validate_data_against_schema(candidate, schema)
        score = 0.0
        if len(errors) == 0:
            score += 50.0

        service = str(candidate.get("service", "")).strip().lower()
        endpoint = str(candidate.get("endpoint", "")).strip().lower()
        action_id = str(candidate.get("action_id", "")).strip().lower()
        verify_probes = list(candidate.get("verify_probes", []))

        if service and service not in {"candidate_service", "myservice"}:
            score += 20.0
        if service in {"candidate_service", "myservice"}:
            score -= 10.0
        if endpoint and endpoint not in {"http://127.0.0.1:8080"}:
            score += 10.0
        if endpoint in {"http://127.0.0.1:8080"}:
            score -= 5.0
        if verify_probes:
            first_probe = verify_probes[0]
            expected = str(first_probe.get("expected_substring", "")).strip().lower()
            if expected and expected not in {"service"}:
                score += 8.0
        if action_id and "candidate_" not in action_id and "myservice_" not in action_id:
            score += 5.0
        if action_id.startswith("candidate_") or action_id.startswith("myservice_"):
            score -= 3.0
        if strategy == "template_first":
            score += 2.0
        if strategy == "nearest_lane" and service in {"myservice", "candidate_service"}:
            score -= 8.0

        artifact = {
            "version": 1,
            "generated_at": _utc_now_iso(),
            "inspect_stamp": inspect_stamp,
            "strategy": strategy,
            "task": task,
            "schema_valid": len(errors) == 0,
            "schema_errors": errors,
            "candidate_lane_spec": candidate,
        }
        path = _write_lane_draft_artifact(root, inspect_stamp, strategy, artifact)
        out.append(
            {
                "strategy": strategy,
                "score": round(score, 2),
                "schema_valid": len(errors) == 0,
                "schema_errors": errors,
                "artifact_path": str(path),
                "action_id": str(candidate.get("action_id", "")),
                "service": str(candidate.get("service", "")),
                "endpoint": str(candidate.get("endpoint", "")),
                "recommended": False,
            }
        )

    def _eligibility_errors(row: Dict[str, Any]) -> List[str]:
        errs: List[str] = []
        if bool(policy.get("require_schema_valid", True)) and not bool(row.get("schema_valid", False)):
            errs.append("schema_invalid")
        score = float(row.get("score", 0.0) or 0.0)
        if score < float(policy.get("min_recommend_score", 60.0)):
            errs.append("score_below_min")
        if bool(policy.get("require_non_placeholder_service", True)):
            svc = str(row.get("service", "")).strip().lower()
            if svc in {"", "candidate_service", "myservice"}:
                errs.append("placeholder_service")
        return errs

    for row in out:
        row["recommendation_blockers"] = _eligibility_errors(row)
        row["recommendation_eligible"] = len(row["recommendation_blockers"]) == 0

    by_strategy = {str(row.get("strategy", "")): row for row in out}
    primary = str(policy.get("primary_strategy", "evidence_driven"))
    fallback = str(policy.get("fallback_strategy", "template_first"))
    selected: Optional[Dict[str, Any]] = None
    if primary in by_strategy and bool(by_strategy[primary].get("recommendation_eligible", False)):
        selected = by_strategy[primary]
    elif fallback in by_strategy and bool(by_strategy[fallback].get("recommendation_eligible", False)):
        selected = by_strategy[fallback]

    if selected is not None:
        selected["recommended"] = True

    out.sort(key=lambda row: (float(row.get("score", 0.0)), bool(row.get("schema_valid", False))), reverse=True)

    return out


def generate_inspect_report(
    root: Path,
    task: str,
    action_id: str,
    lanes_dir: Path,
    context_rows: List[Dict[str, Any]],
    usage_hint: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Path]:
    lane_service = ""
    lane_endpoint = ""
    if action_id:
        try:
            lane = load_lane_spec(action_id, lanes_dir)
            lane_service = str(lane.get("service", ""))
            lane_endpoint = str(lane.get("endpoint", ""))
        except FileNotFoundError:
            lane_service = ""
            lane_endpoint = ""

    evidence = _collect_evidence(context_rows, task)
    hypotheses = _build_hypotheses(task, action_id, lane_service, evidence)
    traceability = _traceability_metrics(hypotheses, evidence)

    content_for_budget = "\n".join(
        [task]
        + [h["claim"] for h in hypotheses]
        + [e.get("snippet", "") for e in evidence]
    )
    token_count = _token_estimate(content_for_budget)

    provider = str((usage_hint or {}).get("provider", "")).strip().lower()
    model = str((usage_hint or {}).get("model", "")).strip()
    input_tokens = int((usage_hint or {}).get("input_tokens", 0) or 0)
    output_tokens = int((usage_hint or {}).get("output_tokens", 0) or 0)

    pricing = _load_pricing_config(root)
    provider_cost = None
    if provider and model and input_tokens > 0:
        provider_cost = _provider_cost_from_usage(
            pricing,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    if provider_cost is not None:
        estimated_cost_usd = provider_cost
        token_for_budget = input_tokens + output_tokens
        token_for_budget = token_for_budget if token_for_budget > 0 else token_count
        cost_source = "provider_usage"
    else:
        estimated_cost_usd = _cost_estimate_usd(token_count)
        token_for_budget = token_count
        cost_source = "heuristic"

    report: Dict[str, Any] = {
        "inspect_id": f"inspect_{_utc_stamp().lower()}",
        "generated_at": _utc_now_iso(),
        "stamp": _utc_stamp(),
        "mode": "inspect",
        "branch": "lane_matched" if action_id else "open_ended",
        "task": task,
        "lane_match": action_id,
        "lane_service": lane_service,
        "lane_endpoint": lane_endpoint,
        "read_only": True,
        "token_estimate": token_for_budget,
        "estimated_cost_usd": estimated_cost_usd,
        "cost_source": cost_source,
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "traceability_ratio": traceability["traceability_ratio"],
        "hypotheses_total": traceability["hypotheses_total"],
        "hypotheses_traceable": traceability["hypotheses_traceable"],
        "hypotheses_uncited": traceability["hypotheses_uncited"],
        "hypotheses": hypotheses,
        "evidence": evidence,
    }

    if not action_id:
        report["lane_drafts"] = _generate_lane_drafts(
            root=root,
            task=task,
            evidence=evidence,
            lanes_dir=lanes_dir,
            inspect_stamp=str(report["stamp"]),
        )
    else:
        report["lane_drafts"] = []

    artifact_path = _write_inspect_artifact(root, report)
    return report, artifact_path
