"""Source discovery helpers."""

from __future__ import annotations

from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv"}
TEMPLATE_NAME_HINTS = {"富途总结表", "富途總結表"}


def scan_sources(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
        and not any(hint in path.name for hint in TEMPLATE_NAME_HINTS)
    ]
