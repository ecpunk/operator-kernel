import json
import os
import subprocess
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request

_HARD_MAX_TIMEOUT_SECONDS = 2
_HARD_MAX_ATTEMPTS = 1
_HARD_MAX_EVENT_SECONDS = 4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return default


def _default_notify_config() -> Dict[str, Any]:
    return {
        "version": 1,
        "enabled": False,
        "defaults": {
            "timeout_seconds": 2,
            "max_attempts": 1,
            "backoff_seconds": 0,
            "max_event_seconds": 3,
            "emit_milestones": False,
            "sender": "operator-kernel",
        },
        "routes": [],
    }


def _resolve_route_url(route: Dict[str, Any]) -> str:
    """Resolve a route's target URL.

    Supports three forms so real webhook URLs never need to be committed:
    - `url_env`: name of an environment variable holding the URL (preferred).
    - `url` starting with `env:NAME`: equivalent inline shorthand.
    - `url`: a literal URL (fine for non-secret internal targets).
    """
    env_key = str(route.get("url_env", "")).strip()
    if env_key:
        return str(os.environ.get(env_key, "")).strip()

    raw = str(route.get("url", "")).strip()
    if raw.startswith("env:"):
        return str(os.environ.get(raw[len("env:"):].strip(), "")).strip()
    return raw


def _route_matches_event(route: Dict[str, Any], event: Dict[str, Any]) -> bool:
    source = str(event.get("source", "")).strip()
    event_name = str(event.get("event", "")).strip()
    status = str(event.get("status", "")).strip()
    milestone = str(event.get("milestone", "")).strip()

    include_sources = route.get("include_sources", [])
    if isinstance(include_sources, list) and include_sources:
        allowed = {str(v).strip() for v in include_sources if str(v).strip()}
        if source not in allowed:
            return False

    include_events = route.get("include_events", [])
    if isinstance(include_events, list) and include_events:
        allowed = {str(v).strip() for v in include_events if str(v).strip()}
        if event_name not in allowed:
            return False

    include_statuses = route.get("include_statuses", [])
    if isinstance(include_statuses, list) and include_statuses:
        allowed = {str(v).strip() for v in include_statuses if str(v).strip()}
        if status not in allowed:
            return False

    include_milestones = route.get("include_milestones", [])
    if isinstance(include_milestones, list) and include_milestones:
        allowed = {str(v).strip() for v in include_milestones if str(v).strip()}
        if milestone not in allowed:
            return False

    exclude_run_types = route.get("exclude_run_types", [])
    if isinstance(exclude_run_types, list) and exclude_run_types:
        excluded = {str(v).strip() for v in exclude_run_types if str(v).strip()}
        run_type_val = str(event.get("run_type", "")).strip()
        if run_type_val in excluded:
            return False

    return True


def _build_discord_payload(event: Dict[str, Any], route: Dict[str, Any], sender: str) -> Dict[str, Any]:
    content_prefix = str(route.get("content_prefix", "")).strip()
    title = str(event.get("title", "")).strip()
    if not title:
        title = f"{str(event.get('status', 'info')).upper()} {str(event.get('event', 'event'))}".strip()
    source = str(event.get("source", "unknown")).strip()
    event_name = str(event.get("event", "event")).strip()
    status = str(event.get("status", "info")).strip()
    severity = str(event.get("severity", "info")).strip()
    message = str(event.get("message", "")).strip()
    milestone = str(event.get("milestone", "")).strip()

    fields: List[Dict[str, Any]] = [
        {"name": "Source", "value": source or "unknown", "inline": True},
        {"name": "Event", "value": event_name or "event", "inline": True},
        {"name": "Status", "value": status or "info", "inline": True},
    ]
    if milestone:
        fields.append({"name": "Milestone", "value": milestone, "inline": False})
    if message:
        fields.append({"name": "Message", "value": message[:1024], "inline": False})

    color_by_severity = {
        "critical": 15158332,
        "error": 15158332,
        "warning": 16763904,
        "warn": 16763904,
        "info": 3447003,
        "success": 5763719,
    }
    color = color_by_severity.get(severity.lower(), 3447003)

    return {
        "username": str(route.get("username", sender)).strip() or sender,
        "content": f"{content_prefix} {title}".strip(),
        "embeds": [
            {
                "title": title,
                "description": f"{source}:{event_name}",
                "color": color,
                "fields": fields,
                "timestamp": str(event.get("timestamp", _utc_now_iso())),
            }
        ],
    }


def _post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout_seconds: int,
    max_attempts: int,
    backoff_seconds: int,
) -> str:
    body = json.dumps(payload).encode("utf-8")
    final_headers = {
        "Content-Type": "application/json",
        "User-Agent": "operator-kernel-notify/1.0",
    }
    for key, val in headers.items():
        final_headers[str(key)] = str(val)

    attempts = max(1, int(max_attempts))
    timeout = max(1, int(timeout_seconds))
    backoff = max(0, int(backoff_seconds))
    last_err = ""

    for idx in range(1, attempts + 1):
        try:
            req = request.Request(url, data=body, headers=final_headers, method="POST")
            with request.urlopen(req, timeout=timeout) as resp:
                return f"{resp.status} {resp.reason} (attempt {idx}/{attempts})"
        except urllib.error.HTTPError as exc:
            last_err = f"HTTP {exc.code}: {exc.reason}"
        except Exception as exc:
            last_err = str(exc)
            # In this environment urllib intermittently fails networking while curl succeeds.
            if "Network is unreachable" in last_err or "Temporary failure in name resolution" in last_err:
                curl_status = _post_json_with_curl(
                    url=url,
                    body=body,
                    headers=final_headers,
                    timeout_seconds=timeout,
                )
                if not curl_status.startswith("error:"):
                    return f"{curl_status} (attempt {idx}/{attempts}, via curl)"

        if idx < attempts and backoff > 0:
            time.sleep(backoff)

    return f"error: {last_err} (attempts={attempts})"


