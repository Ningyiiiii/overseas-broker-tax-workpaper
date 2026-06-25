"""Huatai (华泰) overseas broker parser.

Parses Huatai Hong Kong monthly statements (0M) and daily statements (0D)
into normalized records per the schema.

Recognized sections inside each PDF page text:
- 成交单据 (Trade records) - monthly statements only
- 户口变动 (Account activities) - includes 买卖交易 settlements,
  资金存入 Dividend/Cash, 资金提取 Dividend Collection Fee / Scrip Fee,
  and 产品/现货存入 ... 分红 (fund dividend reinvestments)
- 股息/红股/公司行动 (Dividend / Scrip / Corporate Actions)
- 利息,待交收及待结算金额 (Daily interest / financing interest)
- 持货结存 (Holdings) - used to enrich security master
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from tax_workpaper.normalize.schema import FinancingInterestRecord, IncomeRecord, TradeRecord

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"

_CURRENCY_RE = re.compile(r"\b(HKD|USD|CNY|RMB)\b", re.IGNORECASE)


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip().replace(",", "").replace("(", "-").replace(")", "")
    if not value or value in {"-", "--"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _detect_currency(text: str, default: str = "HKD") -> str:
    counts: dict[str, int] = {}
    for m in _CURRENCY_RE.finditer(text or ""):
        token = m.group(1).upper()
        if token == "RMB":
            token = "CNY"
        counts[token] = counts.get(token, 0) + 1
    if not counts:
        return default
    return max(counts.items(), key=lambda item: item[1])[0]


def _account_no(text: str) -> str:
    m = re.search(r"客户户口\s*[:：]\s*(\d+)", text)
    return m.group(1) if m else ""


def _statement_period(text: str) -> tuple[str, str]:
    m = re.search(r"月结单\s*\((\d{4}-\d{2})\)", text)
    if m:
        return "monthly", m.group(1)
    m = re.search(r"(?:综合)?成交单据及帐户日结单\s*\((\d{4}-\d{2}-\d{2})\)", text)
    if m:
        return "daily", m.group(1)
    return "unknown", ""


def _section(text: str, start: str, end_candidates: list[str]) -> str:
    idx = text.find(start)
    if idx < 0:
        return ""
    end = len(text)
    for marker in end_candidates:
        m = re.search(marker, text[idx + len(start):])
        if m:
            end = min(end, idx + len(start) + m.start())
    return text[idx:end]


def _parse_trade_row(primary: str, secondary: str) -> TradeRecord | None:
    primary = primary.strip()
    m = re.match(
        r"(?P<ref>\d{8,12})\s+"
        r"(?P<trade_date>\d{4}-\d{2}-\d{2})\s+"
        r"(?P<side>买入|沽出)\s+"
        r"(?P<code>(?:\d{4,6}|[A-Z]{2}\d{4,12})(?::[A-Z]+)?)\s+"
        r"(?P<price>\d+\.\d+)\s+"
        r"(?P<qty>-?\(?-?[\d,]+(?:\.\d+)?\)?)\s+"
        r"(?P<amount>\(?-?[\d,]+\.\d{2}\)?)\s+"
        r"(?P<commission>\(?-?[\d,]+\.\d{2}\)?)\s+"
        r"(?P<net>\(?-?[\d,]+\.\d{2}\)?)\s*$",
        primary,
    )
    if not m:
        return None
    code = m.group("code")
    # Strip optional market suffix like :FUND or :HK from fund trade rows.
    if ":" in code:
        code = code.split(":", 1)[0]
    side_text = m.group("side")
    side = SIDE_BUY if side_text == "买入" else SIDE_SELL
    qty_str = m.group("qty").replace("(", "").replace(")", "").replace(",", "")
    amount_str = m.group("amount").replace("(", "").replace(")", "").replace(",", "")
    commission_str = m.group("commission").replace("(", "").replace(")", "").replace(",", "")
    price = _to_float(m.group("price"))
    qty = _to_float(qty_str)
    gross = _to_float(amount_str)
    commission = _to_float(commission_str)
    if qty is None or price is None or gross is None:
        return None
    if side == SIDE_SELL and qty > 0:
        qty = -qty
    gross = abs(gross)
    raw_text = (primary + "\n" + secondary).strip()
    return TradeRecord(
        broker="huatai",
        market="HK",
        currency="HKD",
        code=code,
        name="",
        side=side,
        trade_date=m.group("trade_date"),
        settle_date=None,
        order_id=m.group("ref"),
        trade_id=m.group("ref"),
        quantity=qty,
        price=price,
        gross_amount=gross,
        fee_total=abs(commission) if commission is not None else 0.0,
        source_file="",
        source_page=0,
        source_row=0,
        raw_text=raw_text,
    )


def _parse_trade_section(section_text: str) -> list[TradeRecord]:
    if not section_text:
        return []
    lines = [ln for ln in section_text.splitlines() if ln.strip()]
    start = 0
    for i, ln in enumerate(lines):
        if ln.strip() == "成交单据":
            start = i + 1
            break
    while start < len(lines) and ("参考编号" in lines[start] or "交易日期" in lines[start] or "市场费用" in lines[start]):
        start += 1
    rows = lines[start:]
    trades: list[TradeRecord] = []
    i = 0
    while i < len(rows):
        primary = rows[i]
        if re.match(r"^\d{8,12}\s+\d{4}-\d{2}-\d{2}\s+(买入|沽出)", primary):
            secondary = rows[i + 1] if i + 1 < len(rows) else ""
            trade = _parse_trade_row(primary, secondary)
            if trade is not None:
                trades.append(trade)
            i += 2
        else:
            i += 1
    return trades


def _parse_settlement_trade(line: str) -> TradeRecord | None:
    m = re.match(
        r"(?P<ref>\d{8,12})\s+"
        r"(?P<settle_date>\d{4}-\d{2}-\d{2})\s+"
        r"(?P<trade_date>\d{4}-\d{2}-\d{2})\s+"
        r"买卖交易\s+(?P<side>买入|沽出)\s+"
        r"(?P<code>(?:\d{4,6}|[A-Z]{2}\d{4,12})(?::[A-Z]+)?)\s+"
        r"(?P<name>.+?)\s+@(?P<price>\d+\.\d+)\s+"
        r"(?P<qty>-?\(?-?[\d,]+(?:\.\d+)?\)?)\s+"
        r"(?P<net>\(?-?[\d,]+\.\d{2}\)?)",
        line.strip(),
    )
    if not m:
        return None
    code = m.group("code")
    if ":" in code:
        code = code.split(":", 1)[0]
    side_text = m.group("side")
    side = SIDE_BUY if side_text == "买入" else SIDE_SELL
    qty_str = m.group("qty").replace("(", "").replace(")", "").replace(",", "")
    net_str = m.group("net").replace("(", "").replace(")", "").replace(",", "")
    qty = _to_float(qty_str)
    price = _to_float(m.group("price"))
    net = _to_float(net_str)
    if qty is None or price is None or net is None:
        return None
    if side == SIDE_SELL and qty > 0:
        qty = -qty
    gross = price * abs(qty)
    fee = abs(abs(net) - abs(gross))
    return TradeRecord(
        broker="huatai",
        market="HK",
        currency="HKD",
        code=code,
        name=m.group("name").strip(),
        side=side,
        trade_date=m.group("trade_date"),
        settle_date=m.group("settle_date"),
        order_id=m.group("ref"),
        trade_id=m.group("ref"),
        quantity=qty,
        price=price,
        gross_amount=gross,
        fee_total=fee,
        source_file="",
        source_page=0,
        source_row=0,
        raw_text=line.strip(),
    )


def _parse_account_activity(line: str, currency: str, seen_dividend_keys: set[tuple[str, str]] | None = None) -> dict | None:
    raw = line.strip()
    if "买卖交易" in raw:
        trade = _parse_settlement_trade(raw)
        return {"type": "trade", "trade": trade} if trade else None
    m = re.match(
        r"(?P<ref>\d{8,12})\s+"
        r"(?P<date>\d{4}-\d{2}-\d{2})\s+"
        r"资金存入\s+Dividend/Cash\s+"
        r"(?P<code>[A-Z0-9:]+)\s+(?P<note>.+?)\s+"
        r"(?P<amount>-?[\d,]+\.\d{2})\s+(?P<balance>-?[\d,]+\.\d{2})",
        raw,
    )
    if m:
        if seen_dividend_keys is not None and (m.group("date"), m.group("code")) in seen_dividend_keys:
            return None
        return {
            "type": "cash_dividend",
            "ref": m.group("ref"),
            "date": m.group("date"),
            "code": m.group("code"),
            "note": m.group("note").strip(),
            "amount": _to_float(m.group("amount")),
            "currency": "CNY" if "RMB" in m.group("note").upper() else currency,
        }
    m = re.match(
        r"(?P<ref>\d{8,12})\s+"
        r"(?P<date>\d{4}-\d{2}-\d{2})\s+"
        r"资金提取\s+Dividend Collection Fee\s+(?P<code>[A-Z0-9:]+)\s+(?P<note>.*?)\s+"
        r"(?P<amount>\(?-?[\d,]+\.\d{2}\)?)\s+(?P<balance>\(?-?[\d,]+\.\d{2}\)?)",
        raw,
    )
    if m:
        return {
            "type": "dividend_fee",
            "ref": m.group("ref"),
            "date": m.group("date"),
            "code": m.group("code"),
            "note": m.group("note").strip(),
            "amount": abs(_to_float(m.group("amount")) or 0.0),
            "balance": _to_float(m.group("balance")),
        }
    m = re.match(
        r"(?P<ref>\d{8,12})\s+"
        r"(?P<date>\d{4}-\d{2}-\d{2})\s+"
        r"资金提取\s+Scrip Fee\s+(?P<code>[A-Z0-9:]+)\s+(?P<note>.*?)\s+"
        r"(?P<amount>\(?-?[\d,]+\.\d{2}\)?)\s+(?P<balance>\(?-?[\d,]+\.\d{2}\)?)",
        raw,
    )
    if m:
        return {
            "type": "scrip_fee",
            "ref": m.group("ref"),
            "date": m.group("date"),
            "code": m.group("code"),
            "note": m.group("note").strip(),
            "amount": abs(_to_float(m.group("amount")) or 0.0),
            "balance": _to_float(m.group("balance")),
        }
    m = re.match(
        r"(?P<ref>\d{8,12})\s+"
        r"(?P<date>\d{4}-\d{2}-\d{2})\s+"
        r"(?:产品存入|现货存入)\s+"
        r"(?P<code>HK\d+)\s+(?P<note>.*?分红.*?)\s+"
        r"(?P<qty>\d+\.\d+)\s+(?P<balance>-?[\d,]+\.\d{2})",
        raw,
    )
    if m:
        return {
            "type": "fund_dividend_reinvest",
            "ref": m.group("ref"),
            "date": m.group("date"),
            "code": m.group("code"),
            "note": m.group("note").strip(),
            "qty": _to_float(m.group("qty")),
            "balance": _to_float(m.group("balance")),
        }
    # Monthly Interest Paid / Charged
    m = re.match(
        r"(?P<ref>\d{8,12})\s+"
        r"(?P<date>\d{4}-\d{2}-\d{2})\s+"
        r"资金提取\s+(?P<note>Monthly Interest Paid|Monthly Interest Charged|Interest Paid|Interest Charged)\s*"
        r"(?P<amount>\(?-?[\d,]+\.\d{2}\)?)",
        raw,
    )
    if m:
        return {
            "type": "monthly_interest",
            "ref": m.group("ref"),
            "date": m.group("date"),
            "note": m.group("note").strip(),
            "amount": abs(_to_float(m.group("amount")) or 0.0),
        }
    # Cash value-added yield
    m = re.match(
        r"(?P<ref>\d{8,12})\s+"
        r"(?P<date>\d{4}-\d{2}-\d{2})\s+"
        r"资金存入\s+(?P<note>现金增值收益分配|Cash Yield Distribution)\s*"
        r"(?P<amount>-?[\d,]+\.\d{2})",
        raw,
    )
    if m:
        return {
            "type": "cash_yield",
            "ref": m.group("ref"),
            "date": m.group("date"),
            "note": m.group("note").strip(),
            "amount": _to_float(m.group("amount")),
        }
    return None


def _parse_interest_section(section_text: str) -> list[FinancingInterestRecord]:
    if not section_text:
        return []
    out: list[FinancingInterestRecord] = []
    period_date: str | None = None
    for ln in section_text.splitlines():
        m_date = re.search(r"(\d{4}-\d{2}-\d{2})", ln)
        if m_date and not period_date:
            period_date = m_date.group(1)
        m = re.match(
            r"(?P<ccy>HKD|USD|CNY|RMB)\s+"
            r"(?P<interest>[\d,]+\.\d{2})\s+<(?P<rate>[^>]+)>\s+"
            r"(?P<pending_d1>[\d,]+\.\d{2})\s+"
            r"(?P<pending_d2>[\d,]+\.\d{2})\s+"
            r"(?P<pending_d3>[\d,]+\.\d{2})\s+"
            r"(?P<pending_total>[\d,]+\.\d{2})\s+"
            r"(?P<pending_count>[\d,]+\.\d{2})\s+"
            r"(?P<to_settle>[\d,]+\.\d{2})",
            ln.strip(),
        )
        if m:
            interest = _to_float(m.group("interest")) or 0.0
            if interest == 0.0:
                continue
            ccy = m.group("ccy").upper()
            if ccy == "RMB":
                ccy = "CNY"
            out.append(
                FinancingInterestRecord(
                    broker="huatai",
                    market="HK",
                    currency=ccy,
                    date=period_date or "",
                    amount=interest,
                    source_file="",
                    source_page=0,
                    source_row=0,
                    raw_text=ln.strip(),
                )
            )
    return out


def _parse_dividend_actions(section_text: str, currency: str, source_file: str = "", page: int = 0) -> list[IncomeRecord]:
    if not section_text:
        return []
    out: list[IncomeRecord] = []
    started = False
    for ln in section_text.splitlines():
        if "股息/红股/公司行动" in ln:
            started = True
            continue
        if not started:
            continue
        m = re.match(
            r"(?P<code>\d{4,6}:[A-Z]{2})\s+"
            r"(?P<name>.+?)\s+"
            r"(?P<qty>[\d,]+(?:\.\d+)?)\s+"
            r"(?P<note>.+?)\s+"
            r"(?P<ccy>HKD|USD|CNY|RMB)\s+"
            r"(?P<amount>[\d,]+\.\d{2})\s+"
            r"(?P<scrip>[\d,]+(?:\.\d+)?)\s+"
            r"(?P<date>\d{4}-\d{2}-\d{2})",
            ln.strip(),
        )
        if m:
            ccy = m.group("ccy").upper()
            if ccy == "RMB":
                ccy = "CNY"
            out.append(
                IncomeRecord(
                    broker="huatai",
                    market="HK",
                    currency=ccy,
                    date=m.group("date"),
                    code=m.group("code"),
                    name=m.group("name").strip(),
                    category="股息/分派",
                    amount=_to_float(m.group("amount")) or 0.0,
                    tax_withheld=None,
                    fee=None,
                    source_file=source_file,
                    source_page=page,
                    source_row=0,
                    raw_text=ln.strip(),
                )
            )
    return out


def _parse_holdings(section_text: str) -> list[dict]:
    """Parse the 持货结存 section to capture security names by code.

    The section is column-heavy and pdfplumber sometimes wraps long names
    across multiple lines. We do two passes:

    1. Split the section by the number-of-numeric-tokens pattern and
       identify each holding by its leading code.
    2. If a line contains a code + name + more than 6 numeric tokens,
       treat the following non-code, non-section, non-totals lines as a
       continuation of the name.
    """
    if not section_text:
        return []
    rows: list[dict] = []
    current_market = "HK"
    started = False
    lines = section_text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if "持货结存" in ln:
            started = True
            i += 1
            continue
        if not started:
            i += 1
            continue
        m_market = re.match(r"\s*(HK|US|FUND)\s*-\s*[A-Z \u4e00-\u9fff]+\s*\(([A-Z]{3})\)", ln)
        if m_market:
            current_market = m_market.group(1)
            if current_market == "FUND":
                current_market = "FUND"
            i += 1
            continue
        # Skip column header lines and blank or annotation lines.
        if not ln.strip() or ln.strip().startswith("代码") or ln.strip().startswith("*"):
            i += 1
            continue
        m = re.match(
            r"(?P<code>\d{4,6}|[A-Z]{2}\d{4,12})\s+(?P<rest>.+)$",
            ln.strip(),
        )
        if not m:
            i += 1
            continue
        code = m.group("code")
        rest = m.group("rest")
        # Split rest into tokens and count numeric tokens.
        tokens = rest.split()
        # Merge name tokens from continuation lines if the row contains too
        # many numeric tokens to be a single holding.
        # Concatenate the next lines until the row would yield at most 5
        # numeric tokens total (the standard HK column count is 5 plus a few
        # footer numerics). We do this by collecting tokens whose first
        # character is non-numeric or "-" followed by a digit.
        numeric_re = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
        numeric_count = sum(1 for t in tokens if numeric_re.match(t))
        extra_tokens: list[str] = []
        # If the parsed code looks like a HK-prefixed fund code that was split
        # across lines, the first continuation line often starts with the
        # remaining digits. Detect this and append to the code instead of
        # the name.
        code_extra = ""
        if re.match(r"^[A-Z]{2}\d{4,8}$", code) and tokens and numeric_re.match(tokens[0]):
            # The first token after the code is a number; this is the holding
            # qty, not part of the name. Don't try to merge codes here.
            pass
        # Only attempt continuation if we have an excessive number of numeric
        # tokens or fewer name tokens than expected.
        if numeric_count >= 5:
            # Heuristic: the fund name wraps to subsequent lines.
            j = i + 1
            current_code_starts_hk = code.startswith("HK")
            while j < len(lines):
                nxt = lines[j].strip()
                if not nxt or nxt.startswith("代码") or nxt.startswith("*") or "=" in nxt:
                    j += 1
                    continue
                # A new holding has many numbers following the name. A
                # continuation line has just a name and no numbers.
                nxt_tokens = nxt.split()
                nxt_numeric_re = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
                nxt_numeric_count = sum(1 for t in nxt_tokens if nxt_numeric_re.match(t))
                if nxt_numeric_count >= 3:
                    # Looks like a new holding row.
                    break
                if any(kw in nxt for kw in ["户口变动", "成交单据", "持货结存", "利息,", "重要提示", "股票借贷", "股息/红股/公司行动"]):
                    break
                # Stop if this looks like a totals or bottom note.
                if re.match(r"^[A-Z]{3}\s+", nxt):
                    break
                # Break on market subheader lines - they signal the start of
                # a new sub-section (FUND, HK, US), so the fund's name
                # continuation cannot cross this boundary.
                if re.match(r"^[A-Z]{1,5}\s*-\s*[A-Z \u4e00-\u9fff]+\s*\([A-Z]{3}\)\s*$", nxt):
                    break
                # If the line starts with 1-5 digits and the rest is name-like
                # (e.g. "46540 Income Fund "A" (HKD)"), treat the digits as
                # the continuation of an HK-prefixed fund code and the rest
                # as name continuation.
                m_partial_code = re.match(r"^(\d{1,5})\s+(.+)$", nxt)
                if m_partial_code and current_code_starts_hk:
                    code_extra = m_partial_code.group(1)
                    extra_tokens.append(m_partial_code.group(2))
                    j += 1
                    continue
                # Pure digits on their own line are also treated as a code
                # continuation.
                if re.match(r"^\d+$", nxt):
                    code_extra = nxt
                    j += 1
                    continue
                extra_tokens.append(nxt)
                j += 1
        # Rebuild name as the first non-numeric tokens.
        name_tokens: list[str] = []
        for t in tokens:
            if numeric_re.match(t):
                break
            name_tokens.append(t)
        if extra_tokens or code_extra:
            name_tokens = name_tokens + extra_tokens
            i = j  # advance past continuation lines
        if code_extra:
            code = code + code_extra
        name = " ".join(name_tokens).strip()
        # Strip stray punctuation from name.
        name = re.sub(r"\s+", " ", name)
        market = current_market
        # FUND subheading -> map to HK.
        if market == "FUND":
            market = "HK"
        rows.append({"market": market, "code": code, "name": name})
        i += 1
    return rows


def _name_from_trade_raw(raw: str) -> str:
    m = re.search(r"\)\s+\d{4}-\d{2}-\d{2}\s+([A-Z][A-Z0-9 '\-\.&/]+?)\s+\d", raw)
    if m:
        return m.group(1).strip()
    return ""


class HuataiParser:
    broker = "huatai"

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def parse(self, path: Path, password_candidates: list[str]) -> dict:
        trades: list[TradeRecord] = []
        activities: list[dict] = []
        income: list[IncomeRecord] = []
        financing: list[FinancingInterestRecord] = []
        holdings: list[dict] = []
        exceptions: list[dict] = []
        statement_period = ""
        statement_kind = ""
        account = ""

        try:
            with pdfplumber.open(str(path)) as pdf:
                # Pre-scan to gather (date, code) keys from 股息/红股/公司行动
                # so we can skip duplicate 资金存入 Dividend/Cash entries.
                seen_dividend_keys: set[tuple[str, str]] = set()
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    for ln in text.splitlines():
                        m = re.match(
                            r"\d{4,6}:[A-Z]{2}\s+.+?\s+[\d,]+(?:\.\d+)?\s+.+?\s+"
                            r"HKD|USD|CNY|RMB\s+[\d,]+\.\d{2}\s+[\d,]+(?:\.\d+)?\s+"
                            r"(\d{4}-\d{2}-\d{2})",
                            ln.strip(),
                        )
                        if m:
                            m2 = re.match(r"(\d{4,6}:[A-Z]{2})", ln.strip())
                            if m2:
                                seen_dividend_keys.add((m.group(1), m2.group(1)))
                for page_index, page in enumerate(pdf.pages):
                    text = page.extract_text() or ""
                    if not statement_period:
                        kind, period = _statement_period(text)
                        if kind != "unknown":
                            statement_kind, statement_period = kind, period
                    if not account:
                        account = _account_no(text)
                    currency = _detect_currency(text, default="HKD")
                    trade_section = _section(text, "成交单据", ["户口变动", "持货结存", "利息,", "重要提示", "股票借贷"])
                    if "参考编号" in trade_section and "交收日期" in trade_section:
                        for trade in _parse_trade_section(trade_section):
                            trade = TradeRecord(
                                **{**trade.__dict__, "source_file": str(path), "source_page": page_index + 1}
                            )
                            if not trade.name:
                                trade = TradeRecord(
                                    **{**trade.__dict__, "name": _name_from_trade_raw(trade.raw_text)}
                                )
                            trades.append(trade)
                    activity_section = _section(text, "户口变动", ["持货结存", "利息,", "重要提示", "股票借贷", "股息"])
                    if activity_section:
                        for ln in activity_section.splitlines():
                            parsed = _parse_account_activity(ln, currency, seen_dividend_keys)
                            if parsed is None:
                                continue
                            activities.append({"page": page_index + 1, **parsed})
                    income.extend(_parse_dividend_actions(text, currency, source_file=str(path), page=page_index + 1))
                    financing.extend(_parse_interest_section(text))
                    holdings.extend(_parse_holdings(text))
        except Exception as exc:  # noqa: BLE001
            exceptions.append({"source_file": str(path), "type": "pdf_read_failure", "detail": str(exc)})
            return {
                "broker": self.broker,
                "source_file": str(path),
                "statement_kind": statement_kind,
                "statement_period": statement_period,
                "account": account,
                "trades": [],
                "activities": [],
                "income": [],
                "financing_interest": [],
                "holdings": [],
                "exceptions": exceptions,
            }

        for act in activities:
            if act.get("type") == "trade" and act.get("trade") is not None:
                trade = act["trade"]
                trade = TradeRecord(
                    **{**trade.__dict__, "source_file": str(path), "source_page": act.get("page", 0)}
                )
                already = any(
                    t.trade_id == trade.trade_id
                    and t.trade_date == trade.trade_date
                    and t.side == trade.side
                    and abs(t.quantity) == abs(trade.quantity)
                    for t in trades
                )
                if not already:
                    trades.append(trade)
            elif act.get("type") == "cash_dividend" and act.get("amount"):
                income.append(
                    IncomeRecord(
                        broker=self.broker,
                        market="HK",
                        currency=act["currency"],
                        date=act["date"],
                        code=act["code"],
                        name="",
                        category="股息/分派",
                        amount=act["amount"],
                        tax_withheld=None,
                        fee=None,
                        source_file=str(path),
                        source_page=act.get("page", 0),
                        source_row=0,
                        raw_text=str(act.get("note", "")),
                    )
                )
            elif act.get("type") == "fund_dividend_reinvest" and act.get("qty"):
                income.append(
                    IncomeRecord(
                        broker=self.broker,
                        market="HK",
                        currency="HKD",
                        date=act["date"],
                        code=act["code"],
                        name=act["note"].split("分红")[0].strip().split("\n")[0],
                        category="公司行动",
                        amount=0.0,
                        tax_withheld=None,
                        fee=None,
                        source_file=str(path),
                        source_page=act.get("page", 0),
                        source_row=0,
                        raw_text=f"分红再投资 +{act['qty']} 单位",
                    )
                )
            elif act.get("type") in {"dividend_fee", "scrip_fee"} and act.get("amount"):
                income.append(
                    IncomeRecord(
                        broker=self.broker,
                        market="HK",
                        currency="HKD",
                        date=act["date"],
                        code=act["code"],
                        name=act["note"],
                        category="税费扣减" if act["type"] == "dividend_fee" else "利息",
                        amount=act["amount"],
                        tax_withheld=None,
                        fee=None,
                        source_file=str(path),
                        source_page=act.get("page", 0),
                        source_row=0,
                        raw_text=act["type"],
                    )
                )
            elif act.get("type") == "monthly_interest" and act.get("amount"):
                financing.append(
                    FinancingInterestRecord(
                        broker=self.broker,
                        market="HK",
                        currency="HKD",
                        date=act["date"],
                        amount=act["amount"],
                        source_file=str(path),
                        source_page=act.get("page", 0),
                        source_row=0,
                        raw_text=act["note"],
                    )
                )
            elif act.get("type") == "cash_yield" and act.get("amount"):
                income.append(
                    IncomeRecord(
                        broker=self.broker,
                        market="HK",
                        currency="HKD",
                        date=act["date"],
                        code="",
                        name=act["note"],
                        category="利息",
                        amount=act["amount"],
                        tax_withheld=None,
                        fee=None,
                        source_file=str(path),
                        source_page=act.get("page", 0),
                        source_row=0,
                        raw_text=act["note"],
                    )
                )

        return {
            "broker": self.broker,
            "source_file": str(path),
            "statement_kind": statement_kind,
            "statement_period": statement_period,
            "account": account,
            "trades": trades,
            "activities": activities,
            "income": income,
            "financing_interest": financing,
            "holdings": holdings,
            "exceptions": exceptions,
        }
