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

from tax_workpaper.normalize.schema import (
    FinancingInterestRecord,
    IncomeRecord,
    TradeRecord,
)


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
    _detect_keywords = [
        "Futu",
        "富途",
        "FUTU",
        "Futu Securities",
        "Account No.",
        "SEHK",
        "Scrip Charge",
        "Handling Charge",
    ]

    def can_parse(self, path: Path) -> bool:
        if path.suffix.lower() not in {".pdf", ".xlsx", ".xls", ".csv"}:
            return False
        name = path.name.upper()
        if "FUTU" in name or "富途" in path.name or "1001231828219038" in name or "100110" in name:
            return True
        if path.suffix.lower() != ".pdf":
            return False
        try:
            with pdfplumber.open(path) as pdf:
                text = "\n".join((page.extract_text() or "") for page in pdf.pages[:2])
            return self.can_parse_with_text(text)
        except Exception:
            return False

    def can_parse_with_text(self, text: str) -> bool:
        return any(keyword in (text or "") for keyword in self._detect_keywords)

    def parse(self, path: Path, password_candidates: list[str]) -> dict:
        """Parse a Futu source file into normalized records.

        This uses the validated Futu runtime parser bundled with the skill. The
        parser still keeps broker-specific layout handling here and emits plain
        normalized dictionaries for the shared engines.
        """

        runtime.configure_runtime(
            source_root=path.parent,
            output_dir=path.parent / "outputs",
            password=password_candidates[0] if password_candidates else "",
            passwords=password_candidates or [],
        )
        runtime.PASSWORD = password_candidates[0] if password_candidates else ""
        runtime.PASSWORDS = list(password_candidates or [])
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
                with runtime.open_pdf_with_passwords(path) as pdf:
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
                TradeRecord(
                    broker=self.broker,
                    market=item.get("market", ""),
                    currency=item.get("currency", ""),
                    code=item.get("code", ""),
                    name=item.get("name", ""),
                    side="BUY" if item.get("side") == "buy" else "SELL" if item.get("side") == "sell" else item.get("side", ""),
                    trade_date=str(item.get("trade_datetime", ""))[:10].replace("/", "-"),
                    settle_date=str(item.get("settle_date", "")).replace("/", "-") or None,
                    order_id=item.get("order_id"),
                    trade_id=item.get("order_id"),
                    quantity=float(runtime.d(item.get("quantity"))),
                    price=float(runtime.d(item.get("price"))),
                    gross_amount=float(runtime.d(item.get("amount"))),
                    fee_total=float(runtime.d(item.get("fee_total"))),
                    source_file=item.get("source_file", str(path)),
                    source_page=item.get("page"),
                    source_row=None,
                    raw_text=item.get("raw", "") or item.get("notes", ""),
                )
            )

        income_records = []
        for row in income:
            item = asdict(row) if hasattr(row, "__dataclass_fields__") else dict(row)
            income_records.append(
                IncomeRecord(
                    broker=self.broker,
                    market=item.get("market", ""),
                    currency=item.get("currency", ""),
                    date=str(item.get("date", "")).replace("/", "-"),
                    code=item.get("code", ""),
                    name=item.get("name", ""),
                    category=item.get("category", "股息/分派"),
                    amount=float(runtime.d(item.get("amount"))),
                    tax_withheld=float(runtime.d(item.get("tax_withheld"))) if item.get("tax_withheld") not in (None, "") else None,
                    fee=float(runtime.d(item.get("fee"))) if item.get("fee") not in (None, "") else None,
                    source_file=item.get("source_file", str(path)),
                    source_page=item.get("page"),
                    source_row=None,
                    raw_text=item.get("raw", ""),
                )
            )

        financing_records = []
        for row in financing:
            item = asdict(row) if hasattr(row, "__dataclass_fields__") else dict(row)
            financing_records.append(
                FinancingInterestRecord(
                    broker=self.broker,
                    market=item.get("market", ""),
                    currency=item.get("currency", ""),
                    date=str(item.get("date", "")).replace("/", "-"),
                    amount=float(runtime.d(item.get("amount"))),
                    source_file=item.get("source_file", str(path)),
                    source_page=item.get("page"),
                    source_row=None,
                    raw_text=item.get("raw", ""),
                )
            )

        return {
            "trades": trade_records,
            "income": income_records,
            "financing_interest": financing_records,
            "exceptions": exceptions,
            "broker": self.broker,
            "source_file": str(path),
        }
