"""Period regime helpers."""

from __future__ import annotations

from datetime import date


def parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def china_calendar_year_key(value: date) -> str:
    return f"CY{value.year}"


def hong_kong_fiscal_year_key(value: date) -> str:
    if value.month >= 4:
        return f"FY{value.year}-{value.year + 1}"
    return f"FY{value.year - 1}-{value.year}"


def period_keys_for(value: date) -> dict[str, str]:
    return {
        "china_calendar_year": china_calendar_year_key(value),
        "hong_kong_fiscal_year": hong_kong_fiscal_year_key(value),
    }


def period_end_date(period_regime: str, period_key: str) -> date:
    """Return the period end date for a given regime and period key."""
    if period_regime == "china_calendar_year":
        year = int(period_key.replace("CY", ""))
        return date(year, 12, 31)
    # Hong Kong fiscal year FYyyyy-yyyy+1
    start_year = int(period_key[2:6])
    return date(start_year + 1, 3, 31)


def prior_period(period_regime: str, period_key: str) -> str:
    if period_regime == "china_calendar_year":
        year = int(period_key.replace("CY", ""))
        return f"CY{year - 1}"
    start_year = int(period_key[2:6])
    return f"FY{start_year - 1}-{start_year}"
