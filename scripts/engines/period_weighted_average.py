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
from datetime import date

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

    # Pre-populate state with the opening positions supplied by the caller.
    state: dict[tuple[str, str, str], dict] = {}
    for (mkt, ccy, code, period_key), op in (opening_positions or {}).items():
        key = (mkt, ccy, code)
        st = state.setdefault(key, {
            "open_qty": 0.0,
            "open_cost": 0.0,
            "period_buy_qty": 0.0,
            "period_buy_cost": 0.0,
            "period_key": period_key,
        })
        # Only apply opening if it is for the most recent prior period; for
        # the current calculation we only consume openings whose period is
        # earlier than the first record's period. The runner passes openings
        # for the immediately prior period only.
        st["open_qty"] = op.quantity
        st["open_cost"] = op.cost
        st["period_key"] = period_key

    sorted_records = sorted(
        [r for r in records if r.market == market],
        key=lambda r: (
            (r.trade_date or ""),
            r.source_file or "",
            r.source_page or 0,
            r.source_row or 0,
        ),
    )

    def _state_for(key: tuple[str, str, str]) -> dict:
        if key not in state:
            state[key] = {
                "open_qty": 0.0,
                "open_cost": 0.0,
                "period_buy_qty": 0.0,
                "period_buy_cost": 0.0,
                "period_key": "",
                "first_buy_date": "",
            }
        return state[key]

    for record in sorted_records:
        key = (record.market, record.currency, record.code)
        st = _state_for(key)
        period_key = _period_key_for(period_regime, record.trade_date)
        # Detect period boundary: if this trade is in a different period than
        # the last trade for this key, roll the period buy state into the
        # opening state and reset.
        last_period = st.get("period_key")
        if last_period and last_period != period_key:
            # Move the period-aggregated buy into opening for the new period.
            st["open_qty"] += st["period_buy_qty"]
            st["open_cost"] += st["period_buy_cost"]
            st["period_buy_qty"] = 0.0
            st["period_buy_cost"] = 0.0
            st["first_buy_date"] = ""
        st["period_key"] = period_key
        if record.side == "BUY":
            st["period_buy_qty"] += record.quantity
            st["period_buy_cost"] += abs(record.gross_amount) + abs(record.fee_total)
            if not st.get("first_buy_date"):
                st["first_buy_date"] = record.trade_date
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
            open_qty = st["open_qty"]
            open_cost = st["open_cost"]
            period_buy_qty = st["period_buy_qty"]
            period_buy_cost = st["period_buy_cost"]
            total_qty = open_qty + period_buy_qty
            missing = False
            if total_qty <= 0:
                missing = True
            elif open_qty < 0 or period_buy_qty < 0:
                missing = True
            sell_qty = abs(record.quantity)
            sell_gross = abs(record.gross_amount)
            sell_fee = abs(record.fee_total)
            if missing:
                pnl = None
                unit_cost = None
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
                unit_cost = (open_cost + period_buy_cost) / total_qty
                buy_allocated = unit_cost * sell_qty
                buy_allocated_fee = 0.0
                sell_allocated_amount = sell_gross
                sell_allocated_fee = sell_fee
                tx_fee = sell_fee
                pnl = sell_gross - buy_allocated - sell_fee
            d = parse_date(record.trade_date)
            if d is not None:
                keys = period_keys_for(d)
                cy_key = keys["china_calendar_year"]
                fy_key = keys["hong_kong_fiscal_year"]
            else:
                cy_key = fy_key = ""
            first_buy_date = st.get("first_buy_date", "")
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

    # Carry forward: convert current open state to opening positions for the
    # next period (caller can use this for the next period).
    for key, st in state.items():
        # Find the most recent period key for this key.
        market, ccy, code = key
        period_key = st.get("period_key") or ""
        open_qty = st["open_qty"] + st["period_buy_qty"]
        open_cost = st["open_cost"] + st["period_buy_cost"]
        if open_qty > 0:
            result.opening_positions[(market, ccy, code, period_key)] = _OpenPosition(
                market=market,
                currency=ccy,
                code=code,
                period_key=period_key,
                quantity=open_qty,
                cost=open_cost,
            )
    return result
