"""Normalized record dataclasses used by parser and engine modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TradeRecord:
    broker: str
    market: str
    currency: str
    code: str
    name: str
    side: str
    trade_date: str
    quantity: float
    price: float
    gross_amount: float
    fee_total: float
    source_file: str
    exchange: str = ""
    account_id: str = ""
    side_source: str = ""
    trade_time: str = ""
    settle_date: str | None = None
    order_id: str | None = None
    trade_id: str | None = None
    cash_change: float | None = None
    fee_detail: dict[str, Any] = field(default_factory=dict)
    source_page: str | int | None = None
    source_row: str | int | None = None
    source_coord: str | None = None
    raw_text: str = ""
    parser_layout: str = ""
    exception: str = ""


@dataclass(frozen=True)
class IncomeRecord:
    broker: str
    market: str
    currency: str
    date: str
    code: str
    name: str
    category: str
    amount: float
    source_file: str
    account_id: str = ""
    settle_date: str | None = None
    tax_withheld: float | None = None
    fee: float | None = None
    description: str = ""
    source_page: str | int | None = None
    source_row: str | int | None = None
    source_coord: str | None = None
    raw_text: str = ""
    exception: str = ""


@dataclass(frozen=True)
class FinancingInterestRecord:
    broker: str
    market: str
    currency: str
    date: str
    amount: float
    source_file: str
    account_id: str = ""
    description: str = ""
    source_page: str | int | None = None
    source_row: str | int | None = None
    raw_text: str = ""
    exception: str = ""


@dataclass(frozen=True)
class ParserExceptionRecord:
    broker: str
    source_file: str
    source_page: str | int | None
    record_type: str
    severity: str
    message: str
    raw_text: str = ""
