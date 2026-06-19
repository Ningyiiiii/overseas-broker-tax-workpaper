"""Workbook validation scaffold."""

from __future__ import annotations

from pathlib import Path


def validate_workbook(path: Path) -> list[dict]:
    if not path.exists():
        return [{"type": "missing_workbook", "path": str(path)}]
    return []
