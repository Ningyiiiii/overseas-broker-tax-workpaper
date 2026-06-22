from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import pdfplumber


ROOT = Path("D:/VIBE CODING/LLB/\u5bcc\u9014\u8001\u6731")
PASSWORD = "20162270"
OUT_DIR = Path("D:/VIBE CODING/WHATEVER/outputs/futu_tax")
OUT_JSON = OUT_DIR / "futu_tax_data.json"

BUY = "\u8cb7\u5165"
SELL = "\u8ce3\u51fa"
BUY_OPEN = "\u8cb7\u5165\u958b\u5009"
SELL_CLOSE = "\u8ce3\u51fa\u5e73\u5009"

MONEY_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")
FEE_RE = re.compile(
    r"(?P<label>[\u4e00-\u9fffA-Za-z]+(?:\u7528\u8cbb|\u5fb5\u8cbb|\u4f63\u91d1|\u5370\u82b1\u7a05|\u4ea4\u6536\u8cbb|\u5c0f\u8a08))[:\uff1a]\s*(?P<amount>[-+]?\d[\d,]*\.\d{2})"
)
SUBTOTAL_RE = re.compile(r"\u5c0f\u8a08[:\uff1a]\s*([-+]?\d[\d,]*\.\d{2})")
EXEC_RE = re.compile(
    r"\b(?P<market>SEHK|FUTU OTC|NYSE|NASDAQ|AMEX|US)\s+"
    r"(?P<currency>[A-Z]{3})\s+"
    r"(?P<trade_date>\d{4}/\d{2}/\d{2})\s+"
    r"(?P<settle_date>\d{4}/\d{2}/\d{2})\s+"
    r"(?P<qty>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
    r"(?P<price>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
    r"(?P<amount>[-+]?\d[\d,]*\.\d{2})\s+"
    r"(?P<change>[-+]?\d[\d,]*\.\d{2})\s*"
    r"(?P<time>\d{1,2}:\d{2}:\d{2})?"
)


@dataclass
class Trade:
    source_file: str
    page: int
    format: str
    side: str
    order_id: str
    code: str
    name: str
    market: str
    currency: str
    trade_datetime: str
    settle_date: str
    quantity: str
    price: str
    amount: str
    change_amount: str
    fee_total: str
    fee_detail: dict[str, str]
    raw: str
    notes: str = ""


