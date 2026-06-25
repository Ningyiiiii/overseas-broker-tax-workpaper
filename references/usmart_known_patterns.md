# USMART (盈立证券) Known Patterns

Use these notes when parsing USMART (uSmart Securities) statements.

## PDF Formats

USMART has two layout variants:

- **Old bilingual format (M002C-E)**: Traditional Chinese + English. Trade lines use `買 #00883` / `賣 #00883` inline with `DD MON YYYY` dates. Date header: `結單日期`.
- **New simplified format (M21/M2/D11)**: Simplified Chinese. Table-style `港股 买入/卖出`. Date header: `结单日期` / `月结单`.

Format detection MUST run on CJK-normalized text because the new format uses Kangxi radicals (e.g. `⽉结单` instead of `月结单`).

## Key Traps

### 1. Kangxi Radicals in Section Headers

The new format PDF uses Kangxi radical `⽉` (U+2F49) in `⽉结单`. Without normalization, format detection fails and the parser picks the wrong branch, losing all records.

**Fix**: Always call `normalize_text()` before format detection. See `broker_parser_contract.md` rule 1.

### 2. "IPO融资利息" Triggers False Section Switch

The fund-section entry `IPO融资利息 HKD -943.47 2021-06-17` contains the substring `融资利息`. A naive `if "融资利息" in line` check will falsely switch to the financing section, losing all subsequent fund entries including dividends and penalties.

**Fix**: Use precise section detection (`SectionRule` with exact/short-line match). See `broker_parser_contract.md` rule 2.

### 3. Penalty Interest and IPO Financing Interest Are Financing, Not Income

`罚息入账` (penalty interest) and `IPO融资利息` (IPO financing interest) entries appear in the fund section but MUST be classified as `financing_interests`, not `incomes`.

**Fix**: Route these business types to `FinancingInterestRecord`. See `broker_parser_contract.md` rule 5.

### 4. Financing Interest Section Format

The financing interest section uses format `币种 利率/年化 本月累计利息` (e.g. `HKD 6.60% 0.00`), NOT `币种 金额 日期`. The amount is usually `0.00` for months with no margin balance.

**Fix**: Parse with regex `^(HKD|USD|CNY)\s+([\d.]+%)\s+(-?[\d,.]+)`. Only record entries where amount is non-zero.

### 5. Old Format Sell Lines with Trailing Numbers

Old format sell lines can have multiple trailing numbers (net amount + gross amount):
```
18 FEB 2021 22 FEB 2021 2021-00947910-000 賣 #00883 中國海洋石油 40,870.76 186,612.98
```

A greedy regex `(.+?)\s+([\d,.]+)\s*$` will capture `中國海洋石油 40,870.76` as the name and `186,612.98` as the amount.

**Fix**: Allow trailing extra numbers: `(.+?)\s+([\d,.]+)(?:\s+[\d,.]+)*\s*$`.

### 6. Name Continuation Across Lines

Stock names can span multiple lines in the new format. The header line may show `(康德` and the continuation line shows `莱医械)`.

**Fix**: Allow parentheses in name continuation regex. Strip surrounding parens when flushing the trade block.

### 7. Cross-Statement Duplicates

Monthly statements carry forward prior month's penalty interest entries. For example, the `罚息入账 HKD -105.52 2021-09-30` entry appears in both the October and November statements.

**Fix**: Deduplicate financing interests by `(date, currency, amount)`. See `broker_parser_contract.md` rule 3.

### 8. Traditional vs Simplified Names

Old format statements use Traditional Chinese names (`中國海洋石油`), new format uses Simplified (`中国海洋石油`). Both refer to the same stock (HK 00883).

**Fix**: Build security master from all trades, prefer Simplified, normalize all records. See `broker_parser_contract.md` rule 4.

### 9. Income Code Backfill

Dividend entries in the fund section sometimes lack the stock code on the same line. The code appears on a nearby line (before or after) in `#DDDDD` or `(DDDDD)` format.

**Fix**: Search nearby lines (±2-3 lines) for code patterns. Also backfill from other income records on the same date. Skip code backfill for generic interest records (they don't have stock codes).

## Password

USMART PDF password is user-provided. Do not commit real passwords.
