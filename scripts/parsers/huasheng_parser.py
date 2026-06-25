"""华盛通 (Valuable Capital Limited) PDF parser.

Parses two statement formats:
- Old format (202104-202208): Chinese-only, sections 買賣合約/資金變動/投資總結
- New format (202209+): Bilingual EN+CN, sections FINANCIAL OVERVIEW/CASH MOVEMENT/TRADING SUMMARY/SETTLED TRADES

Dividends appear in cash movement section with transaction codes:
  0320000 = dividend (DIV-HKD)
  0320010 = handling charge for dividend
  0320030 = scrip fee
  0604000 = debit balance interest (financing interest)
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber


# ---- CJK compatibility normalization ----

def _build_cjk_compat_map() -> dict[str, str]:
    m: dict[str, str] = {}
    for cp in range(0x2F00, 0x2FD6):
        ch = chr(cp)
        decomp = unicodedata.decomposition(ch)
        if decomp:
            parts = decomp.split()
            if parts and parts[0].startswith("<"):
                if len(parts) > 1:
                    m[ch] = chr(int(parts[1], 16))
            else:
                m[ch] = chr(int(parts[0], 16))
    for cp in range(0xF900, 0xFB00):
        ch = chr(cp)
        decomp = unicodedata.decomposition(ch)
        if decomp:
            parts = decomp.split()
            if parts[0].startswith("<") and len(parts) > 1:
                m[ch] = chr(int(parts[1], 16))
            elif parts:
                m[ch] = chr(int(parts[0], 16))
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


# ---- Traditional to Simplified Chinese mapping ----

_T2S = {
    "國": "国", "萊": "莱", "醫": "医", "械": "械", "買": "买", "賣": "卖",
    "結": "结", "單": "单", "証": "证", "券": "券", "寶": "宝", "實": "实",
    "電": "电", "訊": "讯", "氣": "气", "車": "车", "銀": "银", "行": "行",
    "中國": "中国", "石油": "石油", "控股": "控股", "集團": "集团",
    "綜": "综", "結": "结", "餘": "余", "額": "额", "變": "变", "動": "动",
    "現": "现", "股": "股", "數": "数", "價": "价", "格": "格", "總": "总",
    "費": "费", "稅": "税", "據": "据", "擇": "择",
    "證": "证", "經": "经", "紀": "纪", "傭": "佣", "佣": "佣",
    "印": "印", "花": "花", "徵": "征", "財": "财", "匯": "汇",
    "局": "局", "系": "系", "統": "统", "場": "场",
    "聯": "联", "合": "合", "交": "交", "易": "易", "所": "所",
    "港": "港", "元": "元", "幣": "币", "貨": "货",
    "日": "日", "期": "期", "結": "结", "算": "算",
    "參": "参", "考": "考", "編": "编", "號": "号",
    "存": "存", "取": "取", "承": "承", "前": "前", "轉": "转", "後": "后",
    "投": "投", "資": "资", "組": "组", "市": "市", "值": "值",
    "可": "可", "抵": "抵", "押": "押", "額": "额",
    "利": "利", "率": "率", "息": "息",
    "融": "融", "資": "资", "保": "保", "證": "证", "金": "金",
    "重": "重", "要": "要", "提": "提", "示": "示",
    "瑛": "瑛", "泰": "泰",
}


def to_simplified(s: str) -> str:
    for t, sp in _T2S.items():
        s = s.replace(t, sp)
    return s


# ---- Data classes ----

@dataclass
class ParsedStatement:
    trades: list = field(default_factory=list)
    incomes: list = field(default_factory=list)
    financing_interests: list = field(default_factory=list)
    exceptions: list = field(default_factory=list)
    statement_date: str | None = None
    statement_date_end: str | None = None
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


# ---- Old format regex patterns (202104-202208) ----

_OLD_STMT_DATE_RE = re.compile(r"結單日期[：:]\s*(\d{4}-\d{2}-\d{2})(?:\s*到\s*(\d{4}-\d{2}-\d{2}))?")
_OLD_TRADE_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+(\S+)\s+"
    r"(買|賣|买|卖)\s+#(\d{4,6})\s+(.+?)\s+"
    r"([\d,]+)\s+([\d.]+)\s+(-?[\d,.]+)$"
)
_OLD_FEE_LINE_RE = re.compile(r"^(經紀佣金|股票印花稅|交易費|交易徵費|平台使用費|中央結算費|交易系统使用费|財匯局交易徵費|交易金額)\s*[:：]\s*(-?[\d,.]+)$")
_OLD_CASH_TXN_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+(\d+)\s+(\d{7})\s+(.+?)\s+(-?[\d,.()]+)$"
)

# ---- New format regex patterns (202209+) ----

# Statement date: "Statement As At 結單⽇期： 2022-09-30" (note: ⽇ is Kangxi radical)
_NEW_STMT_DATE_RE = re.compile(r"Statement As At\s+結單.?期[：:]\s*(\d{4}-\d{2}-\d{2})")

# Stock header line in TRADING SUMMARY (US trades): "#CHA Chagee Holdings Ltd. 霸王"
# Accepts both numeric (#01501) and alphabetic (#CHA) codes
_NEW_STOCK_HEADER_RE = re.compile(r"^#(\w{2,10})\s+(.+)$")

# HK trade line in TRADING SUMMARY (with inline #code):
# 2025-06-10 2025-06-12 202540554343 Sell #01501 INT MEDICAL 瑛泰醫療 HKEX HKD 400 24.3000 9,689.18 AGENCY
_NEW_HK_TRADE_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+(\S+)\s+"
    r"(Buy|Sell)\s+"
    r"#(\w{2,10})\s+(.+?)\s+"
    r"HKEX\s+(HKD|USD|CNY)\s+([\d,]+)\s+([\d.]+)\s+(-?[\d,.()]+)\s+\S+$"
)

# US trade line in TRADING SUMMARY (no inline #code, stock info on separate header line):
# 2025-04-17 2025-04-21 20251004788797 Buy CODA USD 100 38.5000 (3,852.29) AGENCY
# Here "CODA" is the venue/exchange code, not the stock ticker.
# Stock code and name come from a preceding "#code Name" header line.
_NEW_US_TRADE_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+(\S+)\s+"
    r"(Buy|Sell)\s+"
    r"(\S+)\s+(USD|HKD|CNY)\s+([\d,]+)\s+([\d.]+)\s+(-?[\d,.()]+)\s+\S+$"
)

# New format fee lines (bilingual labels: "Transaction Amount 交易金額 3,850.00")
_NEW_FEE_LINE_RE = re.compile(
    r"^(Transaction Amount|Commission|Platform Fee|Trading Fee|SFC Transaction Levy|"
    r"Stamp Duty|CCASS Fee|FRC_Levy|FRC Levy|Settlement Fee|US SEC Fee|"
    r"Transaction Activity Fee|Trading System Fee)\s+(?:[\u4e00-\u9fff_]+\s+)?(-?[\d,.()]+)$"
)

# New format cash movement - description line with txn code:
# 0320000 #01501 INT MEDICAL DIV-HKD 0.27/1
# 0604000 DEBIT BALANCE INTEREST
_NEW_CASH_DESC_RE = re.compile(r"^(\d{7})\s+(.+)$")

# New format holding summary line:
# #01501 KDL MEDICAL 康德萊醫械 400 HKD 24.9500 9,980.00
# Also accepts alphabetic codes: #CHA Chagee Holdings Ltd. 霸王茶姬 100 USD 38.50 3,850.00
_NEW_HOLDING_RE = re.compile(r"^#(\w{2,10})\s+(.+?)(?:\s+[\d,]+\s+(?:HKD|USD|CNY))")


def _extract_code_from_text(text: str) -> str:
    """Extract stock code from text. Accepts both numeric (#01501) and alphabetic (#CHA) codes."""
    m = re.search(r"#(\w{2,10})", text)
    return m.group(1) if m else ""


def _is_old_section_header(line: str) -> bool:
    headers = {
        "綜合月結單", "綜合日結單",
        "資金變動",
        "證券變動",
        "買賣合約",
        "未結算交易",
        "投資總結",
        "孖展保證",
        "融资保證",
        "利率總結",
        "利息總結",
        "重要提示",
    }
    return line.strip() in headers


def _process_cash_txn(result: ParsedStatement, txn_code: str, trade_date: str,
                      description: str, amount: float, currency: str, market: str,
                      source_file: str, page_num: int, line: str):
    """Process a cash movement transaction and add to appropriate record list."""
    if txn_code == "0320000":
        code = _extract_code_from_text(description)
        result.incomes.append(IncomeRecord(
            broker="huasheng", market=market, currency=currency, date=trade_date,
            code=code, name="", category="股息/分派", amount=abs(amount),
            tax_withheld=None, fee=None, source_file=source_file,
            source_page=page_num or None, source_row=None, raw_text=line))
    elif txn_code in ("0320010", "0320030"):
        code = _extract_code_from_text(description)
        result.incomes.append(IncomeRecord(
            broker="huasheng", market=market, currency=currency, date=trade_date,
            code=code, name="", category="税费扣减", amount=amount,
            tax_withheld=None, fee=None, source_file=source_file,
            source_page=page_num or None, source_row=None, raw_text=line))
    elif txn_code == "0604000":
        result.financing_interests.append(FinancingInterestRecord(
            broker="huasheng", market=market, currency=currency, date=trade_date,
            amount=amount, source_file=source_file,
            source_page=page_num or None, source_row=None, raw_text=line))


# ---- Old format parser (202104-202208) ----

def _parse_old_format(full_text: str, source_file: str) -> ParsedStatement:
    result = ParsedStatement(source_file=source_file)
    norm_text = normalize_text(full_text)
    lines = norm_text.split("\n")

    if "綜合日結單" in norm_text:
        result.statement_type = "daily"
    elif "綜合月結單" in norm_text:
        result.statement_type = "monthly"

    m = _OLD_STMT_DATE_RE.search(norm_text)
    if m:
        result.statement_date = m.group(1)
        if m.group(2):
            result.statement_date_end = m.group(2)

    section = None
    current_currency = "HKD"
    current_market = "HK"
    pending_trade: dict | None = None
    pending_fees: dict[str, float] = {}

    def flush_trade():
        nonlocal pending_trade, pending_fees
        if not pending_trade:
            pending_fees = {}
            return
        fee_labels = ("經紀佣金", "股票印花稅", "交易費", "交易徵費", "平台使用費", "中央結算費", "交易系统使用费", "財匯局交易徵費")
        fee_total = sum(abs(pending_fees[k]) for k in fee_labels if k in pending_fees)
        side = "BUY" if pending_trade["side_raw"] in ("買", "买") else "SELL"
        result.trades.append(TradeRecord(
            broker="huasheng", market=pending_trade["market"], currency=pending_trade["currency"],
            code=pending_trade["code"], name=to_simplified(pending_trade["name"]), side=side,
            trade_date=pending_trade["trade_date"], settle_date=pending_trade["settle_date"],
            order_id=pending_trade["order_id"], trade_id=None,
            quantity=pending_trade["quantity"], price=pending_trade["price"],
            gross_amount=pending_trade["gross_amount"], fee_total=abs(fee_total),
            source_file=source_file, source_page=pending_trade.get("page"),
            source_row=None, raw_text=pending_trade.get("raw", ""),
        ))
        pending_trade = None
        pending_fees = {}

    page_num = 0
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("===PAGE"):
            try:
                page_num = int(re.search(r"\d+", line).group())
            except (AttributeError, ValueError):
                pass
            continue

        if _is_old_section_header(line):
            new_section = line.strip()
            if section == "買賣合約" and new_section != "買賣合約":
                flush_trade()
            section = new_section
            continue

        if "以下交易由華盛資本" in line or "以下交易由华盛资本" in line:
            section = "買賣合約"
            continue
        if line.startswith("貨幣：香港聯合交易所") or line.startswith("貨幣：香港联合交易所"):
            current_currency = "HKD"
            current_market = "HK"
            continue
        if line.startswith("貨幣：美國") or line.startswith("貨幣：美国"):
            current_currency = "USD"
            current_market = "US"
            continue
        if line.startswith("貨幣: 港元") or line.startswith("貨幣:港元"):
            current_currency = "HKD"
            current_market = "HK"
            continue
        if line.startswith("貨幣: 人民幣") or line.startswith("貨幣:人民币"):
            current_currency = "CNY"
            current_market = "HK"
            continue

        if "交易日期" in line and "結算日期" in line:
            continue
        if "股票代號與名稱" in line and ("買/賣" in line or "收市價" in line or "现有股数" in line):
            continue
        if "貨幣" in line and "當日融资利息" in line:
            continue
        if "日期" in line and "存款利率" in line:
            continue
        if "股數" in line and "股份代號及名稱" in line:
            continue
        if "交易日期" in line and "參考編號" in line and "項目" in line:
            continue

        if section == "買賣合約":
            m = _OLD_TRADE_LINE_RE.match(line)
            if m:
                if pending_trade:
                    flush_trade()
                pending_trade = {
                    "trade_date": m.group(1), "settle_date": m.group(2),
                    "order_id": m.group(3), "side_raw": m.group(4),
                    "code": m.group(5), "name": m.group(6).strip(),
                    "quantity": parse_number(m.group(7)), "price": parse_number(m.group(8)),
                    "gross_amount": abs(parse_number(m.group(9))),
                    "currency": current_currency, "market": current_market,
                    "page": page_num or None, "raw": line,
                }
                continue
            m = _OLD_FEE_LINE_RE.match(line)
            if m and pending_trade:
                label = m.group(1)
                val = parse_number(m.group(2))
                pending_fees[label] = val
                if label == "交易金額" and pending_trade:
                    pending_trade["gross_amount"] = abs(val)
                continue
            if pending_trade and not re.match(r"^[\d,.\s]+$", line):
                flush_trade()

        if section == "資金變動":
            if "承前結餘" in line or "轉後結餘" in line or "承前结余" in line or "转后结余" in line:
                continue
            if "相等於港元" in line:
                continue
            m = _OLD_CASH_TXN_RE.match(line)
            if m:
                trade_date = m.group(1)
                txn_code = m.group(4)
                description = m.group(5)
                amount = parse_number(m.group(6))
                _process_cash_txn(result, txn_code, trade_date, description, amount,
                                  current_currency, current_market, source_file, page_num, line)
                continue

        if section == "投資總結":
            m = re.match(r"^#(\d{4,6})\s+(.+?)\s+[\d,]+\s", line)
            if m:
                code = m.group(1)
                name = to_simplified(m.group(2).strip())
                if not hasattr(result, '_security_names'):
                    result._security_names = {}
                result._security_names[code] = name

    flush_trade()
    return result


# ---- New format parser (202209+) ----

def _parse_new_format(full_text: str, source_file: str) -> ParsedStatement:
    result = ParsedStatement(source_file=source_file)
    norm_text = normalize_text(full_text)
    lines = norm_text.split("\n")

    result.statement_type = "monthly"

    m = _NEW_STMT_DATE_RE.search(norm_text)
    if m:
        result.statement_date = m.group(1)

    section = None
    current_currency = "HKD"
    current_market = "HK"

    pending_trade: dict | None = None
    pending_fees: dict[str, float] = {}

    # Stock header context for US trades (code/name on separate header line)
    current_stock_code: str = ""
    current_stock_name_parts: list[str] = []

    # Cash movement state - tracks the last seen description line with txn code
    last_cash_desc_code: str = ""
    last_cash_desc_text: str = ""

    def flush_trade():
        nonlocal pending_trade, pending_fees
        if not pending_trade:
            pending_fees = {}
            return
        gross = pending_trade["gross_amount"]
        if "Transaction Amount" in pending_fees:
            gross = abs(pending_fees["Transaction Amount"])
        fee_labels = ("Commission", "Platform Fee", "Trading Fee", "SFC Transaction Levy",
                      "Stamp Duty", "CCASS Fee", "FRC_Levy", "FRC Levy", "Settlement Fee",
                      "US SEC Fee", "Transaction Activity Fee", "Trading System Fee")
        fee_total = sum(abs(pending_fees[k]) for k in fee_labels if k in pending_fees)
        side = "BUY" if pending_trade["side_raw"] == "Buy" else "SELL"
        result.trades.append(TradeRecord(
            broker="huasheng", market=pending_trade["market"], currency=pending_trade["currency"],
            code=pending_trade["code"], name=to_simplified(pending_trade["name"]), side=side,
            trade_date=pending_trade["trade_date"], settle_date=pending_trade["settle_date"],
            order_id=pending_trade["order_id"], trade_id=None,
            quantity=pending_trade["quantity"], price=pending_trade["price"],
            gross_amount=gross, fee_total=abs(fee_total),
            source_file=source_file, source_page=pending_trade.get("page"),
            source_row=None, raw_text=pending_trade.get("raw", ""),
        ))
        pending_trade = None
        pending_fees = {}

    page_num = 0
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("===PAGE"):
            try:
                page_num = int(re.search(r"\d+", line).group())
            except (AttributeError, ValueError):
                pass
            continue

        upper_line = line.upper()
        # Detect new format sections
        if "CASH MOVEMENT" in upper_line and ("資金" in line or "资金" in line):
            if pending_trade:
                flush_trade()
            section = "CASH_MOVEMENT"
            continue
        if "TRADING SUMMARY" in upper_line and ("交易" in line):
            section = "TRADING_SUMMARY"
            continue
        if "SETTLED TRADES" in upper_line and ("結算" in line or "结算" in line):
            if pending_trade:
                flush_trade()
            section = "SETTLED_TRADES"
            continue
        if "HOLDING SUMMARY" in upper_line and ("持倉" in line or "持仓" in line):
            if pending_trade:
                flush_trade()
            section = "HOLDING_SUMMARY"
            continue
        if "IMPORTANT NOTICE" in upper_line and "重要提" in line:
            if pending_trade:
                flush_trade()
            section = "IMPORTANT"
            continue
        if "INTEREST RATE" in upper_line and "利率" in line:
            if pending_trade:
                flush_trade()
            section = "INTEREST_RATE"
            continue

        # Skip column headers and page markers
        if "Trade Date" in line and "Settlement" in line:
            continue
        if "Items" in line and "Exchange" in line and "Units" in line:
            continue
        if "Date" in line and "Reference" in line and ("Amount" in line or "(Debit)" in line):
            continue
        if line.startswith("Page ") and "of" in line:
            continue
        if "CCY Total" in line:
            continue
        if "以下交易由" in line:
            continue

        # ---- TRADING SUMMARY section ----
        if section == "TRADING_SUMMARY":
            # Track market section headers
            if "HK Market" in line or ("港股" in line and "Market" in line):
                current_market = "HK"
                continue
            if "US Market" in line or ("美股" in line and "Market" in line):
                current_market = "US"
                continue

            # Check for stock header line (#code Name) - but not if it's a trade line
            is_hk_trade = bool(_NEW_HK_TRADE_LINE_RE.match(line))
            is_us_trade = bool(_NEW_US_TRADE_LINE_RE.match(line))
            if not is_hk_trade and not is_us_trade:
                m = _NEW_STOCK_HEADER_RE.match(line)
                if m:
                    # New stock header - flush any pending trade
                    if pending_trade:
                        flush_trade()
                    current_stock_code = m.group(1)
                    current_stock_name_parts = [m.group(2).strip()]
                    continue

            # Try HK trade line (with inline #code and HKEX)
            m = _NEW_HK_TRADE_LINE_RE.match(line)
            if m:
                if pending_trade:
                    flush_trade()
                code = m.group(5)
                name = m.group(6).strip()
                currency = m.group(7)
                pending_trade = {
                    "trade_date": m.group(1), "settle_date": m.group(2),
                    "order_id": m.group(3), "side_raw": m.group(4),
                    "code": code, "name": name,
                    "quantity": parse_number(m.group(8)), "price": parse_number(m.group(9)),
                    "gross_amount": abs(parse_number(m.group(10))),
                    "currency": currency, "market": "HK",
                    "page": page_num or None, "raw": line,
                }
                current_stock_code = code
                current_stock_name_parts = [name]
                continue

            # Try US trade line (without #code, venue code + currency)
            m = _NEW_US_TRADE_LINE_RE.match(line)
            if m:
                if pending_trade:
                    flush_trade()
                currency = m.group(6)
                # Use stock info from header
                code = current_stock_code
                name = "".join(current_stock_name_parts)
                pending_trade = {
                    "trade_date": m.group(1), "settle_date": m.group(2),
                    "order_id": m.group(3), "side_raw": m.group(4),
                    "code": code, "name": name,
                    "quantity": parse_number(m.group(7)), "price": parse_number(m.group(8)),
                    "gross_amount": abs(parse_number(m.group(9))),
                    "currency": currency, "market": "US",
                    "page": page_num or None, "raw": line,
                }
                continue

            # Check for fee line (bilingual labels)
            m = _NEW_FEE_LINE_RE.match(line)
            if m and pending_trade:
                label = m.group(1)
                val = parse_number(m.group(2))
                pending_fees[label] = val
                continue

            # Name continuation for US trades (e.g., "茶姬" after trade line)
            if pending_trade and current_stock_name_parts:
                if re.match(r"^[\u4e00-\u9fffA-Za-z\s\.\-]+$", line) and len(line) <= 20:
                    current_stock_name_parts.append(line)
                    pending_trade["name"] = "".join(current_stock_name_parts)
                    continue

            # Flush if line doesn't match any pattern
            if pending_trade and line and not re.match(r"^[\d,.\s()]+$", line):
                if not _NEW_FEE_LINE_RE.match(line):
                    flush_trade()

        # ---- SETTLED TRADES section (skip - duplicates TRADING SUMMARY) ----

        # ---- CASH MOVEMENT section ----
        if section == "CASH_MOVEMENT":
            if "Previous Balance" in line or "Closing Balance" in line:
                continue
            if "承前結餘" in line or "轉後結餘" in line or "承前结余" in line or "转后结余" in line:
                continue
            if "Currency Equivalent" in line or "參考貨幣" in line:
                continue

            # Currency indicator lines: "USD美元", "HKD港元", "CNY人民幣"
            if line in ("USD美元", "USD 美元", "HKD港元", "HKD 港元", "CNY人民幣", "CNY 人民幣"):
                if "USD" in line:
                    current_currency = "USD"
                    current_market = "US"
                elif "HKD" in line:
                    current_currency = "HKD"
                    current_market = "HK"
                elif "CNY" in line:
                    current_currency = "CNY"
                    current_market = "HK"
                continue

            # BUY/SELL header in cash movement (for trade settlement)
            if line.startswith("BUY #") or line.startswith("SELL #"):
                continue

            # Price line: "100 @38.5000"
            if re.match(r"^[\d,]+\s+@[\d.]+$", line):
                continue

            # Balance line: "10,000.00 77,579.00"
            if re.match(r"^[\d,.()]+\s+[\d,.()]+$", line):
                continue

            # Single date line (part of cash movement BUY/SELL block)
            if re.match(r"^\d{4}-\d{2}-\d{2}$", line):
                continue

            # Description line with txn code (comes before date line)
            m = _NEW_CASH_DESC_RE.match(line)
            if m:
                last_cash_desc_code = m.group(1)
                last_cash_desc_text = m.group(2).strip()
                continue

            # Date line with txn code: 2025-04-30 2025-04-30 100123020 0604000 DEBIT BALANCE INTEREST (0.36)
            m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+(\d+)\s+(\d{7})\s+(.+?)\s+(-?[\d,.()]+)$", line)
            if m:
                trade_date = m.group(1)
                txn_code = m.group(4)
                description = m.group(5)
                amount = parse_number(m.group(6))
                _process_cash_txn(result, txn_code, trade_date, description, amount,
                                  current_currency, current_market, source_file, page_num, line)
                last_cash_desc_code = ""
                last_cash_desc_text = ""
                continue

            # Date line without txn code (use last seen description):
            # 2023-06-29 2023-06-29 71588438 97.20
            m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+(\d+)\s+(-?[\d,.()]+)$", line)
            if m and last_cash_desc_code:
                trade_date = m.group(1)
                amount = parse_number(m.group(4))
                _process_cash_txn(result, last_cash_desc_code, trade_date,
                                  last_cash_desc_text, amount,
                                  current_currency, current_market, source_file, page_num, line)
                last_cash_desc_code = ""
                last_cash_desc_text = ""
                continue

            # Trade settlement line (long ref number): skip to avoid duplicate trades
            m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+(\d{10,})\s+(-?[\d,.()]+)$", line)
            if m:
                continue

        # ---- HOLDING SUMMARY section ----
        if section == "HOLDING_SUMMARY":
            m = _NEW_HOLDING_RE.match(line)
            if m:
                code = m.group(1)
                name = to_simplified(m.group(2).strip())
                if not hasattr(result, '_security_names'):
                    result._security_names = {}
                result._security_names[code] = name

    flush_trade()
    return result


# ---- Post-processing ----

def _backfill_names_from_positions(statements: list[ParsedStatement]):
    """Build security master from positions, trades, and holdings; backfill all records.

    The name map is keyed by code only (codes are unique within a broker's account).
    Sources of names (in priority order):
      1. Position/holding sections (_security_names) - most reliable
      2. Trade records - names from trade lines
      3. Income records - names already attached to income entries
    """
    name_map: dict[str, str] = {}
    # Source 1: position/holding sections
    for stmt in statements:
        names = getattr(stmt, '_security_names', {})
        for code, name in names.items():
            if code not in name_map and name:
                name_map[code] = name
    # Source 2: trade records
    for stmt in statements:
        for t in stmt.trades:
            if t.name and t.code and t.code not in name_map:
                name_map[t.code] = t.name
    # Source 3: income records (already attached names)
    for stmt in statements:
        for inc in stmt.incomes:
            if inc.name and inc.code and inc.code not in name_map:
                name_map[inc.code] = inc.name

    # Backfill all records
    for stmt in statements:
        for t in stmt.trades:
            if not t.name and t.code in name_map:
                t.name = name_map[t.code]
        for inc in stmt.incomes:
            if not inc.name and inc.code in name_map:
                inc.name = name_map[inc.code]


def _deduplicate_incomes(statements: list[ParsedStatement]):
    seen = set()
    for stmt in statements:
        unique = []
        for inc in stmt.incomes:
            key = (inc.date, inc.code, inc.category, inc.amount, inc.currency)
            if key not in seen:
                seen.add(key)
                unique.append(inc)
        stmt.incomes = unique


def _deduplicate_financing(statements: list[ParsedStatement]):
    seen = set()
    for stmt in statements:
        unique = []
        for fin in stmt.financing_interests:
            key = (fin.date, fin.amount, fin.currency)
            if key not in seen:
                seen.add(key)
                unique.append(fin)
        stmt.financing_interests = unique


# ---- Main parsing entry point ----

def parse_huasheng_pdf(path: Path, password: str = "") -> ParsedStatement:
    """Parse a single 华盛通 PDF file. Auto-detects old vs new format."""
    full_text = ""
    with pdfplumber.open(str(path), password=password) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            full_text += f"\n===PAGE {page.page_number}===\n" + page_text

    norm_text = normalize_text(full_text)

    if "MONTHLY STATEMENT" in norm_text or "FINANCIAL OVERVIEW" in norm_text or "Statement As At" in norm_text:
        return _parse_new_format(full_text, path.name)
    else:
        return _parse_old_format(full_text, path.name)


def parse_all_pdfs(root: Path, password: str = "") -> tuple[list[ParsedStatement], list[dict]]:
    """Parse all PDF files under root. Returns (statements, errors)."""
    statements = []
    errors = []
    for pdf_path in sorted(root.rglob("*.pdf")):
        try:
            stmt = parse_huasheng_pdf(pdf_path, password)
            if not stmt.trades and not stmt.incomes and not stmt.financing_interests and not stmt.statement_type:
                errors.append({"file": pdf_path.name, "error": "无法解析（可能为加密文件或空内容）"})
            else:
                statements.append(stmt)
        except Exception as e:
            errors.append({"file": pdf_path.name, "error": str(e)})

    _backfill_names_from_positions(statements)
    _deduplicate_incomes(statements)
    _deduplicate_financing(statements)

    return statements, errors
