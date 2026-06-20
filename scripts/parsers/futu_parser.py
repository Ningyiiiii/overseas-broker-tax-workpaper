"""Futu parser rules and helpers.

Keep Futu-specific layout handling out of the common tax engines. The full PDF
extraction pipeline should use these helpers when converting raw text or table
rows into normalized records.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


HK_EXCHANGES = {"SEHK"}
US_EXCHANGES = {"EDGX", "BATS", "MEMX", "XNAS", "XNYS", "NYSE", "NASDAQ", "AMEX", "ARCA", "IEX"}

FUTU_OLD_ACCOUNT_LAYOUT = "futu_old_account_old_layout"
FUTU_NEW_ACCOUNT_LAYOUT = "futu_new_account_new_layout"
FUTU_NEW_TEXT_ORDER_CODE_FIRST = "new_account_text_order_code_first"
FUTU_NEW_TEXT_ORDER_SUMMARY_FIRST = "new_account_text_order_summary_first"

TRADE_REQUIRED_FIELDS = {
    "exchange",
    "currency",
    "trade_date",
    "trade_time",
    "settle_date",
    "quantity",
    "price",
    "gross_amount",
    "cash_change",
}

NON_TRADE_HINTS = {
    "Account Upgrade",
    "Fund Subscription",
    "Fund Redemption",
    "Money Market Fund",
    "holding",
    "position",
    "valuation",
}


@dataclass(frozen=True)
class MarketClassification:
    market: str
    reason: str


def classify_market(exchange: str, currency: str) -> MarketClassification:
    """Classify market from execution exchange and currency.

    Folder names are not reliable after the Futu account migration because HK
    statement folders can contain US trades.
    """

    exchange_norm = (exchange or "").upper()
    currency_norm = (currency or "").upper()
    if exchange_norm in HK_EXCHANGES and currency_norm == "HKD":
        return MarketClassification("HK", "SEHK+HKD")
    if exchange_norm in US_EXCHANGES and currency_norm == "USD":
        return MarketClassification("US", f"{exchange_norm}+USD")
    return MarketClassification("", f"ambiguous exchange/currency: {exchange_norm}/{currency_norm}")


def is_real_trade_candidate(record: dict) -> bool:
    """Return true only when a raw record has enough execution evidence."""

    if any(hint.lower() in str(record.get("raw_text", "")).lower() for hint in NON_TRADE_HINTS):
        return False
    return all(record.get(field) not in (None, "") for field in TRADE_REQUIRED_FIELDS)


def infer_side(side_text: str, cash_change: float | int | str | None) -> tuple[str, str]:
    """Infer BUY/SELL from side text and cash sign, returning side plus note.

    If side text and cash sign conflict, return an empty side and a diagnostic
    note so the caller can emit a parser exception.
    """

    text = (side_text or "").lower()
    side_from_text = ""
    if any(token in text for token in ["buy", "买", "買", "开仓", "開倉"]):
        side_from_text = "BUY"
    if any(token in text for token in ["sell", "卖", "賣", "平仓", "平倉"]):
        side_from_text = "SELL"

    side_from_cash = ""
    if cash_change not in (None, ""):
        value = float(str(cash_change).replace(",", ""))
        if value < 0:
            side_from_cash = "BUY"
        elif value > 0:
            side_from_cash = "SELL"

    if side_from_text and side_from_cash and side_from_text != side_from_cash:
        return "", f"side conflict: text={side_from_text}, cash={side_from_cash}"
    return side_from_text or side_from_cash, "side inferred"


class FutuParser:
    broker = "futu"

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in {".pdf", ".xlsx", ".xls", ".csv"}

    def parse(self, path: Path, password_candidates: list[str]) -> dict:
        """Parse a Futu source file into normalized records.

        This scaffold intentionally keeps the public command contract stable.
        Implementations should wire the validated Futu extraction pipeline here
        and use the helpers above for layout and market routing.
        """

        raise NotImplementedError("Wire the validated Futu extraction pipeline into this parser.")
