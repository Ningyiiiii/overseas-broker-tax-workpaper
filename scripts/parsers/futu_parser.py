"""Futu parser rules and helpers.

Keep Futu-specific layout handling out of the common tax engines. The full PDF
extraction pipeline should use these helpers when converting raw text or table
rows into normalized records.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from dataclasses import asdict

import pdfplumber

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtime"
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

import futu_workpaper_runtime as runtime


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

        This uses the validated Futu runtime parser bundled with the skill. The
        parser still keeps broker-specific layout handling here and emits plain
        normalized dictionaries for the shared engines.
        """

        password = password_candidates[0] if password_candidates else ""
        runtime.PASSWORD = password
        rel = path.name
        trades: list[object] = []
        income: list[dict] = []
        financing: list[dict] = []
        exceptions: list[dict] = []
        try:
            if "1001231828219038" in path.name:
                trades = runtime.parse_modern_trades(path, rel)
                trades.extend(runtime.parse_modern_ipo_allotments(path, rel))
                income, financing = runtime.parse_modern_cash(path, rel)
            else:
                with pdfplumber.open(path, password=password or None) as pdf:
                    if "1001100520203011" in path.name or "美股" in str(path) or "US" in str(path).upper():
                        trades, income, financing = runtime.parse_old_us(pdf, rel)
                    else:
                        trades, income, financing = runtime.parse_old_hk(pdf, rel)
        except Exception as exc:
            exceptions.append(
                {
                    "broker": self.broker,
                    "source_file": str(path),
                    "source_page": None,
                    "record_type": "source_file",
                    "severity": "error",
                    "message": repr(exc),
                    "raw_text": "",
                }
            )

        trade_records = []
        for trade in trades:
            item = asdict(trade) if hasattr(trade, "__dataclass_fields__") else dict(trade)
            trade_records.append(
                {
                    "broker": self.broker,
                    "market": item.get("market", ""),
                    "currency": item.get("currency", ""),
                    "code": item.get("code", ""),
                    "name": item.get("name", ""),
                    "side": "BUY" if item.get("side") == "buy" else "SELL" if item.get("side") == "sell" else item.get("side", ""),
                    "trade_date": str(item.get("trade_datetime", ""))[:10].replace("/", "-"),
                    "quantity": float(runtime.d(item.get("quantity"))),
                    "price": float(runtime.d(item.get("price"))),
                    "gross_amount": float(runtime.d(item.get("amount"))),
                    "fee_total": float(runtime.d(item.get("fee_total"))),
                    "source_file": item.get("source_file", str(path)),
                    "exchange": "",
                    "settle_date": str(item.get("settle_date", "")).replace("/", "-") or None,
                    "order_id": item.get("order_id"),
                    "cash_change": float(runtime.d(item.get("change_amount"))),
                    "fee_detail": item.get("fee_detail", {}),
                    "source_page": item.get("page"),
                    "raw_text": item.get("raw", ""),
                    "parser_layout": item.get("format", ""),
                    "exception": item.get("notes", ""),
                }
            )

        return {
            "trades": trade_records,
            "income": income,
            "financing_interest": financing,
            "exceptions": exceptions,
        }
