"""Period weighted-average cost engine."""

from __future__ import annotations

from collections import defaultdict
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


def _period_start_year(period: str) -> int:
    if period.startswith("CY"):
        return int(period[2:])
    if period.startswith("FY"):
        return int(period[2:6])
    return 0


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "B", "买入", "買入"}:
        return "BUY"
    if text in {"SELL", "S", "卖出", "賣出"}:
        return "SELL"
    return text


def calculate_period_weighted_average(records: list[Any], period_regime: str, market: str = "") -> dict[str, Any]:
    """Calculate realized gains with period weighted-average cost.

    This is not moving average: all buys in a period participate in that
    period's unit cost even if the buy date is later than a sell date.
    """

    trades = [_record_dict(record) for record in records]
    if market:
        trades = [trade for trade in trades if str(trade.get("market", "")).upper() == market.upper()]
    trades.sort(key=lambda item: (str(item.get("trade_date") or item.get("date") or ""), str(item.get("source_file") or "")))

    by_period_symbol: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    periods: set[str] = set()
    symbols: set[tuple[str, str, str]] = set()
    for trade in trades:
        period = _period_key(str(trade.get("trade_date") or trade.get("date")), period_regime)
        symbol = (str(trade.get("market") or ""), str(trade.get("currency") or ""), str(trade.get("code") or ""))
        by_period_symbol[(period, *symbol)].append(trade)
        periods.add(period)
        symbols.add(symbol)

    rows_by_period: dict[str, list[dict[str, Any]]] = defaultdict(list)
    exceptions: list[dict[str, Any]] = []
    carry: dict[tuple[str, str, str], dict[str, Decimal]] = defaultdict(lambda: {"quantity": Decimal("0"), "cost": Decimal("0")})

    for period in sorted(periods, key=_period_start_year):
        for symbol in sorted(symbols):
            period_trades = by_period_symbol.get((period, *symbol), [])
            if not period_trades:
                continue
            opening_quantity = carry[symbol]["quantity"]
            opening_cost = carry[symbol]["cost"]
            buy_quantity = Decimal("0")
            buy_cost = Decimal("0")
            for trade in period_trades:
                if _side(trade.get("side")) == "BUY":
                    buy_quantity += _decimal(trade.get("quantity"))
                    buy_cost += _decimal(trade.get("gross_amount", trade.get("amount"))) + _decimal(trade.get("fee_total"))

            denominator = opening_quantity + buy_quantity
            total_cost = opening_cost + buy_cost
            unit_cost = total_cost / denominator if denominator else None
            period_sell_quantity = Decimal("0")

            for trade in period_trades:
                side = _side(trade.get("side"))
                if side == "BUY":
                    continue
                if side != "SELL":
                    continue
                sell_quantity = _decimal(trade.get("quantity"))
                sell_gross_amount = _decimal(trade.get("gross_amount", trade.get("amount")))
                sell_fee = _decimal(trade.get("fee_total"))
                period_sell_quantity += sell_quantity
                if unit_cost is None or period_sell_quantity > denominator:
                    row = {
                        **trade,
                        "period": period,
                        "segment_quantity": sell_quantity,
                        "sell_gross_amount": _money(sell_gross_amount),
                        "weighted_average_unit_cost": None,
                        "buy_gross_amount": None,
                        "transaction_fee": _money(sell_fee),
                        "pnl": None,
                        "exception": "缺买入成本",
                    }
                    rows_by_period[period].append(row)
                    exceptions.append(row)
                    continue
                deductible_cost = unit_cost * sell_quantity
                pnl = sell_gross_amount - deductible_cost - sell_fee
                rows_by_period[period].append(
                    {
                        **trade,
                        "period": period,
                        "segment_quantity": sell_quantity,
                        "sell_gross_amount": _money(sell_gross_amount),
                        "weighted_average_unit_cost": unit_cost.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
                        "buy_gross_amount": _money(deductible_cost),
                        "transaction_fee": _money(sell_fee),
                        "pnl": _money(pnl),
                        "exception": "",
                    }
                )

            carry[symbol]["quantity"] = denominator - period_sell_quantity
            carry[symbol]["cost"] = unit_cost * carry[symbol]["quantity"] if unit_cost is not None and carry[symbol]["quantity"] > 0 else Decimal("0")

    return {"rows_by_period": dict(rows_by_period), "exceptions": exceptions}
