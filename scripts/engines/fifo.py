"""FIFO tax engine.

Implements FIFO matching per the algorithm spec.

Buy lots are consumed in ascending trade_date, then stable source order.
When a sell crosses multiple buy lots, split into multiple detail rows.

For each matched segment:
  sell_allocated_amount = sell_gross_amount * seg_qty / sell_qty
  sell_allocated_fee    = sell_fee_total    * seg_qty / sell_qty
  buy_allocated_amount  = buy_lot_gross_amount * seg_qty / original_buy_lot_qty
  buy_allocated_fee     = buy_lot_fee_total    * seg_qty / original_buy_lot_qty
  transaction_fee       = buy_allocated_fee + sell_allocated_fee
  pnl                   = sell_allocated_amount - buy_allocated_amount
                                     - buy_allocated_fee - sell_allocated_fee

If there are not enough buy lots, the missing segment stays blank and an
exception is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tax_workpaper.engines.periods import period_keys_for, parse_date
from tax_workpaper.normalize.schema import TradeRecord


@dataclass
class TradeRow:
    code: str
    market: str
    currency: str
    side: str
    trade_date: str
    quantity: float  # signed (negative for sell)
    price: float
    gross_amount: float  # absolute
    fee_total: float  # absolute
    source_file: str
    source_page: int
    raw_text: str
    name: str = ""
    is_summary: bool = False
    is_split_header: bool = False
    is_split_detail: bool = False
    split_index: int = 0
    split_count: int = 1
    missing_cost: bool = False
    period_keys: tuple[str, str] = ("", "")
    sell_allocated_amount: float | None = None
    sell_allocated_fee: float | None = None
    buy_allocated_amount: float | None = None
    buy_allocated_fee: float | None = None
    transaction_fee: float | None = None
    pnl: float | None = None
    buy_trade_date: str | None = None
    buy_source_index: int | None = None
    buy_source_file: str | None = None
    sell_source_index: int | None = None
    pwa_note: str = ""
    source_note: str = ""


@dataclass
class _Lot:
    code: str
    market: str
    currency: str
    trade_date: str
    original_qty: float
    remaining_qty: float
    gross_amount: float
    fee_total: float
    source_index: int
    buy_record_ref: TradeRecord | None = None


def _ensure_period_keys(row: TradeRow) -> None:
    d = parse_date(row.trade_date)
    if d is None:
        return
    keys = period_keys_for(d)
    row.period_keys = (keys["china_calendar_year"], keys["hong_kong_fiscal_year"])


def _new_trade_row_from_record(record: TradeRecord, market: str) -> TradeRow:
    row = TradeRow(
        code=record.code,
        market=market or record.market,
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
    _ensure_period_keys(row)
    return row


@dataclass
class FifoResult:
    rows: list[TradeRow] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)


def calculate_fifo(records: list[TradeRecord], market: str) -> FifoResult:
    """Return matched rows for the given market.

    `records` may contain mixed markets; we filter to the requested market.
    """
    result = FifoResult()
    sorted_records = sorted(
        [r for r in records if r.market == market],
        key=lambda r: (
            (r.trade_date or ""),
            r.source_file or "",
            r.source_page or 0,
            r.source_row or 0,
        ),
    )

    lots_by_key: dict[tuple[str, str, str], list[_Lot]] = {}

    for record in sorted_records:
        row = _new_trade_row_from_record(record, market)
        key = (row.market, row.currency, row.code)
        if row.side == "BUY":
            lot = _Lot(
                code=row.code,
                market=row.market,
                currency=row.currency,
                trade_date=row.trade_date,
                original_qty=row.quantity,
                remaining_qty=row.quantity,
                gross_amount=row.gross_amount,
                fee_total=row.fee_total,
                source_index=len(result.rows),
                buy_record_ref=record,
            )
            lots_by_key.setdefault(key, []).append(lot)
            result.rows.append(row)
        elif row.side == "SELL":
            lots = lots_by_key.get(key, [])
            sell_qty = abs(row.quantity)
            sell_amount = row.gross_amount
            sell_fee = row.fee_total
            remaining = sell_qty
            segments: list[TradeRow] = []
            for lot in lots:
                if remaining <= 0 or lot.remaining_qty <= 0:
                    continue
                seg_qty = min(lot.remaining_qty, remaining)
                seg_sell_amount = sell_amount * seg_qty / sell_qty
                seg_sell_fee = sell_fee * seg_qty / sell_qty
                seg_buy_amount = lot.gross_amount * seg_qty / lot.original_qty
                seg_buy_fee = lot.fee_total * seg_qty / lot.original_qty
                seg_fee = seg_buy_fee + seg_sell_fee
                seg_pnl = seg_sell_amount - seg_buy_amount - seg_buy_fee - seg_sell_fee
                seg_row = TradeRow(
                    code=row.code,
                    market=row.market,
                    currency=row.currency,
                    side=row.side,
                    trade_date=row.trade_date,
                    quantity=-seg_qty,
                    price=row.price,
                    gross_amount=seg_sell_amount,
                    fee_total=seg_sell_fee,
                    source_file=row.source_file,
                    source_page=row.source_page,
                    raw_text=row.raw_text,
                    name=row.name,
                    is_split_detail=True,
                    split_index=0,
                    split_count=0,
                    period_keys=row.period_keys,
                    sell_allocated_amount=seg_sell_amount,
                    sell_allocated_fee=seg_sell_fee,
                    buy_allocated_amount=seg_buy_amount,
                    buy_allocated_fee=seg_buy_fee,
                    transaction_fee=seg_fee,
                    pnl=seg_pnl,
                    buy_trade_date=lot.trade_date,
                    buy_source_index=lot.source_index,
                    buy_source_file=lot.buy_record_ref.source_file if lot.buy_record_ref else None,
                    sell_source_index=len(result.rows),
                    source_note=f"sell:{row.source_file} buy:{lot.buy_record_ref.source_file if lot.buy_record_ref else ''}",
                )
                segments.append(seg_row)
                lot.remaining_qty -= seg_qty
                remaining -= seg_qty
            if remaining > 0:
                # Missing cost basis: append a segment that records the deficit.
                seg_row = TradeRow(
                    code=row.code,
                    market=row.market,
                    currency=row.currency,
                    side=row.side,
                    trade_date=row.trade_date,
                    quantity=-remaining,
                    price=row.price,
                    gross_amount=sell_amount * remaining / sell_qty,
                    fee_total=sell_fee * remaining / sell_qty,
                    source_file=row.source_file,
                    source_page=row.source_page,
                    raw_text=row.raw_text,
                    name=row.name,
                    is_split_detail=True,
                    split_index=0,
                    split_count=0,
                    period_keys=row.period_keys,
                    sell_allocated_amount=sell_amount * remaining / sell_qty,
                    sell_allocated_fee=sell_fee * remaining / sell_qty,
                    buy_allocated_amount=None,
                    buy_allocated_fee=None,
                    transaction_fee=None,
                    pnl=None,
                    buy_trade_date=None,
                    buy_source_index=None,
                    sell_source_index=len(result.rows),
                    missing_cost=True,
                    source_note=row.source_file,
                )
                segments.append(seg_row)
                result.exceptions.append(
                    {
                        "type": "missing_cost",
                        "code": row.code,
                        "market": row.market,
                        "currency": row.currency,
                        "trade_date": row.trade_date,
                        "quantity": remaining,
                        "source_file": row.source_file,
                        "source_page": row.source_page,
                    }
                )
            # If the sell was split, prepend a summary row.
            if len(segments) > 1:
                summary = TradeRow(
                    code=row.code,
                    market=row.market,
                    currency=row.currency,
                    side=row.side,
                    trade_date=row.trade_date,
                    quantity=row.quantity,
                    price=row.price,
                    gross_amount=row.gross_amount,
                    fee_total=row.fee_total,
                    source_file=row.source_file,
                    source_page=row.source_page,
                    raw_text=row.raw_text,
                    name=row.name,
                    is_summary=True,
                    is_split_header=True,
                    split_count=len(segments),
                    period_keys=row.period_keys,
                    sell_source_index=len(result.rows),
                    source_note="订单分拆成交汇总",
                )
                result.rows.append(summary)
                for idx, seg in enumerate(segments, start=1):
                    seg.split_index = idx
                    seg.split_count = len(segments)
                    result.rows.append(seg)
            else:
                # Single segment: keep one detail row.
                if segments:
                    seg = segments[0]
                    seg.split_index = 1
                    seg.split_count = 1
                    result.rows.append(seg)
                else:
                    result.rows.append(row)
        else:
            result.rows.append(row)
    return result
