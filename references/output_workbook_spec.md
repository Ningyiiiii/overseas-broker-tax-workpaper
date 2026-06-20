# Output Workbook Specification

Generate eight `.xlsx` files by default:

- `港股_中国自然年_FIFO_税务底稿.xlsx`
- `港股_中国自然年_期间加权平均成本法_税务底稿.xlsx`
- `港股_香港财年_FIFO_税务底稿.xlsx`
- `港股_香港财年_期间加权平均成本法_税务底稿.xlsx`
- `美股_中国自然年_FIFO_税务底稿.xlsx`
- `美股_中国自然年_期间加权平均成本法_税务底稿.xlsx`
- `美股_香港财年_FIFO_税务底稿.xlsx`
- `美股_香港财年_期间加权平均成本法_税务底稿.xlsx`

Each workbook must contain:

- `年度合计`
- `汇总`
- one sheet per calendar year or fiscal year
- `股息利息_公司行动`
- `融资利息`
- `缺成本与异常`
- `缺名称与异常`
- `解析来源与校验`
- `交易来源明细`
- `证券名称字典`

When parser coverage has changed, also produce or preserve a normalized parsing audit artifact before final workbook generation. It should include source file, source page, source row or coordinate, record type, market, currency, code, name, side, dates, quantity, price, gross amount, cash change, fee, and exception status.

## Annual Trade Sheets

Use the final confirmed output style:

- Split sell summary rows before split details.
- Summary rows are for reconciliation only and must not calculate P&L.
- Detail rows calculate P&L.
- Numeric columns are real Excel numbers, not text-formatted numbers.
- The period total row includes sell total, transaction fees, P&L, and dividend total.

## Dividend and Company-Action Sheet

Detail rows do not repeat period totals. After all dividend/company-action rows for each period, add a total row. The total goes in a dedicated annual or fiscal-period total column.

Company-action fee rows such as `Scrip Charge` and `Handling Charge` should appear in this sheet when they relate to a dividend or scrip event.

## Financing Interest Sheet

Detail rows do not repeat period totals. After all financing-interest rows for each period, add a total row. The total goes in a dedicated annual or fiscal-period total column.

## US Stock Workbooks

Preserve original currency fields and add HKD conversion fields. The final column must identify the FX rate and FX date used.

For future natural-year periods where the period-end FX date has not happened yet, leave HKD conversion blank or flag it as unavailable in the report; do not invent a future rate.
