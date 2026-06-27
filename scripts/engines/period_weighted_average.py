"""Period weighted-average cost engine.

Period weighted-average cost is, by (market, currency, code, period):

  weighted_average_unit_cost =
    (opening_position_total_cost + period_buy_gross_amount + period_buy_fees)
    / (opening_quantity + period_buy_quantity)

  sell_deductible_cost =
    weighted_average_unit_cost * sell_quantity + sell_fee_total

  pnl =
    sell_gross_amount - weighted_average_unit_cost * sell_quantity - sell_fee_total

Opening cost comes from prior records or prior period carry-forward. If
opening or period buy cost is missing, affected sells must remain blank and
enter exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tax_workpaper.engines.fifo import TradeRow
from tax_workpaper.engines.periods import period_keys_for, parse_date
from tax_workpaper.normalize.schema import TradeRecord


@dataclass
class _OpenPosition:
    market: str
    currency: str
    code: str
    period_key: str
    quantity: float
    cost: float  # opening total cost = unit_cost * quantity


@dataclass
class PwaResult:
    rows: list[TradeRow] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)
    opening_positions: dict[tuple[str, str, str, str], _OpenPosition] = field(default_factory=dict)


def _period_key_for(period_regime: str, record_date: str) -> str:
    d = parse_date(record_date)
    if d is None:
        return ""
    keys = period_keys_for(d)
    return keys[period_regime]


def calculate_period_weighted_average(
    records: list[TradeRecord],
    market: str,
    period_regime: str,
    opening_positions: dict[tuple[str, str, str, str], _OpenPosition] | None = None,
) -> PwaResult:
    """Calculate period weighted-average P&L for one market and one period regime.

    `opening_positions` is the carry-forward from prior periods, keyed by
    (market, currency, code, period_key). If not provided, we start with
    zero opening positions, which is the right behavior for the first period
    in our scope (2021).
    """
    result = PwaResult()
    result.opening_positions = dict(opening_positions or {})

    sorted_records = sorted(
        [r for r in records if r.market == market],
        key=lambda r: (
            (r.trade_date or ""),
            r.source_file or "",
            r.source_page or 0,
            r.source_row or 0,
        ),
    )

    period_data: dict[tuple[str, str, str], dict[str, dict]] = {}
    for record in sorted_records:
        key = (record.market, record.currency, record.code)
        period_key = _period_key_for(period_regime, record.trade_date)
        data = period_data.setdefault(key, {}).setdefault(period_key, {
            "first_date": record.trade_date,
            "buy_qty": 0.0,
            "buy_cost": 0.0,
            "sell_qty": 0.0,
            "first_buy_date": "",
        })
        if record.trade_date < data["first_date"]:
            data["first_date"] = record.trade_date
        if record.side == "BUY":
            data["buy_qty"] += record.quantity
            data["buy_cost"] += abs(record.gross_amount) + abs(record.fee_total)
            if not data["first_buy_date"] or record.trade_date < data["first_buy_date"]:
                data["first_buy_date"] = record.trade_date
        elif record.side == "SELL":
            data["sell_qty"] += abs(record.quantity)

    opening_by_key: dict[tuple[str, str, str], tuple[float, float]] = {}
    for (mkt, ccy, code, _period_key), op in (opening_positions or {}).items():
        key = (mkt, ccy, code)
        qty, cost = opening_by_key.get(key, (0.0, 0.0))
        opening_by_key[key] = (qty + op.quantity, cost + op.cost)

    pool_info: dict[tuple[str, str, str, str], dict] = {}
    for key, periods in period_data.items():
        open_qty, open_cost = opening_by_key.get(key, (0.0, 0.0))
        for period_key, data in sorted(periods.items(), key=lambda item: item[1]["first_date"]):
            total_qty = open_qty + data["buy_qty"]
            total_cost = open_cost + data["buy_cost"]
            unit_cost = total_cost / total_qty if total_qty > 0 else None
            pool_info[(*key, period_key)] = {
                "open_qty": open_qty,
                "open_cost": open_cost,
                "period_buy_qty": data["buy_qty"],
                "period_buy_cost": data["buy_cost"],
                "first_buy_date": data["first_buy_date"],
                "unit_cost": unit_cost,
                "remaining_qty": total_qty,
            }
            if unit_cost is None:
                open_qty = data["buy_qty"] - data["sell_qty"]
                open_cost = data["buy_cost"]
            else:
                open_qty = max(total_qty - data["sell_qty"], 0.0)
                open_cost = unit_cost * open_qty

    for record in sorted_records:
        key = (record.market, record.currency, record.code)
        period_key = _period_key_for(period_regime, record.trade_date)
        pool = pool_info.get((*key, period_key), {
            "open_qty": 0.0,
            "open_cost": 0.0,
            "period_buy_qty": 0.0,
            "period_buy_cost": 0.0,
            "first_buy_date": "",
            "unit_cost": None,
            "remaining_qty": 0.0,
        })
        if record.side == "BUY":
            d = parse_date(record.trade_date)
            keys = period_keys_for(d) if d else {"china_calendar_year": "", "hong_kong_fiscal_year": ""}
            result.rows.append(
                TradeRow(
                    code=record.code,
                    market=record.market,
                    currency=record.currency,
                    side="BUY",
                    trade_date=record.trade_date,
                    quantity=record.quantity,
                    price=record.price,
                    gross_amount=abs(record.gross_amount),
                    fee_total=abs(record.fee_total),
                    source_file=record.source_file,
                    source_page=record.source_page or 0,
                    raw_text=record.raw_text,
                    name=record.name,
                    period_keys=(
                        keys["china_calendar_year"],
                        keys["hong_kong_fiscal_year"],
                    ),
                )
            )
        elif record.side == "SELL":
            open_qty = pool["open_qty"]
            open_cost = pool["open_cost"]
            period_buy_qty = pool["period_buy_qty"]
            period_buy_cost = pool["period_buy_cost"]
            total_qty = open_qty + period_buy_qty
            sell_qty = abs(record.quantity)
            sell_gross = abs(record.gross_amount)
            sell_fee = abs(record.fee_total)
            unit_cost = pool["unit_cost"]
            missing = unit_cost is None or pool["remaining_qty"] < sell_qty
            if missing:
                pnl = None
                buy_allocated = None
                sell_allocated_amount = sell_gross
                sell_allocated_fee = sell_fee
                buy_allocated_fee = None
                tx_fee = None
                result.exceptions.append(
                    {
                        "type": "missing_opening_or_period_buy",
                        "code": record.code,
                        "market": record.market,
                        "currency": record.currency,
                        "trade_date": record.trade_date,
                        "period": period_key,
                        "quantity": sell_qty,
                        "source_file": record.source_file,
                        "source_page": record.source_page,
                    }
                )
            else:
                buy_allocated = unit_cost * sell_qty
                buy_allocated_fee = 0.0
                sell_allocated_amount = sell_gross
                sell_allocated_fee = sell_fee
                tx_fee = sell_fee
                pnl = sell_gross - buy_allocated - sell_fee
                pool["remaining_qty"] -= sell_qty
            d = parse_date(record.trade_date)
            if d is not None:
                keys = period_keys_for(d)
                cy_key = keys["china_calendar_year"]
                fy_key = keys["hong_kong_fiscal_year"]
            else:
                cy_key = fy_key = ""
            first_buy_date = pool.get("first_buy_date", "")
            if missing:
                pwa_note = "缺买入成本"
            else:
                pwa_note = (
                    f"期间加权平均成本法；{period_key}；"
                    f"期初股数{int(open_qty)}；本期买入股数{int(period_buy_qty)}；"
                    f"持仓起始买入日期{first_buy_date}"
                )
            source_note = f"sell:{record.source_file} period_weighted_average_pool"
            result.rows.append(
                TradeRow(
                    code=record.code,
                    market=record.market,
                    currency=record.currency,
                    side="SELL",
                    trade_date=record.trade_date,
                    quantity=-sell_qty,
                    price=record.price,
                    gross_amount=sell_gross,
                    fee_total=sell_fee,
                    source_file=record.source_file,
                    source_page=record.source_page or 0,
                    raw_text=record.raw_text,
                    name=record.name,
                    period_keys=(cy_key, fy_key),
                    sell_allocated_amount=sell_allocated_amount,
                    sell_allocated_fee=sell_allocated_fee,
                    buy_allocated_amount=buy_allocated,
                    buy_allocated_fee=buy_allocated_fee,
                    transaction_fee=tx_fee,
                    pnl=pnl,
                    missing_cost=missing,
                    pwa_note=pwa_note,
                    source_note=source_note,
                )
            )
        else:
            result.rows.append(
                TradeRow(
                    code=record.code,
                    market=record.market,
                    currency=record.currency,
                    side=record.side,
                    trade_date=record.trade_date,
                    quantity=record.quantity,
                    price=record.price,
                    gross_amount=abs(record.gross_amount),
                    fee_total=abs(record.fee_total),
                    source_file=record.source_file,
                    source_page=record.source_page or 0,
                    raw_text=record.raw_text,
                    name=record.name,
                )
            )

    for key, periods in period_data.items():
        market, ccy, code = key
        latest_period = ""
        latest_first_date = ""
        for period_key, data in periods.items():
            if not latest_first_date or data["first_date"] > latest_first_date:
                latest_first_date = data["first_date"]
                latest_period = period_key
        if not latest_period:
            continue
        pool = pool_info.get((*key, latest_period), {})
        unit_cost = pool.get("unit_cost")
        ending_qty = pool.get("remaining_qty", 0.0)
        if unit_cost is not None and ending_qty > 0:
            result.opening_positions[(market, ccy, code, latest_period)] = _OpenPosition(
                market=market,
                currency=ccy,
                code=code,
                period_key=latest_period,
                quantity=ending_qty,
                cost=unit_cost * ending_qty,
            )
    return result
