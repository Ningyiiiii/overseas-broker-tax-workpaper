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
