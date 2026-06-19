"""Base parser contract."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class BrokerParser(Protocol):
    broker: str

    def can_parse(self, path: Path) -> bool:
        ...

    def parse(self, path: Path, password_candidates: list[str]) -> dict:
        ...
