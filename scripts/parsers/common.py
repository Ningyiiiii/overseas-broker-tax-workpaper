"""Common parsing utilities shared by all broker parsers.

This module provides broker-agnostic capabilities that every parser needs:
- CJK compatibility character normalization (Kangxi radicals, CJK compat ideographs)
- Multi-format number and date parsing
- Precise section header detection (avoiding substring false matches)
- Cross-statement deduplication (monthly statements carry forward prior entries)
- Security master name backfill with Traditional-to-Simplified normalization

Import from here instead of reimplementing per broker.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable


# ===========================================================================
# 1. CJK compatibility character normalization
# ===========================================================================

def build_cjk_compat_map() -> dict[str, str]:
    """Build a mapping from CJK compatibility characters to standard forms.

    Handles:
    - Kangxi radicals U+2F00-U+2FD5 (e.g. ⽉ -> 月, ⾦ -> 金)
    - CJK Compatibility ideographs U+F900-U+FAFF

    PDF text extraction often yields Kangxi radicals instead of standard CJK
    characters. All format detection and section matching MUST operate on
    normalized text to avoid silent failures.
    """
    m: dict[str, str] = {}
    # Kangxi radicals: each decomposes to a CJK ideograph
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
    # CJK Compatibility ideographs
    for cp in range(0xF900, 0xFB00):
        ch = chr(cp)
        decomp = unicodedata.decomposition(ch)
        if decomp:
            parts = decomp.split()
            if parts and parts[0].startswith("<") and len(parts) > 1:
                m[ch] = chr(int(parts[1], 16))
            elif parts:
                m[ch] = chr(int(parts[0], 16))
    return m


_CJK_COMPAT_MAP = build_cjk_compat_map()


def normalize_text(text: str) -> str:
    """Normalize CJK compatibility characters to standard forms.

    MUST be applied to all extracted PDF text before any format detection,
    section matching, or keyword search. Failing to do so causes silent
    mismatches when PDFs use Kangxi radicals (e.g. ⽉结单 vs 月结单).
    """
    if not text:
        return text
    return "".join(_CJK_COMPAT_MAP.get(ch, ch) for ch in text)


# ===========================================================================
# 2. Number and date parsing
# ===========================================================================

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_number(s: str) -> float:
    """Parse a number string handling parentheses negatives and thousands separators.

    Handles: "1,234.56", "(1,234.56)", "-1,234.56", "".
    """
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
    """Parse a date string into ISO 8601 (YYYY-MM-DD).

    Supports:
    - ISO: "2021-03-15"
    - Day Month Year: "15 MAR 2021", "5 FEB 2021"
    """
    s = s.strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"^(\d{1,2})\s+([A-Z]{3})\s+(\d{4})", s)
    if m:
        day = int(m.group(1))
        mon = _MONTHS.get(m.group(2), 0)
        year = int(m.group(3))
        if mon:
            return f"{year:04d}-{mon:02d}-{day:02d}"
    return s


# ===========================================================================
# 3. Precise section header detection
# ===========================================================================

@dataclass
class SectionRule:
    """A section detection rule.

    Section headers in broker PDFs are typically short standalone lines like
    "交易明细", "资金出入", "融资利息". However, business lines can contain
    these keywords as substrings (e.g. "IPO融资利息 HKD -943.47 ...").

    To avoid false matches, section detection should check that the line IS
    the header, not just CONTAINS it. This is done by:
    - Exact match: line == keyword
    - Or: line starts with keyword and total length is short (<= max_length)
    """
    keyword: str
    max_length: int = 10

    def matches(self, line: str) -> bool:
        if line == self.keyword:
            return True
        if line.startswith(self.keyword) and len(line) <= self.max_length:
            return True
        return False


def make_section_detector(rules: dict[str, SectionRule]) -> Callable[[str], str | None]:
    """Create a section detector function from a set of rules.

    Args:
        rules: mapping of section_name -> SectionRule

    Returns:
        A function that takes a normalized line and returns the section name
        if it matches a header, or None.

    Example::

        detector = make_section_detector({
            "trade": SectionRule("交易明细"),
            "fund": SectionRule("资金出入"),
            "financing": SectionRule("融资利息"),
        })
        section = detector(line)  # returns "financing" or None
    """
    def detect(line: str) -> str | None:
        for name, rule in rules.items():
            if rule.matches(line):
                return name
        return None
    return detect


# ===========================================================================
# 4. Cross-statement deduplication
# ===========================================================================

def deduplicate_records(
    statements: list[Any],
    record_attr: str,
    key_fn: Callable[[Any], tuple],
) -> None:
    """Remove duplicate records across statements in-place.

    Monthly statements often carry forward entries from prior months. This
    function deduplicates records by a composite key and keeps only the first
    occurrence.

    Args:
        statements: list of statement objects (each has a list attribute)
        record_attr: attribute name on each statement (e.g. "incomes", "financing_interests")
        key_fn: function that takes a record and returns a dedup key tuple
    """
    seen: set[tuple] = set()
    for stmt in statements:
        records = getattr(stmt, record_attr, [])
        unique = []
        for rec in records:
            key = key_fn(rec)
            if key not in seen:
                seen.add(key)
                unique.append(rec)
        setattr(stmt, record_attr, unique)


def income_dedup_key(inc) -> tuple:
    """Default dedup key for income records."""
    return (inc.date, inc.code, inc.category, inc.amount, inc.currency)


def financing_dedup_key(fin) -> tuple:
    """Default dedup key for financing interest records."""
    return (fin.date, fin.currency, fin.amount)


# ===========================================================================
# 5. Security master name backfill with Traditional-to-Simplified
# ===========================================================================

# Common Traditional -> Simplified mapping for stock name characters.
# This is NOT a full T2S converter — it covers the most common characters
# appearing in Hong Kong stock names. For production use, consider a proper
# T2S library (e.g. opencc).
_T2S_MAP = {
    "國": "国", "萊": "莱", "醫": "医", "買": "买", "賣": "卖",
    "結": "结", "單": "单", "証": "证", "寶": "宝", "實": "实",
    "電": "电", "訊": "讯", "氣": "气", "車": "车", "銀": "银",
    "團": "团", "東": "东", "寧": "宁", "寬": "宽", "島": "岛",
    "華": "华", "聯": "联", "興": "兴", "業": "业", "網": "网",
    "達": "达", "飛": "飞", "龍": "龙", "鳳": "凤", "豐": "丰",
    "億": "亿", "萬": "万", "與": "与", "兩": "两", "個": "个",
    "們": "们", "這": "这", "來": "来", "過": "过", "從": "从",
    "後": "后", "裡": "里", "為": "为", "對": "对", "開": "开",
    "關": "关", "長": "长", "還": "还", "間": "间", "問": "问",
    "時": "时", "經": "经", "說": "说", "員": "员", "報": "报",
    "務": "务", "構": "构", "機": "机", "標": "标", "準": "准",
    "確": "确", "認": "认", "證": "证", "驗": "验", "導": "导",
    "層": "层", "廳": "厅", "區": "区", "號": "号", "碼": "码",
    "點": "点", "線": "线", "據": "据", "數": "数",
}


def to_simplified(s: str) -> str:
    """Convert common Traditional Chinese characters to Simplified."""
    if not s:
        return s
    return "".join(_T2S_MAP.get(ch, ch) for ch in s)


def backfill_names(
    statements: list[Any],
    trades_attr: str = "trades",
    incomes_attr: str = "incomes",
) -> None:
    """Build security master from trades and backfill missing names.

    Also normalizes all names so records for the same code use the same name
    (preferring Simplified Chinese).

    Args:
        statements: list of parsed statement objects
        trades_attr: attribute name for trade records
        incomes_attr: attribute name for income records
    """
    # Build name map from all trades (prefer Simplified Chinese)
    name_map: dict[tuple[str, str], str] = {}
    for stmt in statements:
        for t in getattr(stmt, trades_attr, []):
            if t.name and t.code:
                key = (t.market, t.code)
                name = to_simplified(t.name)
                if key not in name_map:
                    name_map[key] = name
                elif to_simplified(name_map[key]) != name_map[key] and name == to_simplified(t.name):
                    name_map[key] = name

    # Normalize all trade names
    for stmt in statements:
        for t in getattr(stmt, trades_attr, []):
            if t.code:
                key = (t.market, t.code)
                if key in name_map:
                    t.name = name_map[key]

    # Backfill income names
    for stmt in statements:
        for inc in getattr(stmt, incomes_attr, []):
            if not inc.name and inc.code:
                key = (inc.market, inc.code)
                if key in name_map:
                    inc.name = name_map[key]
            elif inc.name:
                inc.name = to_simplified(inc.name)
