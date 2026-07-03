import argparse
import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_archive_index(root: Path) -> Dict[str, Any]:
    path = root / "artifacts" / "archive" / "archive_index.json"
    if not path.exists():
        return {"version": 1, "updated_at": "", "records": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_iso(ts: str) -> datetime:
    value = datetime.fromisoformat(ts)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _within_days(ts: str, days: int) -> bool:
    if days <= 0:
        return True
    return _parse_iso(ts) >= (_utc_now() - timedelta(days=days))


def _filter_records(records: List[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    if days <= 0:
        return records
    out: List[Dict[str, Any]] = []
    for rec in records:
        archived_at = str(rec.get("archived_at", "")).strip()
        if not archived_at:
            continue
        try:
            if _within_days(archived_at, days):
                out.append(rec)
        except ValueError:
            continue
    return out


def _pending_purge_count(records: List[Dict[str, Any]], archive_retention_days: int) -> int:
    if archive_retention_days <= 0:
        return 0
    cutoff = _utc_now() - timedelta(days=archive_retention_days)
    count = 0
    for rec in records:
        if rec.get("status") != "archived":
            continue
        ts = str(rec.get("archived_at", "")).strip()
        if not ts:
            continue
        try:
            parsed = _parse_iso(ts)
        except ValueError:
            continue
        if parsed < cutoff:
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Only include records archived in the last N days. Use 0 for all.",
    )
    parser.add_argument(
        "--recent",
        type=int,
        default=10,
        help="Number of most recent archive records to print.",
    )
    parser.add_argument(
        "--archive-retention-days",
        type=int,
        default=120,
        help="Retention policy used to estimate records pending purge.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    payload = _load_archive_index(root)
    records = list(payload.get("records", []))
    scoped = _filter_records(records, args.days)

    by_status = Counter(str(r.get("status", "unknown")) for r in scoped)
    by_type = Counter(str(r.get("file_type", "unknown")) for r in scoped)
    by_reason = Counter()
    for rec in scoped:
        for reason in rec.get("reasons", []):
            by_reason[str(reason)] += 1

    scoped_sorted = sorted(
        scoped,
        key=lambda r: str(r.get("archived_at", "")),
        reverse=True,
    )

    pending_purge = _pending_purge_count(scoped, args.archive_retention_days)

    print("Archive Activity Report")
    print(f"- window_days: {args.days}")
    print(f"- total_records: {len(scoped)}")
    print(f"- status: archived={by_status.get('archived', 0)}, purged={by_status.get('purged', 0)}")
    print(f"- file_type: plan={by_type.get('plan', 0)}, proof={by_type.get('proof', 0)}")
    print(f"- reasons: age={by_reason.get('age', 0)}, count={by_reason.get('count', 0)}")
    print(f"- pending_purge_estimate: {pending_purge}")

    if args.recent > 0:
        print("- recent_records:")
        for rec in scoped_sorted[: args.recent]:
            print(
                "  - "
                f"{rec.get('id','')} | {rec.get('file_type','')} | {rec.get('status','')} | "
                f"reasons={','.join(rec.get('reasons', []))} | archived_at={rec.get('archived_at','')}"
            )


if __name__ == "__main__":
    main()
