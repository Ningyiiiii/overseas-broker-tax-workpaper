"""Period regime helpers."""

from __future__ import annotations

from datetime import date


def china_calendar_year_key(value: date) -> str:
    return f"CY{value.year}"


def hong_kong_fiscal_year_key(value: date) -> str:
    if value.month >= 4:
        return f"FY{value.year}-{value.year + 1}"
    return f"FY{value.year - 1}-{value.year}"
