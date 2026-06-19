"""Validation placeholders for normalized records."""

from __future__ import annotations


def looks_garbled(text: str) -> bool:
    if not text:
        return False
    return "\ufffd" in text or "�" in text


def validate_records(records: list[object]) -> list[dict]:
    issues: list[dict] = []
    for index, record in enumerate(records):
        name = getattr(record, "name", "")
        if looks_garbled(str(name)):
            issues.append({"index": index, "type": "garbled_name", "name": name})
    return issues
