# Futu Known Patterns

Use these notes when parsing Futu statements. Keep these rules in the parser layer; tax engines should only consume normalized records.

## Access and Source Handling

- PDF passwords must be configurable. The observed Futu password for the validated source family was `20162270`; do not commit real passwords to public repos.
- Treat `富途总结表.xlsx` and prior generated workbooks as output-format references, not source records.
- Recompute from the full current source inventory whenever source files change. Do not append newly parsed records behind an existing calculated workbook.
- Do not classify by folder name. Futu "HK stock" statement folders can contain US trades after account migration.

## Statement Layouts

Use two statement-layout families:

1. **Old account old layout**
   - Observed account prefix: `1001100320164382`.
   - Observed through the old-account October 2024 statement.
   - Trade rows are old table-like records without a clean `exchange + currency` execution block.
   - Parser should keep the old table/state-machine branch for these files.

2. **New account new layout**
   - Observed account prefix: `1001231828219038`.
   - Observed from the post-2024-10-18 account-migration statements.
   - Visual statement layout is one family: order summary row, execution-detail row(s), and fee block.
   - PDF text extraction can expose two token orders. Support both in the same parser branch:

```text
Text order A, observed around 2024-10 through 2025-10:
side -> code(name) -> currency -> summary_quantity/summary_price/summary_amount/change
-> exchange -> currency -> trade_date -> trade_time -> settle_date
-> fill_quantity/fill_price/fill_amount/change -> fees

Text order B, observed around 2025-11 and later:
side -> currency -> summary_quantity/summary_price/summary_amount/change
-> code(name) -> exchange -> currency -> trade_date -> trade_time -> settle_date
-> fill_quantity/fill_price/fill_amount/change -> fees
```

Do not describe these two extraction orders as separate statement layouts. They are one new-account statement layout with two PDF text-order variants.

For Text order B, the `code(name)` token before an execution-detail row can be
split across multiple extracted text lines before the exchange token. Observed
examples include `06600(臥安機` + `器人)`, `AEVA(Aeva` + `Technologies)`, and
`GLDM(SPDR` + `Gold` + `MiniShares` + `Trust)`. The parser must concatenate all
non-noise tokens from the first code/name token until the exchange token before
parsing code and name; otherwise it will stop after the first fill and miss most
split-fill rows.

When parsing the new-account token stream across page boundaries, filter split
page footer tokens before looking for the next code/name or fill row. Observed
footer fragments include standalone page numbers such as `10`, `/`, `56` before
the next page's first trade row; if not removed they can be concatenated into
invalid security codes such as `105600258`.

## Market Classification

Classify each normalized trade by the execution exchange and currency, not by source folder:

- `SEHK + HKD` -> `market=HK`
- Known US execution venues such as `EDGX`, `BATS`, `MEMX`, `XNAS`, `XNYS`, `NYSE`, `NASDAQ`, `AMEX`, `ARCA`, `ARCX`, `IEX`, `OCEA`, `CDED`, `EPRL`, `XBOS`, `BATY`, `KNEM`, and `JNST` with `USD` -> `market=US`
- For US stocks, do not require a complete venue whitelist. If the execution currency is `USD` and the security code is a stock/ETF ticker-like code, treat it as a US stock trade unless the row is clearly a fund, cash, or non-stock movement.
- When parsing execution-detail rows, a short all-uppercase token followed by a currency token should be treated as an execution venue boundary even when it is not in the known venue list. Observed post-2025 examples include `JLEQ`, `JSJX`, `MOON`, and `BOSS`. Do not concatenate those venue tokens and following trade fields into the security name.

If exchange/currency does not map cleanly, emit a parser exception rather than guessing.

## Real Trade Detection

A stock trade must have all of:

- exchange
- execution currency
- trade date
- trade time
- settlement date
- fill quantity
- fill price
- fill gross amount
- cash change amount

Do not emit trades from holdings, valuation rows, `Account Upgrade` cash movements, money-market fund activity, fund subscription/redemption, or pure cash transfer rows.

Use both side text and cash-change sign to infer side:

- buy-like side normally has negative cash change.
- sell-like side normally has positive cash change.
- If side text and cash sign conflict, keep the raw record but emit an exception and do not silently calculate.

