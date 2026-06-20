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

## Market Classification

Classify each normalized trade by the execution exchange and currency, not by source folder:

- `SEHK + HKD` -> `market=HK`
- `EDGX`, `BATS`, `MEMX`, `XNAS`, `XNYS`, `NYSE`, `NASDAQ`, `AMEX`, `ARCA`, or `IEX` with `USD` -> `market=US`

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

## Names and Mojibake

Futu PDF text extraction can corrupt Chinese security names. Never output mojibake as a final security name. Build a security master from all available sources, backfill by `market + code`, and if still unresolved leave the name blank and record a missing-name exception.
