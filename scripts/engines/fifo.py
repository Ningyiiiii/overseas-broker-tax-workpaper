"""FIFO tax engine."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .periods import china_calendar_year_key, hong_kong_fiscal_year_key


def _record_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return dict(record)
    if is_dataclass(record):
        return asdict(record)
    return dict(vars(record))


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value).replace(",", ""))


def _money(value: Decimal | str | int | float) -> Decimal:
    return _decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _parse_date(value: str) -> date:
    return date.fromisoformat(str(value).replace("/", "-")[:10])


def _period_key(trade_date: str, period_regime: str) -> str:
    dt = _parse_date(trade_date)
    if period_regime in {"calendar", "china_calendar", "china_natural_year"}:
        return china_calendar_year_key(dt)
    if period_regime in {"hk_fiscal", "hong_kong_fiscal"}:
        return hong_kong_fiscal_year_key(dt)
    raise ValueError(f"unknown period regime: {period_regime}")


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "B", "买入", "買入"}:
        return "BUY"
    if text in {"SELL", "S", "卖出", "賣出"}:
        return "SELL"
    return text


def _sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("trade_date") or item.get("date") or ""),
        str(item.get("trade_time") or ""),
        str(item.get("source_file") or ""),
    )


def calculate_fifo(records: list[Any], period_regime: str, market: str = "") -> dict[str, Any]:
    """Calculate realized gains with FIFO lots.

    Returns `{"rows_by_period": ..., "exceptions": ...}`. Rows are plain dicts
    so workbook builders can map them into their preferred column names.
    """

    trades = [_record_dict(record) for record in records]
    if market:
        trades = [trade for trade in trades if str(trade.get("market", "")).upper() == market.upper()]
    trades.sort(key=_sort_key)

    lots: dict[tuple[str, str, str], deque[dict[str, Any]]] = defaultdict(deque)
    rows_by_period: dict[str, list[dict[str, Any]]] = defaultdict(list)
    exceptions: list[dict[str, Any]] = []

    for trade in trades:
        side = _side(trade.get("side"))
        key = (str(trade.get("market") or ""), str(trade.get("currency") or ""), str(trade.get("code") or ""))
        quantity = _decimal(trade.get("quantity"))
        gross_amount = _decimal(trade.get("gross_amount", trade.get("amount")))
        fee_total = _decimal(trade.get("fee_total"))

        if side == "BUY":
            lots[key].append(
                {
                    **trade,
                    "original_quantity": quantity,
                    "remaining_quantity": quantity,
                    "remaining_gross_amount": gross_amount,
                    "remaining_fee_total": fee_total,
                }
            )
            continue
        if side != "SELL":
            continue

        period = _period_key(str(trade.get("trade_date") or trade.get("date")), period_regime)
        sell_remaining = quantity
        while sell_remaining > 0:
            if not lots[key]:
                ratio = sell_remaining / quantity if quantity else Decimal("0")
                sell_allocated_amount = gross_amount * ratio
                sell_allocated_fee = fee_total * ratio
                row = {
                    **trade,
                    "period": period,
                    "segment_quantity": sell_remaining,
                    "sell_gross_amount": _money(sell_allocated_amount),
                    "buy_gross_amount": None,
                    "transaction_fee": _money(sell_allocated_fee),
                    "pnl": None,
                    "buy_date": "",
                    "exception": "缺买入成本",
                }
                rows_by_period[period].append(row)
                exceptions.append(row)
                break

            lot = lots[key][0]
            use_quantity = min(sell_remaining, lot["remaining_quantity"])
            sell_ratio = use_quantity / quantity if quantity else Decimal("0")
            buy_ratio = use_quantity / lot["original_quantity"] if lot["original_quantity"] else Decimal("0")
            sell_allocated_amount = gross_amount * sell_ratio
            sell_allocated_fee = fee_total * sell_ratio
            buy_allocated_amount = _decimal(lot.get("gross_amount", lot.get("amount"))) * buy_ratio
            buy_allocated_fee = _decimal(lot.get("fee_total")) * buy_ratio
            pnl = sell_allocated_amount - buy_allocated_amount - buy_allocated_fee - sell_allocated_fee
            rows_by_period[period].append(
                {
                    **trade,
                    "period": period,
                    "segment_quantity": use_quantity,
                    "sell_gross_amount": _money(sell_allocated_amount),
                    "buy_gross_amount": _money(buy_allocated_amount),
                    "transaction_fee": _money(buy_allocated_fee + sell_allocated_fee),
                    "pnl": _money(pnl),
                    "buy_date": lot.get("trade_date") or lot.get("date") or "",
                    "buy_source_file": lot.get("source_file", ""),
                    "exception": "",
                }
            )
            lot["remaining_quantity"] -= use_quantity
            lot["remaining_gross_amount"] -= buy_allocated_amount
            lot["remaining_fee_total"] -= buy_allocated_fee
            sell_remaining -= use_quantity
            if lot["remaining_quantity"] <= Decimal("0.00000001"):
                lots[key].popleft()

    return {"rows_by_period": dict(rows_by_period), "exceptions": exceptions}