## Holdings, Cost Tracing, and IPO Allotments

Holdings tables are not stock trades, but they are important reconciliation evidence for missing-cost investigation.

- Do not use a holding snapshot's market value or statement price as tax cost unless the user explicitly approves it as a fallback.
- When a sell lacks cost but the same security appears in an opening-position or closing-position table, trace backward through earlier monthly statements before declaring missing cost.
- Follow the chain across opening holdings, closing holdings, trade detail rows, stock movement rows, and IPO allotment rows until a real cost source is found.
- For HK stocks, a holding present in `YYYY-MM` opening holdings should normally be checked against the previous statement's closing holdings, then earlier trade or stock movement records. Example pattern: a stock appears in 2024-12 opening and closing holdings, then 2025-01 opening holdings, then sells in 2025-01; the cost source is earlier than 2025-01 and should not be reported as missing until the earlier chain has been searched.
- Parse old-account stock movement rows as well as new-account rows. Old-account Futu statements can show `IPO Allotment Qty - #xxxxx` under stock movement / stock in-out sections, with matching cash application, refund, and handling-fee rows in cash movements. Convert these allotments into synthetic buy-cost records using the allotment amount and related IPO fees/refunds when available.
- New-account IPO allotment rows follow the same rule. Do not restrict IPO allotment cost-basis parsing to post-migration statements only.
- If an allotment quantity is present but the cash application/refund details are incomplete, emit the allotment as a cost-source candidate with a warning rather than silently dropping it.

## Account-Migration Month

October 2024 is a migration boundary. Old-account October 2024 can still contain trades through `2024/10/18`; new-account October 2024 can contain trades from later dates such as `2024/10/24`. Do not split the month by folder or account. Parse all records and let record dates determine periods.

New-account October 2024 can also contain `Account Upgrade`, money-market fund movements, and company-action rows. Treat these as cash/source evidence or income/company-action candidates, not stock trades unless they satisfy real trade detection.

## Dividends, Company Actions, and Fees

Recognize dividend/company-action patterns including:

- `F/D`
- `S/D`
- `Dividend`
- `Coupon`
- `Scrip Charge`
- `Handling Charge`
- ADR fee and withholding-tax style descriptions when present

Keep dividend/company-action income separate from trade P&L. Fee rows that belong to a dividend or scrip event should be included in the income/company-action table and annual total logic, not in stock-trade fees.

Company-action stock movements can also create cost-basis records. If a stock movement row shows a positive share quantity and positive amount with descriptions such as `SCP OPT`, `SCRIP`, or `REINV PR`, emit a synthetic buy-cost record for those shares using the stated amount as the cost basis. Keep matching cash dividend, `Scrip Charge`, and `Handling Charge` rows in the income/company-action sheet separately.

## Names and Mojibake

Futu PDF text extraction can corrupt Chinese security names. Never output mojibake as a final security name. Build a security master from all available sources, backfill by `market + code`, and if still unresolved leave the name blank and record a missing-name exception.

## Universal Lessons (from cross-broker testing)

The following lessons were identified while testing with USMART statements but apply to Futu and all other brokers:

### CJK Normalization

PDF text extraction can yield Kangxi radicals (U+2F00-U+2FD5) instead of standard CJK characters. Futu PDFs may also exhibit this. Always normalize before format detection and section matching. Use \parsers.common.normalize_text()\.

### Precise Section Detection

Do not use substring matching (\in\ operator) for section headers. Business lines can contain section keywords as substrings. Use \parsers.common.SectionRule\ with exact or short-line match.

### Cross-Statement Deduplication

Monthly statements may carry forward entries from prior months. Always deduplicate income and financing-interest records after parsing all statements. Use \parsers.common.deduplicate_records()\.

### Name Consistency

Different statements for the same stock may use different character sets (Traditional vs Simplified). Build a security master from all trades and normalize names. Use \parsers.common.backfill_names()\.

### Financing Interest Classification

Margin interest, penalty interest, and similar financing-cost entries must be classified as \inancing_interests\, not \incomes\. Review the fund-section business types carefully for each broker.
