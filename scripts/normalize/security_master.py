"""Security master helpers for name backfill."""

from __future__ import annotations


def build_security_master(records: list[object]) -> dict[tuple[str, str], str]:
    master: dict[tuple[str, str], str] = {}
    for record in records:
        market = getattr(record, "market", "")
        code = getattr(record, "code", "")
        name = getattr(record, "name", "")
        if market and code and name and "�" not in str(name):
            master.setdefault((str(market), str(code)), str(name))
    return master
