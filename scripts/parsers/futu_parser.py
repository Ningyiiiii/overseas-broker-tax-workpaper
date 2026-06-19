"""Futu parser scaffold.

Implement Futu PDF/Excel/CSV extraction here. Keep Futu-specific layout handling
out of the common tax engines.
"""

from __future__ import annotations

from pathlib import Path


class FutuParser:
    broker = "futu"

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in {".pdf", ".xlsx", ".xls", ".csv"}

    def parse(self, path: Path, password_candidates: list[str]) -> dict:
        raise NotImplementedError("Wire existing Futu extraction rules into this parser.")
