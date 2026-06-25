"""USMART (盈立证券) PDF parser.

Handles two layout variants:
- Old bilingual format (M002C-E): Traditional Chinese + English, "買 #00883" inline.
- New simplified format (M21/M2/D11): Simplified Chinese, table-style "港股 买入/卖出".
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber


# ---- Text normalization ----
# PDF uses CJK compatibility ideographs (Kangxi radicals U+2F00-U+2FD5 and others).
# Build the mapping programmatically from the Unicode database for correctness.

def _build_cjk_compat_map() -> dict[str, str]:
    """Build a mapping from CJK compatibility characters to standard forms."""
    m: dict[str, str] = {}
    # Kangxi radicals U+2F00-U+2FD5: each decomposes to a CJK ideograph
    for cp in range(0x2F00, 0x2FD6):
        ch = chr(cp)
        decomp = unicodedata.decomposition(ch)
        if decomp:
            parts = decomp.split()
            if parts and parts[0].startswith("<"):
                if len(parts) > 1:
                    target = chr(int(parts[1], 16))
                    m[ch] = target
            else:
                target = chr(int(parts[0], 16))
                m[ch] = target
    # CJK Compatibility ideographs (U+F900-U+FAFF) -> their standard equivalents
    for cp in range(0xF900, 0xFB00):
        ch = chr(cp)
        decomp = unicodedata.decomposition(ch)
        if decomp:
            parts = decomp.split()
            if parts and parts[0].startswith("<") and len(parts) > 1:
                target = chr(int(parts[1], 16))
                m[ch] = target
            elif parts:
                target = chr(int(parts[0], 16))
                m[ch] = target
    return m


_CJK_COMPAT_MAP = _build_cjk_compat_map()


def normalize_text(text: str) -> str:
    if not text:
        return text
    return "".join(_CJK_COMPAT_MAP.get(ch, ch) for ch in text)


def parse_number(s: str) -> float:
    s = s.strip().replace(",", "")
    if not s:
        return 0.0
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    if s.startswith("-"):
        negative = True
        s = s[1:]
    try:
        val = float(s)
    except ValueError:
        return 0.0
    return -val if negative else val


def parse_date(s: str) -> str:
    s = s.strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
              "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    m = re.match(r"^(\d{1,2})\s+([A-Z]{3})\s+(\d{4})", s)
    if m:
        day = int(m.group(1))
        mon = months.get(m.group(2), 0)
        year = int(m.group(3))
        if mon:
            return f"{year:04d}-{mon:02d}-{day:02d}"
    return s


@dataclass
class ParsedStatement:
    trades: list = field(default_factory=list)
    incomes: list = field(default_factory=list)
    financing_interests: list = field(default_factory=list)
    exceptions: list = field(default_factory=list)
    statement_date: str | None = None
    statement_type: str | None = None
    source_file: str = ""


@dataclass
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


@dataclass
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


@dataclass
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


# ---- New format parser (M21/M2/D11) ----
_NEW_TRADE_LINE_RE = re.compile(
    r"^(港股|美股|A股通)\s+(买入|卖出|交易-买|交易-卖)\s+"
    r"([\d,]+)\s+(HKD|USD|CNY)\s+([\d,.]+)\s+(-?[\d,.]+)\s+(\d{4}-\d{2}-\d{2})"
)
_NEW_STOCK_HEADER_RE = re.compile(r"^(\d{4,6})\s+(.+)$")
_NEW_ORDER_ID_RE = re.compile(r"^(\d{15,})\s")
_NEW_STMT_DATE_RE = re.compile(r"结单日期[：:]\s*(\d{4}-\d{2}(?:-\d{2})?)")
_NEW_STMT_TYPE_RE = re.compile(r"(月结单|日结单)")


def _extract_code_from_remark(remark: str) -> str:
    m = re.search(r"#(\d{4,6})", remark)
    return m.group(1) if m else ""


def _parse_new_format(text: str, source_file: str) -> ParsedStatement:
    result = ParsedStatement(source_file=source_file)
    norm_text = normalize_text(text)
    lines = norm_text.split("\n")

    m = _NEW_STMT_TYPE_RE.search(norm_text)
    if m:
        result.statement_type = "monthly" if "月" in m.group(1) else "daily"
    m = _NEW_STMT_DATE_RE.search(norm_text)
    if m:
        result.statement_date = m.group(1)

    in_trade_section = False
    in_fund_section = False
    in_financing_section = False

    current_code = ""
    current_name_parts: list[str] = []
    current_order_id = ""
    pending_fees: dict[str, float] = {}
    pending_trade_lines: list[dict] = []

    def flush_trade_block():
        nonlocal current_code, current_name_parts, current_order_id, pending_fees, pending_trade_lines
        if not pending_trade_lines:
            current_code = ""
            current_name_parts = []
            current_order_id = ""
            pending_fees = {}
            pending_trade_lines = []
            return
        name = "".join(current_name_parts).strip()
        # Strip surrounding parentheses if present (e.g. "(康德莱医械)" -> "康德莱医械")
        if name.startswith("(") and name.endswith(")"):
            name = name[1:-1]
        fee_total = abs(pending_fees.get("交易费用合计", 0.0))
        for fill in pending_trade_lines:
            side_raw = fill["side"]
            if side_raw in ("买入", "交易-买"):
                side = "BUY"
            else:
                side = "SELL"
            market = {"港股": "HK", "美股": "US", "A股通": "CN"}.get(fill["market"], fill["market"])
            gross = abs(fill["gross"])
            result.trades.append(TradeRecord(
                broker="usmart", market=market, currency=fill["currency"],
                code=current_code, name=name, side=side,
                trade_date=fill["trade_date"],
                settle_date=fill.get("settle_date"),
                order_id=current_order_id or None, trade_id=None,
                quantity=fill["quantity"], price=fill["price"],
                gross_amount=gross, fee_total=fee_total,
                source_file=source_file, source_page=fill.get("page"),
                source_row=None, raw_text=fill.get("raw", ""),
            ))
        current_code = ""
        current_name_parts = []
        current_order_id = ""
        pending_fees = {}
        pending_trade_lines = []

    page_num = 0
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("===PAGE") and "===" in line[7:]:
            page_num += 1
            continue
        # Skip standalone page numbers (e.g. "1", "2")
        if re.match(r"^\d{1,3}$", line):
            continue

        if "交易明细" in line:
            in_trade_section = True
            in_fund_section = False
            in_financing_section = False
            continue
        if "持仓明细" in line:
            in_trade_section = False
            flush_trade_block()
            in_fund_section = False
            in_financing_section = False
            continue
        if "资金出入" in line:
            in_trade_section = False
            flush_trade_block()
            in_fund_section = True
            in_financing_section = False
            continue
        if "证券提存" in line:
            in_fund_section = False
            continue
        # Section header "融资利息" must be a standalone line, NOT "IPO融资利息"
        if line == "融资利息" or (line.startswith("融资利息") and len(line) <= 8):
            in_fund_section = False
            in_financing_section = True
            continue
        if "重要提示" in line:
            in_trade_section = False
            in_fund_section = False
            in_financing_section = False
            flush_trade_block()
            continue

        if in_trade_section:
            if "证券/编号" in line or "市场 买/卖" in line:
                continue
            if "暂无数据" in line:
                continue
            if line.startswith("币种 HKD") or line.startswith("币种 USD") or line.startswith("币种 CNY"):
                flush_trade_block()
                continue
            if any(line.startswith(x) for x in ("交易费用汇总", "佣金汇总", "平台费汇总", "总买入金额", "总卖出金额", "变动金额总计", "变动金额汇总")):
                flush_trade_block()
                continue

            m = _NEW_STOCK_HEADER_RE.match(line)
            if m and not _NEW_TRADE_LINE_RE.match(line):
                if pending_trade_lines:
                    flush_trade_block()
                current_code = m.group(1)
                name_part = m.group(2).strip()
                current_name_parts = [name_part]
                continue

            m = _NEW_TRADE_LINE_RE.match(line)
            if m:
                if pending_trade_lines and pending_fees:
                    flush_trade_block()
                elif pending_trade_lines and not current_code:
                    flush_trade_block()
                market = m.group(1)
                side = m.group(2)
                quantity = parse_number(m.group(3))
                currency = m.group(4)
                price = parse_number(m.group(5))
                gross = parse_number(m.group(6))
                trade_date = m.group(7)
                rest = line[m.end():].strip()
                settle_date = None
                date_matches = re.findall(r"\d{4}-\d{2}-\d{2}", rest)
                if date_matches:
                    settle_date = date_matches[0]
                pending_trade_lines.append({
                    "market": market, "side": side, "quantity": quantity,
                    "currency": currency, "price": price, "gross": gross,
                    "trade_date": trade_date, "settle_date": settle_date,
                    "page": page_num or None, "raw": line,
                })
                continue

            m = _NEW_ORDER_ID_RE.match(line)
            if m and pending_trade_lines:
                current_order_id = m.group(1)

            if pending_trade_lines:
                fee_pairs = re.findall(
                    r"(印花税|交收费|交易费|交易征费|证监会交易征费|财汇局交易征费|"
                    r"交易系统使用费用|交易费用合计|佣金|平台费|交易金额合计|变动金额合计)"
                    r"\s+(-?[\d,.]+)", line)
                if fee_pairs:
                    for label, val in fee_pairs:
                        pending_fees[label] = parse_number(val)
                    if "交易费用合计" in pending_fees and pending_trade_lines:
                        flush_trade_block()
                    continue

            if current_code and not pending_fees:
                # Allow parentheses in name continuation (e.g. "莱医械)")
                if re.match(r"^[\u4e00-\u9fffA-Za-z\-\(\)]+$", line) and len(line) <= 15:
                    current_name_parts.append(line)
                    continue

        if in_fund_section:
            if "业务标志" in line or "币种" in line:
                continue
            if "暂无数据" in line or "变动金额汇总" in line:
                continue
            m = re.match(
                r"^(股息入帐|股息入账|代收股息|过户费|红利手续费返款|利息入帐|利息入账|"
                r"罚息入账|买入股票|卖出股票|出金|入金|IPO资金冻结|IPO资金解冻|"
                r"IPO认购扣款|IPO认购退款|IPO认购手续费|IPO融资利息|"
                r"DBS|Coupon|Commission Return|Platform Fee Return|"
                r"现金存入|现金提取|资金转入|资金转出)\s+"
                r"(HKD|USD|CNY)\s+(-?[\d,.]+)\s+(\d{4}-\d{2}-\d{2})\s*(.*)$", line)
            if m:
                biz = m.group(1)
                currency = m.group(2)
                amount = parse_number(m.group(3))
                date = m.group(4)
                remark = m.group(5).strip()
                market = {"HKD": "HK", "USD": "US", "CNY": "CN"}.get(currency, currency)
                if biz in ("股息入帐", "股息入账"):
                    code = _extract_code_from_remark(remark)
                    result.incomes.append(IncomeRecord(
                        broker="usmart", market=market, currency=currency, date=date,
                        code=code, name="", category="股息/分派", amount=abs(amount),
                        tax_withheld=None, fee=None, source_file=source_file,
                        source_page=page_num or None, source_row=None, raw_text=line))
                elif biz == "代收股息":
                    code = _extract_code_from_remark(remark)
                    result.incomes.append(IncomeRecord(
                        broker="usmart", market=market, currency=currency, date=date,
                        code=code, name="", category="税费扣减", amount=amount,
                        tax_withheld=None, fee=None, source_file=source_file,
                        source_page=page_num or None, source_row=None, raw_text=line))
                elif biz == "过户费":
                    result.incomes.append(IncomeRecord(
                        broker="usmart", market=market, currency=currency, date=date,
                        code=_extract_code_from_remark(remark), name="", category="税费扣减",
                        amount=amount, tax_withheld=None, fee=None, source_file=source_file,
                        source_page=page_num or None, source_row=None, raw_text=line))
                elif biz in ("利息入帐", "利息入账"):
                    result.incomes.append(IncomeRecord(
                        broker="usmart", market=market, currency=currency, date=date,
                        code="", name="", category="利息", amount=amount,
                        tax_withheld=None, fee=None, source_file=source_file,
                        source_page=page_num or None, source_row=None, raw_text=line))
                elif biz in ("罚息入账", "IPO融资利息"):
                    result.financing_interests.append(FinancingInterestRecord(
                        broker="usmart", market=market, currency=currency, date=date,
                        amount=amount, source_file=source_file,
                        source_page=page_num or None, source_row=None, raw_text=line))
                continue

        if in_financing_section:
            if "暂无数据" in line:
                continue
            # Format 1: "HKD -amount date" (with date)
            m = re.match(r"^(HKD|USD|CNY)\s+(-?[\d,.]+)\s+(\d{4}-\d{2}-\d{2})", line)
            if m:
                currency = m.group(1)
                amount = parse_number(m.group(2))
                date = m.group(3)
                market = {"HKD": "HK", "USD": "US", "CNY": "CN"}.get(currency, currency)
                if abs(amount) > 0.01:
                    result.financing_interests.append(FinancingInterestRecord(
                        broker="usmart", market=market, currency=currency, date=date,
                        amount=amount, source_file=source_file,
                        source_page=page_num or None, source_row=None, raw_text=line))
                continue
            # Format 2: "HKD 6.60% 0.00" (currency rate% amount, no date)
            m = re.match(r"^(HKD|USD|CNY)\s+([\d.]+%)\s+(-?[\d,.]+)", line)
            if m:
                currency = m.group(1)
                amount = parse_number(m.group(3))
                market = {"HKD": "HK", "USD": "US", "CNY": "CN"}.get(currency, currency)
                # Use statement date if available, otherwise skip (amount is usually 0.00)
                if abs(amount) > 0.01 and result.statement_date:
                    result.financing_interests.append(FinancingInterestRecord(
                        broker="usmart", market=market, currency=currency,
                        date=result.statement_date, amount=amount,
                        source_file=source_file, source_page=page_num or None,
                        source_row=None, raw_text=line))
                continue

    flush_trade_block()
    return result


# ---- Old format parser (M002C-E) ----
# Main trade section: "17 FEB 2021 19 FEB 2021 ref 買 #00883 中國海洋石油 (40,168.63)"
# Sells have amount without parens: "...賣 #00883 中國海洋石油 40,870.76"
# Some sell lines have extra trailing numbers: "...中國海洋石油 40,870.76 186,612.98"
_OLD_TRADE_BUY_RE = re.compile(
    r"^(\d{1,2}\s+[A-Z]{3}\s+\d{4})\s+(\d{1,2}\s+[A-Z]{3}\s+\d{4})\s+(\S+)\s+"
    r"(買|賣|买|卖)\s+#(\d{4,6})\s+(.+?)\s+\(([\d,.]+)\)")
_OLD_TRADE_SELL_RE = re.compile(
    r"^(\d{1,2}\s+[A-Z]{3}\s+\d{4})\s+(\d{1,2}\s+[A-Z]{3}\s+\d{4})\s+(\S+)\s+"
    r"(買|賣|买|卖)\s+#(\d{4,6})\s+(.+?)\s+([\d,.]+)(?:\s+[\d,.]+)*\s*$")
# Unsettled trades section: "25 FEB 2021 01 MAR 2021 ref #01501康德萊醫械 賣 1,000 42.0000 41,909.07"
_OLD_UNSETTLED_RE = re.compile(
    r"^(\d{1,2}\s+[A-Z]{3}\s+\d{4})\s+(\d{1,2}\s+[A-Z]{3}\s+\d{4})\s+(\S+)\s+"
    r"#(\d{4,6})(\S+)\s+(買|賣|买|卖)\s+([\d,]+)\s+([\d.]+)\s+([\d,.]+)")
_OLD_TRADE_QTY_RE = re.compile(r"([\d,]+)\s*@\s*([\d.]+)")
_OLD_STMT_DATE_RE = re.compile(r"結單日期[：:]\s*(\d{1,2}\s+[A-Z]{3}\s+\d{4})\s*-\s*(\d{1,2}\s+[A-Z]{3}\s+\d{4})")


def _parse_old_format(text: str, source_file: str) -> ParsedStatement:
    result = ParsedStatement(source_file=source_file)
    norm_text = normalize_text(text)
    lines = norm_text.split("\n")
    m = _OLD_STMT_DATE_RE.search(norm_text)
    if m:
        result.statement_date = parse_date(m.group(2))
    result.statement_type = "monthly"

    # Section tracking: only read trades from "賬戶結單" (account statement),
    # NOT from "未結算交易" (unsettled trades) — those appear in next month's
    # account statement and would be double-counted.
    in_account_statement = False
    in_unsettled = False

    page_num = 0
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("===PAGE") and "===" in line[7:]:
            page_num += 1
            i += 1
            continue

        # Track sections
        if line in ("賬戶結單", "账户结单"):
            in_account_statement = True
            in_unsettled = False
            i += 1
            continue
        if line in ("未結算交易", "未结算交易"):
            in_account_statement = False
            in_unsettled = True
            i += 1
            continue
        if line in ("投資總結", "投资总结", "資金變動", "资金变动"):
            in_account_statement = False
            in_unsettled = False
            i += 1
            continue

        # Skip unsettled trades section entirely
        if in_unsettled:
            i += 1
            continue

        # Try buy format (amount in parentheses)
        m = _OLD_TRADE_BUY_RE.match(line)
        if m:
            trade_date = parse_date(m.group(1))
            settle_date = parse_date(m.group(2))
            order_id = m.group(3)
            side_raw = m.group(4)
            code = m.group(5)
            name = m.group(6).strip()
            net_amount = parse_number(m.group(7))
            side = "BUY" if side_raw in ("買", "买") else "SELL"
            quantity = 0.0
            price = 0.0
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                qm = _OLD_TRADE_QTY_RE.match(next_line)
                if qm:
                    quantity = parse_number(qm.group(1))
                    price = parse_number(qm.group(2))
                    i += 1
            gross = quantity * price
            if side == "BUY":
                fee = max(0, abs(net_amount) - gross)
            else:
                fee = max(0, gross - abs(net_amount))
            result.trades.append(TradeRecord(
                broker="usmart", market="HK", currency="HKD", code=code, name=name,
                side=side, trade_date=trade_date, settle_date=settle_date,
                order_id=order_id, trade_id=None, quantity=quantity, price=price,
                gross_amount=gross, fee_total=fee,
                source_file=source_file, source_page=page_num or None,
                source_row=None, raw_text=line))
            i += 1
            continue

        # Try sell format (amount without parentheses, at end of line)
        m = _OLD_TRADE_SELL_RE.match(line)
        if m:
            trade_date = parse_date(m.group(1))
            settle_date = parse_date(m.group(2))
            order_id = m.group(3)
            side_raw = m.group(4)
            code = m.group(5)
            name = m.group(6).strip()
            net_amount = parse_number(m.group(7))
            side = "BUY" if side_raw in ("買", "买") else "SELL"
            quantity = 0.0
            price = 0.0
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                qm = _OLD_TRADE_QTY_RE.match(next_line)
                if qm:
                    quantity = parse_number(qm.group(1))
                    price = parse_number(qm.group(2))
                    i += 1
            gross = quantity * price
            if side == "BUY":
                fee = max(0, abs(net_amount) - gross)
            else:
                fee = max(0, gross - abs(net_amount))
            result.trades.append(TradeRecord(
                broker="usmart", market="HK", currency="HKD", code=code, name=name,
                side=side, trade_date=trade_date, settle_date=settle_date,
                order_id=order_id, trade_id=None, quantity=quantity, price=price,
                gross_amount=gross, fee_total=fee,
                source_file=source_file, source_page=page_num or None,
                source_row=None, raw_text=line))
            i += 1
            continue

        i += 1
    return result


def _postprocess_statement(stmt: ParsedStatement, full_text: str):
    """Fill in missing stock codes for income records by looking at context."""
    if not stmt.incomes:
        return
    norm_text = normalize_text(full_text)
    lines = norm_text.split("\n")

    # Strategy 1: look at nearby lines (both forward and backward) for a code pattern
    # Skip for interest records (they don't have stock codes)
    for inc in stmt.incomes:
        if inc.code or inc.category == "利息":
            continue
        raw = inc.raw_text
        try:
            idx = lines.index(raw.strip())
        except ValueError:
            idx = -1
        if idx >= 0:
            for offset in [-2, -1, 1, 2, 3]:
                ni = idx + offset
                if ni < 0 or ni >= len(lines):
                    continue
                next_line = lines[ni].strip()
                # Look for #DDDD pattern
                m = re.search(r"#(\d{4,6})", next_line)
                if m:
                    code = m.group(1)
                    if not (len(code) == 4 and code.startswith("20")):
                        inc.code = code
                        break
                # Look for (DDDD) pattern
                m = re.search(r"\((\d{4,6})\)", next_line)
                if m:
                    code = m.group(1)
                    if not (len(code) == 4 and code.startswith("20")):
                        inc.code = code
                        break

    # Strategy 2: backfill from other income records on the same date (skip interest)
    code_by_date: dict[str, str] = {}
    for inc in stmt.incomes:
        if inc.code and inc.date and inc.category != "利息":
            code_by_date.setdefault(inc.date, inc.code)
    for inc in stmt.incomes:
        if not inc.code and inc.date and inc.date in code_by_date and inc.category != "利息":
            inc.code = code_by_date[inc.date]


def _normalize_statement_names(stmt: ParsedStatement):
    """Normalize stock names in all records (convert Traditional to Simplified if needed)."""
    # Names are already normalized via normalize_text in parsers; just strip whitespace
    for t in stmt.trades:
        if t.name:
            t.name = t.name.strip()
    for inc in stmt.incomes:
        if inc.name:
            inc.name = inc.name.strip()


def parse_usmart_pdf(path: Path, password: str) -> ParsedStatement:
    """Parse a single USMART PDF file."""
    full_text = ""
    with pdfplumber.open(str(path), password=password) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            full_text += f"\n===PAGE {page.page_number}===\n" + page_text

    # Normalize before format detection (PDF uses Kangxi radicals like ⽉ -> 月)
    norm = normalize_text(full_text)

    # Detect format on normalized text
    if "結單日期" in norm or "買 #" in norm or "賣 #" in norm:
        stmt = _parse_old_format(full_text, path.name)
    elif "结单日期" in norm or "月结单" in norm or "港股 买入" in norm or "港股 卖出" in norm:
        stmt = _parse_new_format(full_text, path.name)
    else:
        # Try both and pick the one with more total records
        stmt_old = _parse_old_format(full_text, path.name)
        stmt_new = _parse_new_format(full_text, path.name)
        old_count = len(stmt_old.trades) + len(stmt_old.incomes)
        new_count = len(stmt_new.trades) + len(stmt_new.incomes)
        stmt = stmt_old if old_count >= new_count else stmt_new

    _postprocess_statement(stmt, full_text)
    _normalize_statement_names(stmt)
    return stmt


def parse_all_pdfs(root: Path, password: str):
    """Parse all PDF files under root. Returns (statements, errors)."""
    statements = []
    errors = []
    for pdf_path in sorted(root.rglob("*.pdf")):
        try:
            stmt = parse_usmart_pdf(pdf_path, password)
            statements.append(stmt)
        except Exception as e:
            errors.append({"file": pdf_path.name, "error": str(e)})
    return statements, errors
