# Output Workbook Specification

Generate eight `.xlsx` files by default, covering:

- `HK` and `US`
- `china_calendar_year` and `hong_kong_fiscal_year`
- `fifo` and `period_weighted_average`

Each workbook must contain:

- `年度合计`
- `汇总`
- One sheet per year or fiscal year
- `股息利息_公司行动`
- `融资利息`
- `缺成本与异常`
- `缺名称与异常`
- `解析来源与校验`
- `交易来源明细`
- `证券名称字典`

## Annual Trade Sheets

Use the final confirmed output style:

- Split sell summary rows before split details.
- Summary rows do not calculate P&L.
- Numeric columns are real Excel numbers.
- The period total row includes sell total, fees, P&L, and dividend total.

## Dividend Sheet

Detail rows do not repeat period totals. After all dividend rows for each period, add a total row. The total goes in `年度股息/分派合计` or the corresponding fiscal-period total column.

## Financing Interest Sheet

Detail rows do not repeat period totals. After all financing-interest rows for each period, add a total row. The total goes in `年度融资利息合计` or the corresponding fiscal-period total column.

## US Stock Workbooks

Preserve original currency fields and add HKD conversion fields. The final column must identify the FX rate and FX date used.