def _post_json_with_curl(
    url: str,
    body: bytes,
    headers: Dict[str, str],
    timeout_seconds: int,
) -> str:
    cmd = [
        "curl",
        "-sS",
        "--max-time",
        str(max(1, int(timeout_seconds))),
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "-X",
        "POST",
        url,
    ]
    for key, val in headers.items():
        cmd.extend(["-H", f"{key}: {val}"])
    cmd.extend(["--data-binary", body.decode("utf-8")])

    try:
        proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    except Exception as exc:
        return f"error: curl invocation failed: {exc}"

    http_code = str(proc.stdout or "").strip()
    if proc.returncode != 0:
        return f"error: curl failed rc={proc.returncode}: {(proc.stderr or '').strip()}"
    if not http_code.isdigit():
        return f"error: curl returned invalid status: {http_code!r}"
    code = int(http_code)
    if 200 <= code <= 299:
        return f"{code} OK"
    return f"error: HTTP {code}"


def notify_event(
    root: Path,
    source: str,
    event: str,
    status: str,
    message: str,
    severity: str = "info",
    milestone: str = "",
    details: Optional[Dict[str, Any]] = None,
    run_type: str = "",
) -> Dict[str, Any]:
    if str(os.environ.get("OPERATOR_NOTIFY_SUPPRESS", "")).strip() == "1":
        return {"enabled": False, "sent": 0, "results": [{"status": "skipped", "reason": "suppressed by env"}]}

    cfg_path = root / "config" / "notify.json"
    cfg = _load_json(cfg_path, _default_notify_config())

    if not bool(cfg.get("enabled", False)):
        return {"enabled": False, "sent": 0, "results": []}

    defaults = dict(cfg.get("defaults", {}))
    sender = str(defaults.get("sender", "operator-kernel")).strip() or "operator-kernel"
    emit_milestones = bool(defaults.get("emit_milestones", False))
    max_event_seconds = int(defaults.get("max_event_seconds", 5) or 5)
    max_event_seconds = max(1, min(max_event_seconds, _HARD_MAX_EVENT_SECONDS))

    event_payload: Dict[str, Any] = {
        "timestamp": _utc_now_iso(),
        "source": str(source),
        "event": str(event),
        "status": str(status),
        "severity": str(severity),
        "message": str(message),
        "milestone": str(milestone),
        "details": dict(details or {}),
        "run_type": str(run_type),
    }

    if event_payload["status"] == "milestone" and not emit_milestones:
        return {"enabled": True, "sent": 0, "results": [{"status": "skipped", "reason": "milestones disabled"}]}

    results: List[Dict[str, Any]] = []
    routes = cfg.get("routes", [])
    if not isinstance(routes, list):
        routes = []

    started = time.monotonic()
    for route in routes:
        if not isinstance(route, dict):
            continue
        if not bool(route.get("enabled", False)):
            continue
        if not _route_matches_event(route, event_payload):
            continue

        if (time.monotonic() - started) > float(max_event_seconds):
            results.append({"route": "*", "status": "skipped", "reason": "event timeout budget exceeded"})
            break

        route_name = str(route.get("name", "route")).strip() or "route"
        target = str(route.get("target", "webhook_json")).strip()
        url = _resolve_route_url(route)
        if not url:
            results.append({"route": route_name, "status": "skipped", "reason": "missing url or unset env var"})
            continue

        headers = route.get("headers", {})
        if not isinstance(headers, dict):
            headers = {}

        timeout_seconds = int(route.get("timeout_seconds", defaults.get("timeout_seconds", 2)) or 2)
        max_attempts = int(route.get("max_attempts", defaults.get("max_attempts", 1)) or 1)
        backoff_seconds = int(route.get("backoff_seconds", defaults.get("backoff_seconds", 0)) or 0)

        timeout_seconds = max(1, min(timeout_seconds, _HARD_MAX_TIMEOUT_SECONDS))
        max_attempts = max(1, min(max_attempts, _HARD_MAX_ATTEMPTS))

        if target == "discord_webhook":
            body = _build_discord_payload(event_payload, route, sender)
        else:
            body = {
                "sender": sender,
                "kind": "operator_kernel_notification",
                **event_payload,
            }

        delivery = _post_json(
            url=url,
            payload=body,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
        )
        ok = not delivery.startswith("error:")
        results.append({"route": route_name, "status": "sent" if ok else "error", "delivery": delivery})

    sent = len([row for row in results if row.get("status") == "sent"])
    return {"enabled": True, "sent": sent, "results": results}
