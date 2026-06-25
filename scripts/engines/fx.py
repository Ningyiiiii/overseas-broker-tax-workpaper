"""FX helper for US-stock HKD conversion.

Loads PBOC central parity rates from config/fx_sources.json (or the in-memory
default table for this project) and exposes a simple lookup keyed by date.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import json

DEFAULT_USD_HKD: list[tuple[str, float]] = [
    ("2020-12-31", 7.7534),
    ("2021-03-31", 7.7746),
    ("2021-12-31", 7.7983),
    ("2022-03-31", 7.8080),
    ("2022-12-31", 7.8257),
    ("2023-03-31", 7.8495),
    ("2023-12-31", 7.8209),
    ("2024-03-31", 7.8078),
    ("2024-12-31", 7.7689),
]


def _load_default_table() -> list[tuple[str, float]]:
    config_path = Path(__file__).resolve().parents[1] / "config" / "fx_sources.json"
    if not config_path.exists():
        return list(DEFAULT_USD_HKD)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        rows = data.get("USDHKD", [])
        if rows:
            return [(r["date"], float(r["rate"])) for r in rows]
    except Exception:  # noqa: BLE001
        pass
    return list(DEFAULT_USD_HKD)


_TABLE: list[tuple[str, float]] = _load_default_table()


def get_period_end_fx(pair: str, period_end_date: str) -> dict:
    """Return PBOC central parity for the requested pair on the period end date.

    If no official quote exists, use the previous available official quote and
    record the actual FX date used.
    """
    if pair != "USDHKD":
        return {"rate": None, "date": period_end_date, "fallback": False}
    target = date.fromisoformat(period_end_date)
    table = sorted(_TABLE, key=lambda r: r[0])
    chosen: tuple[str, float] | None = None
    for entry_date, rate in table:
        ed = date.fromisoformat(entry_date)
        if ed <= target:
            chosen = (entry_date, rate)
        else:
            break
    if chosen is None:
        return {"rate": None, "date": period_end_date, "fallback": False}
    if chosen[0] != period_end_date:
        return {"rate": chosen[1], "date": chosen[0], "fallback": True}
    return {"rate": chosen[1], "date": chosen[0], "fallback": False}


def format_fx_note(pair: str, fx: dict) -> str:
    if fx.get("rate") is None:
        return f"{pair} missing"
    return f"{pair} {fx['rate']:.4f} @ {fx['date']}{' (fallback)' if fx.get('fallback') else ''}"
