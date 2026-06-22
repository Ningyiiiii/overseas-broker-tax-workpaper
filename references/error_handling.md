# Error Handling

## Blocking Errors

Stop formal workbook generation when:

- No source files are found.
- All source files fail parsing.
- All encrypted PDFs fail password attempts.
- No trade records are found.
- Critical fields are globally missing: date, side, quantity, amount, or currency.
- The output directory cannot be written.

## Warnings

Generate workbooks but report warnings when:

- Some files fail parsing.
- Some sells have missing cost basis.
- Some stock names are missing or suspicious.
- Company-action rows cannot be fully classified.
- Unknown currencies appear.
- Required US-stock FX rates are missing.

## Exception Tables

Record individual problems in workbook exception sheets:

- Missing cost basis.
- Missing or garbled stock name.
- Invalid or missing date.
- Unclassified company action.
- Unknown currency.
- Fee parsing failure.

Do not silently drop problematic records.

Missing-cost exception rows should only be emitted after parser-level and engine-level cost tracing has been attempted. For Futu data, check earlier trades, old-account and new-account IPO allotment / stock movement rows, and prior-month opening/closing holding continuity before declaring missing cost.

Do not add records outside the requested output periods to workbook exception sheets. They may be retained in parse audit artifacts, but they should not inflate tax-period missing-cost exception counts.

When a holding snapshot proves share continuity but does not provide original acquisition cost, classify the issue explicitly as unresolved historical cost basis rather than parser-missing trade data.
