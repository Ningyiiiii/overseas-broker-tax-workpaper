"""Normalized record dataclasses used by the parser and engine modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeRecord:
    broker: str
    market: str
    currency: str
    code: str
    name: str
    side: str
    trade_date: str
    settle_date: str | None
    order_id: str | None
    trade_id: str | None
    quantity: float
    price: float
    gross_amount: float
    fee_total: float
    source_file: str
    source_page: str | int | None
    source_row: str | int | None
    raw_text: str


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
    tax_withheld: float | None
    fee: float | None
    source_file: str
    source_page: str | int | None
    source_row: str | int | None
    raw_text: str


@dataclass(frozen=True)
class FinancingInterestRecord:
    broker: str
    market: str
    currency: str
    date: str
    amount: float
    source_file: str
    source_page: str | int | None
    source_row: str | int | None
    raw_text: str
