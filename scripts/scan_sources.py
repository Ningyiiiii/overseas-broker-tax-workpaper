"""Source discovery helpers."""

from __future__ import annotations

from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv"}
TEMPLATE_FILENAMES = {"富途总结表.xlsx"}


def scan_sources(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
        and path.name not in TEMPLATE_FILENAMES
    ]
