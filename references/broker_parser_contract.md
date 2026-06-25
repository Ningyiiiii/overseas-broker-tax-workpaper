# Broker Parser Contract

Broker parsers must convert raw source files into normalized records. They must not calculate tax P&L.

Each parser should:

1. Identify supported file types.
2. Try configured password candidates for encrypted PDFs.
3. Extract trades, income/company actions, financing interest, source summaries, and parser exceptions.
4. Preserve source file, page, row or coordinate, and raw text evidence.
5. Emit normalized records matching `normalized_schema.md`.
6. Avoid outputting garbled stock names.

## Record-Level Market Routing

Do not use folder names as market truth. A parser must classify each trade or income record independently using source content:

- Use execution exchange and currency for trade market.
- Use source description, code, exchange hint, and currency for income/company-action market.
- If classification is ambiguous, emit an exception and keep the source evidence.

## Real Trade Gate

Only emit a normalized stock trade when the raw record contains enough execution evidence:

- side or a reliable side inference
- code
- exchange
- currency
- trade date
- trade time when available in the source
- settlement date when available in the source
- quantity
- price
- gross amount
- fee total or enough fee detail to compute it
- raw cash change or other evidence to verify side

Rows for holdings, account upgrades, fund subscriptions/redemptions, cash transfers, valuation, or position summaries must not become trades.

## Exceptions

Parser exceptions are part of the output contract. Emit them for:

- unsupported layout
- missing or contradictory side
- ambiguous market
- missing required execution fields
- garbled or unresolved security name
- cash movement that looks relevant but is not a stock trade
- encrypted or unreadable source file

Adding a new broker should require a new parser only. Do not fork the common tax engine or workbook format for broker-specific quirks.

## Universal Rules (apply to all brokers)

These rules are derived from real parsing incidents across multiple brokers. Every parser MUST follow them.

### 1. CJK Normalization Before Detection

PDF text extraction frequently yields CJK compatibility characters (Kangxi radicals U+2F00-U+2FD5 and CJK Compatibility ideographs U+F900-U+FAFF) instead of standard CJK characters. For example, `⽉` (U+2F49) instead of `月` (U+6708).

- **Rule**: All extracted PDF text MUST be normalized via `common.normalize_text()` before any format detection, section matching, or keyword search.
- **Reason**: Format detection keywords like `月结单` will silently fail to match `⽉结单`, causing the parser to pick the wrong format branch and lose all records.
- **Use**: `from parsers.common import normalize_text`

### 2. Precise Section Header Detection

Section headers in broker PDFs are short standalone lines (e.g. `交易明细`, `资金出入`, `融资利息`). Business data lines can contain these keywords as substrings.

- **Rule**: Section header detection MUST use exact match or short-line match, NOT substring match (`in` operator). Use `common.SectionRule` and `common.make_section_detector()`.
- **Reason**: The line `IPO融资利息 HKD -943.47 2021-06-17` contains the substring `融资利息`. A naive `if "融资利息" in line` check will falsely trigger a section switch, causing all subsequent fund-section entries (dividends, interest, penalties) to be lost.
- **Use**:
  ```python
  from parsers.common import SectionRule, make_section_detector
  detect = make_section_detector({
      "trade": SectionRule("交易明细"),
      "fund": SectionRule("资金出入"),
      "financing": SectionRule("融资利息"),
  })
  section = detect(line)
  ```

### 3. Cross-Statement Deduplication

Monthly statements often carry forward entries from prior months (e.g. a penalty interest entry from the previous month appears again in the current month's statement).

- **Rule**: After parsing all statements, income and financing-interest records MUST be deduplicated across statements. Use `common.deduplicate_records()` with appropriate key functions.
- **Reason**: Without deduplication, the same dividend or financing interest entry will be counted multiple times, inflating annual totals.
- **Use**:
  ```python
  from parsers.common import deduplicate_records, income_dedup_key, financing_dedup_key
  deduplicate_records(statements, "incomes", income_dedup_key)
  deduplicate_records(statements, "financing_interests", financing_dedup_key)
  ```

### 4. Name Backfill and Traditional-to-Simplified Normalization

Different statements for the same stock may use different character sets (Traditional vs Simplified Chinese), and income records often lack stock names that trades have.

- **Rule**: After parsing, build a security master from trade records and backfill missing income names. Normalize all names to Simplified Chinese so the same stock has a consistent name across all records. Use `common.backfill_names()`.
- **Reason**: Without normalization, the same stock appears as both `中國海洋石油` and `中国海洋石油` in the output, confusing the security dictionary and annual totals.
- **Use**:
  ```python
  from parsers.common import backfill_names
  backfill_names(statements)
  ```

### 5. Business Category Classification

Financing-related entries (margin interest, IPO financing interest, penalty interest) MUST be classified as financing interest, not as income.

- **Rule**: Entries like `罚息入账`, `IPO融资利息`, margin interest charges, and similar financing-cost entries go into `financing_interests`, NOT `incomes`. Only dividends, dividend tax withholding, and deposit interest go into `incomes`.
- **Reason**: Misclassifying financing interest as income inflates dividend totals and understates financing costs.

### 6. Settled vs Unsettled Trade Sections

Broker statements may contain both a "settled / account statement" section and an "unsettled / pending trades" section. The unsettled section is a preview of trades that will settle in the next period; those same trades will appear again as settled in the next period's statement.

- **Rule**: Parsers MUST track section context and ONLY emit trades from the settled / account-statement section. Lines in the unsettled-trades section MUST be skipped entirely. Do not rely on field-level dedup to catch these, because the unsettled and settled versions of the same trade can differ in date format, settlement date, and amount presentation.
- **Reason**: If unsettled trades are also parsed, each such trade is counted twice (once as unsettled, once as settled in the next statement). This inflates sell quantities, depletes cost basis prematurely, and produces false "missing cost" exceptions for later sells.
- **Example (USMART old format)**:
  - `賬戶結單` (account statement) = settled, authoritative -> parse trades
  - `未結算交易` (unsettled trades) = pending preview -> skip all lines
  - `投資總結` / `資金變動` = summary, not trades -> skip
- **Use**: Add a section-state variable. On entering the unsettled section, set a flag and skip every line until the next settled section or a non-trade summary section begins.

### 7. Import Common Utilities

Parsers SHOULD import shared utilities from `parsers.common` rather than reimplementing them:

- `normalize_text()` — CJK normalization
- `parse_number()` — multi-format number parsing
- `parse_date()` — multi-format date parsing
- `SectionRule` / `make_section_detector()` — precise section detection
- `deduplicate_records()` / `income_dedup_key()` / `financing_dedup_key()` — dedup
- `backfill_names()` / `to_simplified()` — name normalization

This ensures consistency across brokers and prevents re-introduction of fixed bugs.
