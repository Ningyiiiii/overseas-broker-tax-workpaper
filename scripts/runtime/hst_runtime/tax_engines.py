"""Tax calculation engines: FIFO and Period Weighted-Average Cost."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


# ---- Period key functions ----

def china_calendar_year_key(d: str) -> str:
    """'2021-03-15' -> 'CY2021'"""
    return f"CY{d[:4]}"


def hong_kong_fiscal_year_key(d: str) -> str:
    """'2021-03-15' -> 'FY2020-2021'; '2021-05-01' -> 'FY2021-2022'"""
    dt = date.fromisoformat(d)
    if dt.month >= 4:
        y1 = dt.year
        y2 = dt.year + 1
    else:
        y1 = dt.year - 1
        y2 = dt.year
    return f"FY{y1}-{y2}"


def get_period_key(d: str, regime: str) -> str:
    if regime == "china_calendar_year":
        return china_calendar_year_key(d)
    elif regime == "hong_kong_fiscal_year":
        return hong_kong_fiscal_year_key(d)
    return china_calendar_year_key(d)


def get_period_end_date(period_key: str) -> str:
    """'CY2021' -> '2021-12-31'; 'FY2021-2022' -> '2022-03-31'"""
    if period_key.startswith("CY"):
        return f"{period_key[2:]}-12-31"
    elif period_key.startswith("FY"):
        # FY2021-2022 -> 2022-03-31
        y2 = period_key.split("-")[1]
        return f"{y2}-03-31"
    return period_key

def get_period_start_date(period_key: str) -> str:
    """'CY2021' -> '2021-01-01'; 'FY2021-2022' -> '2021-04-01'"""
    if period_key.startswith("CY"):
        return f"{period_key[2:]}-01-01"
    elif period_key.startswith("FY"):
        y1 = period_key[2:6]
        return f"{y1}-04-01"
    return period_key


# ---- Data classes ----

@dataclass
class SellDetail:
    sell_date: str
    code: str
    name: str
    side: str  # "卖出" or "卖出(汇总)"
    sell_quantity: float
    sell_price: float
    sell_amount: float  # gross
    sell_fee_allocated: float
    buy_date: str | None
    buy_price: float | None
    buy_quantity: float | None
    buy_amount_allocated: float | None  # gross portion
    buy_fee_allocated: float | None
    cost_basis: float | None  # buy_amount_allocated + buy_fee_allocated
    transaction_fee: float | None  # buy_fee_allocated + sell_fee_allocated
    pnl: float | None
    remark: str | None = None
    is_split_summary: bool = False
    split_count: int = 0


@dataclass
class FifoResult:
    details: list[SellDetail] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)
    period_total: dict = field(default_factory=dict)  # period_key -> {sell_total, fee_total, pnl_total}


@dataclass
class PwaDetail:
    sell_date: str
    code: str
    name: str
    side: str
    sell_quantity: float
    sell_price: float
    sell_amount: float
    sell_fee: float
    weighted_avg_cost: float | None
    cost_basis: float | None  # weighted_avg_cost * sell_quantity + sell_fee
    pnl: float | None
    remark: str | None = None


@dataclass
class PwaResult:
    details: list[PwaDetail] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)
    period_total: dict = field(default_factory=dict)


# ---- FIFO engine ----

def calculate_fifo(trades: list, period_regime: str, market: str) -> FifoResult:
    """Calculate FIFO matching for a given market and period regime.

    trades: list of TradeRecord (already filtered to market)
    Returns FifoResult with details, exceptions, and period totals.
    """
    result = FifoResult()

    # Filter trades for this market
    market_trades = [t for t in trades if t.market == market and t.side in ("BUY", "SELL")]
    if not market_trades:
        return result

    # Sort by trade_date, then stable source order
    sorted_trades = sorted(enumerate(market_trades), key=lambda x: (x[1].trade_date, x[0]))

    # Build buy lots by code
    buy_lots: dict[str, list[dict]] = {}  # code -> list of {date, price, qty_remaining, gross, fee, original_qty}

    # Track sells by period for totals
    period_sells: dict[str, list] = {}  # period_key -> list of sell details

    for idx, t in sorted_trades:
        period_key = get_period_key(t.trade_date, period_regime)
        if period_key not in period_sells:
            period_sells[period_key] = []

        if t.side == "BUY":
            if t.code not in buy_lots:
                buy_lots[t.code] = []
            buy_lots[t.code].append({
                "date": t.trade_date,
                "price": t.price,
                "qty_remaining": t.quantity,
                "gross": t.gross_amount,
                "fee": t.fee_total,
                "original_qty": t.quantity,
            })
        elif t.side == "SELL":
            code = t.code
            lots = buy_lots.get(code, [])
            sell_qty = t.quantity
            sell_gross = t.gross_amount
            sell_fee = t.fee_total

            # Match against buy lots
            matched_segments = []
            remaining = sell_qty
            has_shortage = False

            while remaining > 0.001 and lots:
                lot = lots[0]
                if lot["qty_remaining"] <= 0.001:
                    lots.pop(0)
                    continue
                seg_qty = min(remaining, lot["qty_remaining"])
                matched_segments.append({
                    "buy_date": lot["date"],
                    "buy_price": lot["price"],
                    "seg_qty": seg_qty,
                    "buy_gross": lot["gross"],
                    "buy_fee": lot["fee"],
                    "buy_original_qty": lot["original_qty"],
                })
                lot["qty_remaining"] -= seg_qty
                remaining -= seg_qty
                if lot["qty_remaining"] <= 0.001:
                    lots.pop(0)

            if remaining > 0.001:
                has_shortage = True
                # Record shortage as exception
                result.exceptions.append({
                    "trade_date": t.trade_date,
                    "code": t.code,
                    "name": t.name,
                    "quantity": remaining,
                    "reason": "卖出时无可用买入批次",
                })

            # Generate detail rows
            if len(matched_segments) > 1:
                # Add split summary row first
                summary = SellDetail(
                    sell_date=t.trade_date, code=t.code, name=t.name,
                    side="卖出(汇总)",
                    sell_quantity=sell_qty, sell_price=t.price,
                    sell_amount=sell_gross, sell_fee_allocated=sell_fee,
                    buy_date=None, buy_price=None, buy_quantity=None,
                    buy_amount_allocated=None, buy_fee_allocated=None,
                    cost_basis=None, transaction_fee=None, pnl=None,
                    remark=f"拆分卖出汇总(共{len(matched_segments)}笔明细)",
                    is_split_summary=True, split_count=len(matched_segments),
                )
                result.details.append(summary)
                period_sells[period_key].append(summary)

            for seg in matched_segments:
                seg_qty = seg["seg_qty"]
                sell_allocated_amount = sell_gross * seg_qty / sell_qty
                sell_allocated_fee = sell_fee * seg_qty / sell_qty
                buy_allocated_amount = seg["buy_gross"] * seg_qty / seg["buy_original_qty"]
                buy_allocated_fee = seg["buy_fee"] * seg_qty / seg["buy_original_qty"]
                cost_basis = buy_allocated_amount + buy_allocated_fee
                transaction_fee = buy_allocated_fee + sell_allocated_fee
                pnl = sell_allocated_amount - buy_allocated_amount - buy_allocated_fee - sell_allocated_fee

                detail = SellDetail(
                    sell_date=t.trade_date, code=t.code, name=t.name,
                    side="卖出",
                    sell_quantity=seg_qty, sell_price=t.price,
                    sell_amount=sell_allocated_amount,
                    sell_fee_allocated=sell_allocated_fee,
                    buy_date=seg["buy_date"], buy_price=seg["buy_price"],
                    buy_quantity=seg_qty,
                    buy_amount_allocated=buy_allocated_amount,
                    buy_fee_allocated=buy_allocated_fee,
                    cost_basis=cost_basis, transaction_fee=transaction_fee,
                    pnl=pnl,
                )
                result.details.append(detail)
                period_sells[period_key].append(detail)

            if has_shortage and not matched_segments:
                # No matching at all - still record the sell with blank cost
                detail = SellDetail(
                    sell_date=t.trade_date, code=t.code, name=t.name,
                    side="卖出",
                    sell_quantity=sell_qty, sell_price=t.price,
                    sell_amount=sell_gross, sell_fee_allocated=sell_fee,
                    buy_date=None, buy_price=None, buy_quantity=None,
                    buy_amount_allocated=None, buy_fee_allocated=None,
                    cost_basis=None, transaction_fee=None, pnl=None,
                    remark="无可用买入批次",
                )
                result.details.append(detail)
                period_sells[period_key].append(detail)

    # Calculate period totals (only from non-summary detail rows)
    for pk, sells in period_sells.items():
        sell_total = sum(s.sell_amount for s in sells if not s.is_split_summary)
        fee_total = sum(s.sell_fee_allocated for s in sells if not s.is_split_summary)
        pnl_total = sum(s.pnl for s in sells if not s.is_split_summary and s.pnl is not None)
        result.period_total[pk] = {
            "sell_total": sell_total,
            "fee_total": fee_total,
            "pnl_total": pnl_total,
        }

    return result


# ---- Period Weighted-Average Cost engine ----

def calculate_period_weighted_average(trades: list, period_regime: str, market: str) -> PwaResult:
    """Calculate period weighted-average cost for a given market and period regime.

    All buys in a period are included in the average even if the buy date is later
    than a sell date inside that same period.
    """
    result = PwaResult()

    market_trades = [t for t in trades if t.market == market and t.side in ("BUY", "SELL")]
    if not market_trades:
        return result

    # Group trades by code + period
    # For each code, track opening position (carried from prior periods)
    code_data: dict[str, dict] = {}  # code -> {periods: {pk: {buys, sells, opening_qty, opening_cost}}}

    for t in sorted(market_trades, key=lambda x: x.trade_date):
        pk = get_period_key(t.trade_date, period_regime)
        if t.code not in code_data:
            code_data[t.code] = {"name": t.name, "periods": {}, "period_order": []}
        if pk not in code_data[t.code]["periods"]:
            code_data[t.code]["periods"][pk] = {"buys": [], "sells": []}
            code_data[t.code]["period_order"].append(pk)

        if t.side == "BUY":
            code_data[t.code]["periods"][pk]["buys"].append(t)
        else:
            code_data[t.code]["periods"][pk]["sells"].append(t)

    period_sells: dict[str, list] = {}

    for code, data in code_data.items():
        name = data["name"]
        opening_qty = 0.0
        opening_cost = 0.0

        for pk in data["period_order"]:
            period = data["periods"][pk]
            buys = period["buys"]
            sells = period["sells"]

            period_buy_qty = sum(b.quantity for b in buys)
            period_buy_gross = sum(b.gross_amount for b in buys)
            period_buy_fees = sum(b.fee_total for b in buys)

            total_qty = opening_qty + period_buy_qty
            if total_qty > 0.001:
                wac = (opening_cost + period_buy_gross + period_buy_fees) / total_qty
            else:
                wac = None

            if pk not in period_sells:
                period_sells[pk] = []

            for s in sells:
                if wac is not None and total_qty > 0:
                    cost_basis = wac * s.quantity + s.fee_total
                    pnl = s.gross_amount - wac * s.quantity - s.fee_total
                else:
                    cost_basis = None
                    pnl = None
                    result.exceptions.append({
                        "trade_date": s.trade_date,
                        "code": s.code,
                        "name": s.name,
                        "quantity": s.quantity,
                        "reason": "卖出时无可用成本基础",
                    })

                detail = PwaDetail(
                    sell_date=s.trade_date, code=code, name=name,
                    side="卖出",
                    sell_quantity=s.quantity, sell_price=s.price,
                    sell_amount=s.gross_amount, sell_fee=s.fee_total,
                    weighted_avg_cost=wac,
                    cost_basis=cost_basis, pnl=pnl,
                )
                result.details.append(detail)
                period_sells[pk].append(detail)

            # Update opening for next period
            # Opening = prior opening + period buys - period sells (at WAC)
            period_sell_qty = sum(s.quantity for s in sells)
            if wac is not None:
                period_sell_cost = wac * period_sell_qty
            else:
                period_sell_cost = 0.0
            opening_qty = opening_qty + period_buy_qty - period_sell_qty
            opening_cost = opening_cost + period_buy_gross + period_buy_fees - period_sell_cost
            if opening_qty < 0.001:
                opening_qty = 0.0
                opening_cost = 0.0

    # Calculate period totals
    for pk, sells in period_sells.items():
        sell_total = sum(s.sell_amount for s in sells)
        fee_total = sum(s.sell_fee for s in sells)
        pnl_total = sum(s.pnl for s in sells if s.pnl is not None)
        result.period_total[pk] = {
            "sell_total": sell_total,
            "fee_total": fee_total,
            "pnl_total": pnl_total,
        }

    return result
