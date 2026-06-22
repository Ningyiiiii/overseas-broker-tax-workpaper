"""FX helpers for US-stock HKD conversion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_USD_HKD_RATES: dict[str, dict[str, str]] = {
    "2021-12-31": {"date": "2021-12-31", "rate": "7.798068"},
    "2022-03-31": {"date": "2022-03-31", "rate": "7.827524"},
    "2022-12-30": {"date": "2022-12-30", "rate": "7.796747"},
    "2023-03-31": {"date": "2023-03-31", "rate": "7.849693"},
    "2023-12-29": {"date": "2023-12-29", "rate": "7.815652"},
    "2024-03-29": {"date": "2024-03-29", "rate": "7.826375"},
    "2024-12-31": {"date": "2024-12-31", "rate": "7.762516"},
    "2025-03-31": {"date": "2025-03-31", "rate": "7.778464"},
    "2025-12-31": {"date": "2025-12-31", "rate": "7.781936"},
    "2026-03-31": {"date": "2026-03-31", "rate": "7.836684"},
}


def load_fx_table(path: Path | str | None = None) -> dict[str, dict[str, str]]:
    table = dict(DEFAULT_USD_HKD_RATES)
    if not path:
        return table
    source = Path(path)
    if not source.exists():
        return table
    data: Any = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        raw_rates = data.get("USD/HKD") if "USD/HKD" in data else data
        if isinstance(raw_rates, dict):
            for key, value in raw_rates.items():
                if isinstance(value, dict) and value.get("rate"):
                    table[str(key)] = {"date": str(value.get("date", key)), "rate": str(value["rate"])}
                elif value:
                    table[str(key)] = {"date": str(key), "rate": str(value)}
    return table


def get_period_end_fx(pair: str, period_end_date: str, fx_table_path: Path | str | None = None) -> dict[str, str]:
    """Return official FX rate metadata for a period end date.

    If the exact date is unavailable, use the previous available official quote.
    """

    pair_norm = pair.upper().replace("_", "/")
    if pair_norm != "USD/HKD":
        return {"pair": pair_norm, "date": "", "rate": "", "exception": f"unsupported FX pair: {pair}"}
    table = load_fx_table(fx_table_path)
    target = str(period_end_date)
    if target in table:
        return {"pair": "USD/HKD", **table[target], "exception": ""}
    available = sorted(day for day in table if day <= target)
    if not available:
        return {"pair": "USD/HKD", "date": "", "rate": "", "exception": f"missing FX rate for {target}"}
    used = available[-1]
    return {"pair": "USD/HKD", **table[used], "exception": f"used previous official quote for {target}"}