def d(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    s = str(value).strip().replace(",", "")
    if not s or s in {"-", "--"}:
        return Decimal("0")
    return Decimal(s)


def money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def qty_str(value: Decimal) -> str:
    q = value.normalize()
    return format(q, "f")


def norm_text(s: str | None) -> str:
    if not s:
        return ""
    s = s.replace("\u3000", " ").replace("\xa0", " ")
    # New-format statements often render Chinese/ascii glyphs twice. Do not
    # collapse digits because account numbers and amounts can be damaged.
    for _ in range(3):
        s = re.sub(r"([\u4e00-\u9fffA-Za-z])\1", r"\1", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def clean_cell(v: Any) -> str:
    return norm_text(str(v or "").replace("\n", " "))


def normalize_security_name(value: str) -> str:
    value = clean_cell(value)
    if re.search(r"[\u4e00-\u9fff]", value):
        value = re.sub(r"\s+", "", value)
    return value


def parse_code_name(value: str) -> tuple[str, str]:
    value = clean_cell(value)
    m = re.match(r"([0-9A-Z]{3,})\s*\((.*?)\)", value)
    if m:
        return m.group(1), normalize_security_name(m.group(2))
    m = re.match(r"([0-9A-Z]{3,})\s*\((.*)$", value)
    if m:
        return m.group(1), normalize_security_name(m.group(2))
    parts = value.split(None, 1)
    if parts:
        return parts[0], normalize_security_name(parts[1]) if len(parts) > 1 else ""
    return "", ""


def complete_new_format_name(code: str, current_name: str, currency: str, lines: list[str]) -> str:
    """Recover names wrapped inside the code(name) cell of new statements."""
    text = " / ".join(lines[:10])
    m = re.search(rf"{re.escape(code)}\((.*?)\)", text)
    if not m:
        return normalize_security_name(current_name)
    content = m.group(1)
    parts = [p.strip() for p in content.split(" / ") if p.strip()]
    if not parts:
        return normalize_security_name(current_name)
    head = parts[0].split(f" {currency} ", 1)[0].strip()
    tail = "".join(
        p
        for p in parts[1:]
        if not re.search(r"\d{4}/\d{2}/\d{2}|^\d|製備日期|客戶姓名|賬戶號碼", p)
    )
    recovered = normalize_security_name(head + tail)
    if len(recovered) >= len(normalize_security_name(current_name)):
        return recovered
    return normalize_security_name(current_name)


def parse_fee_blob(value: str) -> tuple[Decimal, dict[str, str]]:
    text = norm_text(value)
    detail: dict[str, str] = {}
    for m in FEE_RE.finditer(text):
        detail[m.group("label")] = money(d(m.group("amount")))
    subtotal = Decimal("0")
    sub = SUBTOTAL_RE.search(text)
    if sub:
        subtotal = d(sub.group(1))
    elif detail:
        subtotal = sum((d(v) for k, v in detail.items() if k != "\u5c0f\u8a08"), Decimal("0"))
    else:
        nums = MONEY_RE.findall(text)
        if nums:
            subtotal = d(nums[-1])
    return subtotal, detail


def parse_old_tables(pdf: pdfplumber.PDF, rel: str) -> list[Trade]:
    trades: list[Trade] = []
    pending: Trade | None = None
    for page_index, page in enumerate(pdf.pages, start=1):
        tables = page.extract_tables() or []
        for table in tables:
            for row in table:
                cells = [clean_cell(c) for c in row]
                if not any(cells):
                    continue
                side = cells[0]
                if side in {BUY, SELL} and len(cells) >= 8:
                    code, name = parse_code_name(cells[2])
                    pending = Trade(
                        source_file=rel,
                        page=page_index,
                        format="old",
                        side="buy" if side == BUY else "sell",
                        order_id=cells[1],
                        code=code,
                        name=name,
                        market="",
                        currency="HKD",
                        trade_datetime=cells[3],
                        settle_date="",
                        quantity=qty_str(d(cells[4])),
                        price=str(d(cells[5])),
                        amount=money(d(cells[6])),
                        change_amount=money(d(cells[7])),
                        fee_total="0.00",
                        fee_detail={},
                        raw=" | ".join(cells),
                    )
                    trades.append(pending)
                    continue
                if pending and cells[0] and any(
                    label in cells[0]
                    for label in [
                        "\u4f63\u91d1",
                        "\u5370\u82b1\u7a05",
                        "\u5e73\u53f0\u4f7f\u7528\u8cbb",
                        "\u4ea4\u6613\u8cbb",
                        "\u4ea4\u6613\u5fb5\u8cbb",
                        "\u5c0f\u8a08",
                    ]
                ):
                    fee_total, fee_detail = parse_fee_blob(cells[0])
                    trade_amount = d(pending.amount)
                    # Guard against pdfplumber joining a fee-label row with
                    # unrelated account/portfolio tables later on the page.
                    if fee_total < 0 or abs(fee_total) > max(Decimal("1000"), trade_amount * Decimal("0.10")):
                        fee_total = Decimal("0")
                        fee_detail = {}
                        pending.notes = (pending.notes + "; " if pending.notes else "") + "ignored suspicious fee parse"
                    if fee_total:
                        pending.fee_total = money(fee_total)
                        pending.fee_detail = fee_detail
                    market = re.search(r"\u5e02\u5834[:\uff1a]\s*([A-Z ]+)", cells[0])
                    settle = re.search(r"\u4ea4\u6536\u65e5[:\uff1a]\s*(\d{4}/\d{2}/\d{2})", cells[0])
                    if market:
                        pending.market = market.group(1).strip()
                    if settle:
                        pending.settle_date = settle.group(1)
    return trades


def trade_header_from_new_line(line: str) -> tuple[str, str, str, Decimal, Decimal, Decimal, Decimal] | None:
    line = norm_text(line)
    side_options = [BUY_OPEN, SELL_CLOSE, BUY, SELL]
    side = next((x for x in side_options if line.startswith(x)), "")
    if not side:
        return None
    rest = line[len(side) :].strip()
    # New statements often wrap the security name after the first few Chinese
    # characters, so the header line can be "03939(萬國黃 HKD ..." without a
    # closing parenthesis. Treat the currency token as the reliable delimiter.
    code_match = re.match(
        r"(?P<code_name>[0-9A-Z]{3,}\([^)]*?)\s+(?P<currency>[A-Z]{3})\s+(?P<tail>.*)$",
        rest,
    )
    if not code_match:
        return None
    code_name = code_match.group("code_name")
    tail = f"{code_match.group('currency')} {code_match.group('tail')}"
    nums = MONEY_RE.findall(tail)
    if len(nums) < 4:
        return None
    currency = code_match.group("currency")
    qty, price, amount, change = nums[-4:]
    return side, code_name, currency, d(qty), d(price), d(amount), d(change)


def parse_new_text(pdf: pdfplumber.PDF, rel: str) -> list[Trade]:
    trades: list[Trade] = []
    text_pages = [(i, norm_text(page.extract_text(x_tolerance=1, y_tolerance=3) or "")) for i, page in enumerate(pdf.pages, start=1)]
    blocks: list[tuple[int, list[str]]] = []
    current: tuple[int, list[str]] | None = None
    for page_no, text in text_pages:
        for raw_line in text.splitlines():
            line = norm_text(raw_line)
            if not line:
                continue
            if trade_header_from_new_line(line):
                if current:
                    blocks.append(current)
                current = (page_no, [line])
            elif current:
                current[1].append(line)
    if current:
        blocks.append(current)

    for idx, (page_no, lines) in enumerate(blocks, start=1):
        header = trade_header_from_new_line(lines[0])
        if not header:
            continue
        side_text, code_name, currency, total_qty, total_price, total_amount, total_change = header
        code, name = parse_code_name(code_name)
        name = complete_new_format_name(code, name, currency, lines)
        execs = [m.groupdict() for line in lines[1:] for m in EXEC_RE.finditer(line)]
        fee_blob = " ".join(lines)
        fee_total, fee_detail = parse_fee_blob(fee_blob)
        side = "buy" if BUY in side_text else "sell"
        order_id = f"{rel}#new#{idx}"
        if execs:
            exec_qty = sum((d(e["qty"]) for e in execs), Decimal("0"))
            # Allocate the summary fee across executions. If extraction found
            # extra lines from the previous block, keep the summary as a single
            # transaction and flag it.
            if exec_qty == total_qty:
                for fill_index, e in enumerate(execs, start=1):
                    q = d(e["qty"])
                    ratio = q / total_qty if total_qty else Decimal("0")
                    trades.append(
                        Trade(
                            source_file=rel,
                            page=page_no,
                            format="new",
                            side=side,
                            order_id=f"{order_id}.{fill_index}",
                            code=code,
                            name=name,
                            market=e["market"],
                            currency=e["currency"],
                            trade_datetime=f"{e['trade_date']} {e.get('time') or '00:00:00'}",
                            settle_date=e["settle_date"],
                            quantity=qty_str(q),
                            price=str(d(e["price"])),
                            amount=money(d(e["amount"])),
                            change_amount=money(d(e["change"])),
                            fee_total=money(fee_total * ratio),
                            fee_detail={k: money(d(v) * ratio) for k, v in fee_detail.items()},
                            raw=" / ".join(lines[:6]),
                        )
                    )
                continue
        # Fallback to summary line when execution parsing is incomplete or
        # unavailable, which is common for US trades in these statements.
        date_match = re.search(r"(\d{4}/\d{2}/\d{2})", " ".join(lines))
        trade_date = date_match.group(1) if date_match else ""
        trades.append(
            Trade(
                source_file=rel,
                page=page_no,
                format="new",
                side=side,
                order_id=order_id,
                code=code,
                name=name,
                market="SEHK",
                currency=currency,
                trade_datetime=f"{trade_date} 00:00:00" if trade_date else "",
                settle_date="",
                quantity=qty_str(total_qty),
                price=str(total_price),
                amount=money(total_amount),
                change_amount=money(total_change),
                fee_total=money(fee_total),
                fee_detail=fee_detail,
                raw=" / ".join(lines[:8]),
                notes="new-format execution lines did not reconcile to summary" if execs else "",
            )
        )
    return trades


def extract_cash_items(pdf: pdfplumber.PDF, rel: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    income: list[dict[str, Any]] = []
    financing: list[dict[str, Any]] = []
    corporate_action_re = re.compile(
        r"(?P<date>\d{4}/\d{2}/\d{2})\s+"
        r"(?P<direction>\u589e\u52a0|\u6e1b\u5c11)\s+"
        r"\u516c\u53f8\u884c\u52d5\s+"
        r"(?P<currency>[A-Z]{3})\s+"
        r"(?P<amount>[-+]?\d[\d,]*\.\d{2})\s+"
        r"(?P<description>.*)$"
    )
    interest_for_month_re = re.compile(
        r"(?P<date>\d{4}/\d{2}/\d{2}).*?(?P<amount>[-+]?\d[\d,]*\.\d{2}).*?Interest for Month"
    )
    date_re = re.compile(r"(\d{4}/\d{2}/\d{2})")
    for page_no, page in enumerate(pdf.pages, start=1):
        text = norm_text(page.extract_text(x_tolerance=1, y_tolerance=3) or "")
        for line in text.splitlines():
            line = norm_text(line)
            if not line:
                continue
            finance_match = interest_for_month_re.search(line)
            if finance_match:
                financing.append(
                    {
                        "source_file": rel,
                        "page": page_no,
                        "date": finance_match.group("date"),
                        "currency": "HKD",
                        "amount": finance_match.group("amount"),
                        "description": line,
                        "category": "融资利息",
                    }
                )
                continue
            action_match = corporate_action_re.search(line)
            if action_match:
                desc = action_match.group("description")
                category = "公司行动收入" if action_match.group("direction") == "\u589e\u52a0" else "公司行动费用"
                if any(term in desc for term in ["DISTRIBUTION", "DIST", "DIV", "\u80a1\u606f", "\u6d3e\u606f", "\u5206\u6d3e"]):
                    category = "股息/分派" if action_match.group("direction") == "\u589e\u52a0" else "股息/分派相关费用"
                income.append(
                    {
                        "source_file": rel,
                        "page": page_no,
                        "date": action_match.group("date"),
                        "currency": action_match.group("currency"),
                        "amount": action_match.group("amount"),
                        "description": desc,
                        "category": category,
                    }
                )
                continue
            # Keep a very narrow fallback for explicit dividend/interest cash
            # lines with a date and amount. Exclude legal-disclaimer paragraphs.
            if not date_re.search(line) or line.startswith(("2.", "3.", "4.")):
                continue
            if any(term in line for term in ["Dividend", "\u80a1\u606f", "\u6d3e\u606f"]):
                nums = MONEY_RE.findall(line)
                item = {
                "source_file": rel,
                "page": page_no,
                    "date": date_re.search(line).group(1),
                    "currency": "",
                    "amount": nums[-1] if nums else "",
                "description": line,
                    "category": "股息/利息候选",
                }
                income.append(item)
    return income, financing


def statement_period_from_rel(rel: str) -> str:
    matches = re.findall(r"20\d{4}(?:\d{2})?", rel)
    return matches[-1][:6] if matches else ""


def fix_partial_statement_date(value: str, rel: str) -> str:
    value = clean_cell(value)
    if re.match(r"\d{4}/\d{2}/\d{2}$", value):
        return value
    if re.match(r"/\d{2}/\d{2}$", value):
        period = statement_period_from_rel(rel)
        if len(period) == 6:
            return f"{period[:4]}{value}"
    return value


def classify_cash_action(description: str, direction: str) -> str:
    desc = description.upper()
    if "FUND SUBSCRIPTION#" in desc or "FUND REDEMPTION#" in desc:
        return ""
    if "INTEREST FOR MONTH" in desc:
        return ""
    if "HANDLING CHARGE" in desc or "SCRIP CHARGE" in desc or re.search(r"\bCA FEE\b|\bHANDLING FEE\b", desc):
        return "公司行动费用"
    if re.search(r"\b(?:\d{2}(?:/\d{2})?\s*)?(?:F/D|I/D)\b", desc):
        return "股息/分派"
    if re.search(r"\b(?:FINAL|INTERIM|INT|SPECIAL)\s+(?:DIS|DIST|DISTRIBUTION)\b|\bDISTRIBUTION\b|\bDIVIDEND\b", desc):
        return "股息/分派"
    if "SUBSCRIPTION RIGHTS" in desc or "RIGHTS" in desc or "ENTITLEMENT" in desc:
        return "其他公司行动"
    if any(term in description for term in ["股息", "派息", "分派"]):
        return "股息/分派"
    return ""


def extract_cash_items_v2(pdf: pdfplumber.PDF, rel: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    income: list[dict[str, Any]] = []
    financing: list[dict[str, Any]] = []
    seen_income: set[tuple[str, str, str, str, int]] = set()
    seen_financing: set[tuple[str, str, str, int]] = set()

    def add_income(page_no: int, date_value: str, direction: str, currency: str, amount: str, description: str) -> None:
        desc = clean_cell(description)
        category = classify_cash_action(desc, direction)
        if not category:
            return
        item = {
            "source_file": rel,
            "page": page_no,
            "date": fix_partial_statement_date(date_value, rel),
            "currency": currency or "HKD",
            "amount": clean_cell(amount),
            "description": desc,
            "category": category,
        }
        for idx, existing in enumerate(income):
            same_cash_line = (
                existing["date"] == item["date"]
                and existing["amount"] == item["amount"]
                and existing["source_file"] == item["source_file"]
                and existing["page"] == item["page"]
                and existing["category"] == item["category"]
            )
            if same_cash_line and (
                item["description"].startswith(existing["description"])
                or existing["description"].startswith(item["description"])
            ):
                if len(item["description"]) > len(existing["description"]):
                    old_key = (
                        existing["date"],
                        existing["amount"],
                        existing["description"],
                        existing["source_file"],
                        existing["page"],
                    )
                    seen_income.discard(old_key)
                    income[idx] = item
                    seen_income.add((item["date"], item["amount"], item["description"], item["source_file"], item["page"]))
                return
        key = (item["date"], item["amount"], item["description"], item["source_file"], item["page"])
        if key not in seen_income:
            seen_income.add(key)
            income.append(item)

    def add_financing(page_no: int, date_value: str, amount: str, description: str, currency: str = "HKD") -> None:
        item = {
            "source_file": rel,
            "page": page_no,
            "date": fix_partial_statement_date(date_value, rel),
            "currency": currency or "HKD",
            "amount": clean_cell(amount),
            "description": clean_cell(description),
            "category": "融资利息",
        }
        key = (item["date"], item["amount"], item["source_file"], item["page"])
        if key not in seen_financing:
            seen_financing.add(key)
            financing.append(item)

    corporate_action_re = re.compile(
        r"(?P<date>\d{4}/\d{2}/\d{2})\s+"
        r"(?P<direction>增加|減少)\s+"
        r"公司行動\s+"
        r"(?P<currency>[A-Z]{3})\s+"
        r"(?P<amount>[-+]?\d[\d,]*\.\d{2})\s+"
        r"(?P<description>.*)$"
    )
    interest_for_month_re = re.compile(
        r"(?P<date>\d{4}/\d{2}/\d{2}).*?(?P<amount>[-+]?\d[\d,]*\.\d{2}).*?Interest for Month"
    )
    date_re = re.compile(r"(\d{4}/\d{2}/\d{2})")
    for page_no, page in enumerate(pdf.pages, start=1):
        for table in page.extract_tables() or []:
            for row in table:
                cells = [clean_cell(c) for c in row]
                if len(cells) >= 6 and re.match(r"\d{4}/\d{2}/\d{2}$", cells[1] or "") and re.search(r"[-+]?\d[\d,]*\.\d{2}", cells[2] or ""):
                    direction = cells[0] if cells[0] in {"增加", "減少"} else ("增加" if cells[2].startswith("+") else "減少")
                    add_income(page_no, cells[1], direction, "HKD", cells[2], cells[-1])
                    continue
                if len(cells) >= 6 and "公司行動" in cells and cells[1] in {"增加", "減少"}:
                    add_income(page_no, cells[0], cells[1], cells[3], cells[4], cells[-1])
                    continue

        text = norm_text(page.extract_text(x_tolerance=1, y_tolerance=3) or "")
        for line in text.splitlines():
            line = norm_text(line)
            if not line:
                continue
            finance_match = interest_for_month_re.search(line)
            if finance_match:
                add_financing(page_no, finance_match.group("date"), finance_match.group("amount"), line)
                continue
            action_match = corporate_action_re.search(line)
            if action_match:
                add_income(
                    page_no,
                    action_match.group("date"),
                    action_match.group("direction"),
                    action_match.group("currency"),
                    action_match.group("amount"),
                    action_match.group("description"),
                )
                continue
            if not date_re.search(line) or line.startswith(("2.", "3.", "4.")):
                continue
            if any(term in line for term in ["Dividend", "股息", "派息", "分派"]):
                nums = MONEY_RE.findall(line)
                add_income(page_no, date_re.search(line).group(1), "增加", "", nums[-1] if nums else "", line)
    return income, financing


def extract_ipo_allotment_trades(pdf: pdfplumber.PDF, rel: str) -> list[Trade]:
    allotments: dict[str, dict[str, Any]] = {}
    cash_by_code: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"application": Decimal("0"), "refund": Decimal("0"), "handling": Decimal("0")})
    seen_allotments: set[tuple[str, str, str, str]] = set()
    seen_cash: set[tuple[str, str, str]] = set()

    def decimal_amounts(text: str) -> list[str]:
        return re.findall(r"[-+]?\d[\d,]*\.\d{2}", text)

    def ensure(code: str) -> dict[str, Any]:
        if code not in allotments:
            allotments[code] = {
                "code": code,
                "name": "",
                "date": "",
                "currency": "HKD",
                "quantity": Decimal("0"),
                "amount": Decimal("0"),
                "page": 0,
                "raw": [],
            }
        return allotments[code]

    def add_cash(code: str, kind: str, amount_value: str) -> None:
        amount = abs(d(amount_value))
        if code:
            key = (code, kind, money(amount))
            if key in seen_cash:
                return
            seen_cash.add(key)
            cash_by_code[code][kind] += amount

    def add_allotment(page_no: int, date_value: str, code: str, name: str, currency: str, qty_value: str, amount_value: str, raw: str) -> None:
        code = code.zfill(5) if code.isdigit() else code
        key = (fix_partial_statement_date(date_value, rel), code, qty_str(abs(d(qty_value))), money(abs(d(amount_value))))
        if key in seen_allotments:
            return
        seen_allotments.add(key)
        item = ensure(code)
        item["name"] = normalize_security_name(name) or item["name"]
        item["date"] = fix_partial_statement_date(date_value, rel) or item["date"]
        item["currency"] = currency or item["currency"]
        item["quantity"] += abs(d(qty_value))
        item["amount"] += abs(d(amount_value))
        item["page"] = item["page"] or page_no
        item["raw"].append(clean_cell(raw))

    for page_no, page in enumerate(pdf.pages, start=1):
        tables = page.extract_tables() or []
        for table in tables:
            for row in table:
                cells = [clean_cell(c) for c in row]
                joined = " | ".join(cells)
                if "IPO Application Amount" in joined or "IPO Aplication Amount" in joined:
                    m = re.search(r"#(?P<code>\d{3,5})", joined)
                    nums = decimal_amounts(joined)
                    if m and nums:
                        add_cash(m.group("code").zfill(5), "application", nums[-1])
                if "IPO Refund Amount" in joined or "Cr. - IPO Refund Amount" in joined:
                    m = re.search(r"#(?P<code>\d{3,5})", joined)
                    nums = decimal_amounts(joined)
                    if m and nums:
                        add_cash(m.group("code").zfill(5), "refund", nums[-1])
                if "IPO Application Handling Fee" in joined or "IPO Aplication Handling Fee" in joined or "IPO Aplication Handling Fe" in joined:
                    m = re.search(r"#(?P<code>\d{3,5})", joined)
                    nums = decimal_amounts(joined)
                    if m and nums:
                        add_cash(m.group("code").zfill(5), "handling", nums[-1])
                if "IPO Allotment Qty" in joined or "IPO Alotment Qty" in joined:
                    # Old statements: 存入股票 | date | 03347 泰格醫藥 | +100 | ... | IPO Allotment Qty
                    if len(cells) >= 6 and cells[0] in {"存入股票", "存入股票 "}:
                        code_name = cells[2]
                        code, name = parse_code_name(code_name)
                        add_allotment(page_no, cells[1], code, name, "HKD", cells[3], "0", joined)
                        continue
                    # New statements often split the IPO allotment row into cells:
                    # date | 增加 | 港股IPO公開發售 | 2097(蜜雪集團) | HKD | +200 | +40,500.00 | IPO Alotment Qty
                    if len(cells) >= 8 and re.match(r"\d{3,5}\(", cells[4] or ""):
                        code, name = parse_code_name(cells[4])
                        add_allotment(page_no, cells[1], code, name, cells[5], cells[6], cells[7], joined)
                        continue
                    # Text statements: date 增加 港股IPO公 2097(蜜雪集團) HKD +200 +40,500.00 IPO Allotment Qty
                    m = re.search(
                        r"(?P<date>\d{4}/\d{2}/\d{2}).*?(?P<code>\d{3,5})\((?P<name>[^)]*)\)\s+"
                        r"(?P<currency>[A-Z]{3})\s+(?P<qty>[+-]?\d[\d,]*)\s+(?P<amount>[+-]?\d[\d,]*\.\d{2}).*?IPO A?l?lotment Qty",
                        joined,
                    )
                    if m:
                        add_allotment(page_no, m.group("date"), m.group("code"), m.group("name"), m.group("currency"), m.group("qty"), m.group("amount"), joined)

        text = norm_text(page.extract_text(x_tolerance=1, y_tolerance=3) or "")
        for line in text.splitlines():
            line = norm_text(line)
            if "IPO Application Amount" in line or "IPO Aplication Amount" in line:
                m = re.search(r"#(?P<code>\d{3,5})", line)
                nums = decimal_amounts(line)
                if m and nums:
                    add_cash(m.group("code").zfill(5), "application", nums[-1])
            if "IPO Refund Amount" in line or "Cr. - IPO Refund Amount" in line:
                m = re.search(r"#(?P<code>\d{3,5})", line)
                nums = decimal_amounts(line)
                if m and nums:
                    add_cash(m.group("code").zfill(5), "refund", nums[-1])
            if "IPO Application Handling Fee" in line or "IPO Aplication Handling Fee" in line or "IPO Aplication Handling Fe" in line:
                m = re.search(r"#(?P<code>\d{3,5})", line)
                nums = decimal_amounts(line)
                if m and nums:
                    add_cash(m.group("code").zfill(5), "handling", nums[-1])
            if "IPO Allotment Qty" in line or "IPO Alotment Qty" in line:
                m = re.search(
                    r"(?P<date>\d{4}/\d{2}/\d{2}).*?(?P<code>\d{3,5})\((?P<name>[^)]*)\)\s+"
                    r"(?P<currency>[A-Z]{3})\s+(?P<qty>[+-]?\d[\d,]*)\s+(?P<amount>[+-]?\d[\d,]*\.\d{2}).*?IPO A?l?lotment Qty",
                    line,
                )
                if m:
                    add_allotment(page_no, m.group("date"), m.group("code"), m.group("name"), m.group("currency"), m.group("qty"), m.group("amount"), line)

    trades: list[Trade] = []
    for code, item in allotments.items():
        qty = item["quantity"]
        amount = item["amount"]
        if qty <= 0 or amount <= 0:
            continue
        cash = cash_by_code.get(code, {})
        application = cash.get("application", Decimal("0"))
        refund = cash.get("refund", Decimal("0"))
        handling = cash.get("handling", Decimal("0"))
        cash_cost = application - refund
        ipo_fee = max(Decimal("0"), cash_cost - amount) + handling
        trades.append(
            Trade(
                source_file=rel,
                page=item["page"],
                format="ipo",
                side="buy",
                order_id=f"{rel}#ipo#{code}",
                code=code,
                name=item["name"],
                market="SEHK",
                currency=item["currency"],
                trade_datetime=f"{item['date']} 00:00:00",
                settle_date=item["date"],
                quantity=qty_str(qty),
                price=str((amount / qty).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
                amount=money(amount),
                change_amount=money(-amount),
                fee_total=money(ipo_fee),
                fee_detail={"IPO费用": money(ipo_fee)} if ipo_fee else {},
                raw=" / ".join(item["raw"][:3]),
                notes=f"IPO allotment cost basis; application={money(application)} refund={money(refund)} handling={money(handling)}",
            )
        )
    return trades


def parse_trade_date(value: str) -> date | None:
    m = re.search(r"\d{4}/\d{2}/\d{2}", value or "")
    if not m:
        return None
    return datetime.strptime(m.group(0), "%Y/%m/%d").date()


def fiscal_year(dt: date) -> str | None:
    if date(2021, 4, 1) <= dt <= date(2022, 3, 31):
        return "FY2021-2022"
    if date(2022, 4, 1) <= dt <= date(2023, 3, 31):
        return "FY2022-2023"
    if date(2023, 4, 1) <= dt <= date(2024, 3, 31):
        return "FY2023-2024"
    if date(2024, 4, 1) <= dt <= date(2025, 3, 31):
        return "FY2024-2025"
    return None


def merge_trades(trades: list[Trade]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str, str], list[Trade]] = defaultdict(list)
    for t in trades:
        dt = parse_trade_date(t.trade_datetime)
        date_key = dt.isoformat() if dt else ""
        key = (t.side, t.code, t.name, t.currency, date_key, t.order_id.rsplit(".", 1)[0])
        grouped[key].append(t)
    merged = []
    for key, rows in grouped.items():
        rows = sorted(rows, key=lambda x: x.trade_datetime)
        qty = sum((d(r.quantity) for r in rows), Decimal("0"))
        amount = sum((d(r.amount) for r in rows), Decimal("0"))
        fee = sum((d(r.fee_total) for r in rows), Decimal("0"))
        change = sum((d(r.change_amount) for r in rows), Decimal("0"))
        price = amount / qty if qty else Decimal("0")
        first = rows[0]
        merged.append(
            {
                "side": first.side,
                "code": first.code,
                "name": first.name,
                "currency": first.currency or "HKD",
                "market": first.market,
                "trade_datetime": first.trade_datetime,
                "trade_date": parse_trade_date(first.trade_datetime).isoformat() if parse_trade_date(first.trade_datetime) else "",
                "settle_date": first.settle_date,
                "quantity": qty_str(qty),
                "price": str(price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
                "amount": money(amount),
                "fee_total": money(fee),
                "change_amount": money(change),
                "order_id": first.order_id.rsplit(".", 1)[0],
                "source_file": first.source_file,
                "source_pages": ",".join(str(r.page) for r in rows),
                "raw_count": len(rows),
                "notes": "; ".join(sorted({r.notes for r in rows if r.notes})),
            }
        )
    return sorted(merged, key=lambda x: (x["trade_date"], x["trade_datetime"], x["code"], x["side"]))


def backfill_best_security_names(trades: list[Trade]) -> None:
    best: dict[str, str] = {}
    for t in trades:
        name = normalize_security_name(t.name)
        if name and len(name) > len(best.get(t.code, "")):
            best[t.code] = name
    for t in trades:
        if best.get(t.code):
            t.name = best[t.code]


def apply_fifo(merged: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    queues: dict[tuple[str, str], deque[dict[str, Any]]] = defaultdict(deque)
    fy_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    exceptions: list[dict[str, Any]] = []

    for t in merged:
        dt = datetime.strptime(t["trade_date"], "%Y-%m-%d").date() if t["trade_date"] else None
        if not dt:
            exceptions.append({"type": "missing trade date", **t})
            continue
        key = (t["code"], t["currency"])
        qty = d(t["quantity"])
        amount = d(t["amount"])
        fee = d(t["fee_total"])
        if t["side"] == "buy":
            queues[key].append({**t, "remaining_qty": qty, "remaining_amount": amount, "remaining_fee": fee})
            continue
        if t["side"] != "sell":
            continue
        sell_remaining = qty
        fy = fiscal_year(dt)
        sell_amount_remaining = amount
        sell_fee_remaining = fee
        while sell_remaining > 0:
            if not queues[key]:
                row = {
                    "交易日期": t["trade_date"],
                    " 股票代码": t["code"],
                    "股票名称": t["name"],
                    "卖出价格": t["price"],
                    "买入价格": "",
                    "交易数量": qty_str(sell_remaining),
                    "卖出总金额": money(sell_amount_remaining),
                    "买入总金额": "",
                    "交易费用": money(sell_fee_remaining),
                    "盈亏": "",
                    "备注：买入时间": "缺买入成本",
                    "币种": t["currency"],
                    "来源": t["source_file"],
                    "异常": "缺买入成本",
                }
                if fy:
                    fy_rows[fy].append(row)
                exceptions.append({"type": "missing cost basis", **row})
                break
            lot = queues[key][0]
            lot_remaining = lot["remaining_qty"]
            use_qty = min(sell_remaining, lot_remaining)
            buy_ratio = use_qty / lot_remaining if lot_remaining else Decimal("0")
            sell_ratio = use_qty / sell_remaining if sell_remaining else Decimal("0")
            buy_amount_part = (lot["remaining_amount"] * buy_ratio).quantize(Decimal("0.00000001"))
            buy_fee_part = (lot["remaining_fee"] * buy_ratio).quantize(Decimal("0.00000001"))
            sell_amount_part = (sell_amount_remaining * sell_ratio).quantize(Decimal("0.00000001"))
            sell_fee_part = (sell_fee_remaining * sell_ratio).quantize(Decimal("0.00000001"))
            total_fee = buy_fee_part + sell_fee_part
            pnl = sell_amount_part - buy_amount_part - total_fee
            buy_price = buy_amount_part / use_qty if use_qty else Decimal("0")
            row = {
                "交易日期": t["trade_date"],
                " 股票代码": t["code"],
                "股票名称": t["name"],
                "卖出价格": t["price"],
                "买入价格": str(buy_price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
                "交易数量": qty_str(use_qty),
                "卖出总金额": money(sell_amount_part),
                "买入总金额": money(buy_amount_part),
                "交易费用": money(total_fee),
                "盈亏": money(pnl),
                "备注：买入时间": lot["trade_date"],
                "币种": t["currency"],
                "来源": f"sell:{t['source_file']} buy:{lot['source_file']}",
                "异常": t.get("notes", ""),
            }
            if fy:
                fy_rows[fy].append(row)
            lot["remaining_qty"] -= use_qty
            lot["remaining_amount"] -= buy_amount_part
            lot["remaining_fee"] -= buy_fee_part
            sell_remaining -= use_qty
            sell_amount_remaining -= sell_amount_part
            sell_fee_remaining -= sell_fee_part
            if lot["remaining_qty"] <= Decimal("0.00000001"):
                queues[key].popleft()
    return fy_rows, exceptions


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_trades: list[Trade] = []
    all_income: list[dict[str, Any]] = []
    all_financing: list[dict[str, Any]] = []
    files = sorted(ROOT.rglob("*.pdf"))
    file_summaries = []
    for pdf_path in files:
        rel = str(pdf_path.relative_to(ROOT))
        with pdfplumber.open(pdf_path, password=PASSWORD) as pdf:
            old = parse_old_tables(pdf, rel)
            new = parse_new_text(pdf, rel)
            # Prefer old tables for old statements; prefer new parser when it
            # finds the modern trading section.
            trades = new if new else old
            trades.extend(extract_ipo_allotment_trades(pdf, rel))
            income, financing = extract_cash_items_v2(pdf, rel)
            all_trades.extend(trades)
            all_income.extend(income)
            all_financing.extend(financing)
            file_summaries.append(
                {
                    "file": rel,
                    "pages": len(pdf.pages),
                    "trades": len(trades),
                    "old_trades": len(old),
                    "new_trades": len(new),
                    "income_candidates": len(income),
                    "financing_candidates": len(financing),
                }
            )

    backfill_best_security_names(all_trades)
    merged = merge_trades(all_trades)
    fy_rows, exceptions = apply_fifo(merged)
    payload = {
        "source_root": str(ROOT),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "file_summaries": file_summaries,
        "raw_trades": [asdict(t) for t in all_trades],
        "merged_trades": merged,
        "fiscal_year_rows": fy_rows,
        "income_items": all_income,
        "financing_items": all_financing,
        "exceptions": exceptions,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "files": len(files),
        "raw_trades": len(all_trades),
        "merged_trades": len(merged),
        "fy_rows": {k: len(v) for k, v in fy_rows.items()},
        "income_items": len(all_income),
        "financing_items": len(all_financing),
        "exceptions": len(exceptions),
        "output": str(OUT_JSON),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
