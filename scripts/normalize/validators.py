"""Validation helpers for normalized records."""

from __future__ import annotations

from .security_master import looks_garbled


def validate_records(records: list[object]) -> list[dict]:
    issues: list[dict] = []
    for index, record in enumerate(records):
        name = getattr(record, "name", "")
        if looks_garbled(str(name)):
            issues.append({"index": index, "type": "garbled_name", "name": name})
        # Date sanity
        date_attr = getattr(record, "trade_date", None) or getattr(record, "date", None)
        if not date_attr or len(str(date_attr)) < 8:
            issues.append({"index": index, "type": "invalid_or_missing_date", "date": date_attr})
    return issues
