# Algorithm Specification

## Period Regimes

China calendar year:

- `CY2021`: `2021-01-01` through `2021-12-31`

Hong Kong fiscal year:

- `FY2021-2022`: `2021-04-01` through `2022-03-31`

Apply both period regimes independently.

## Split Sell Orders

If one sell order is split into multiple fills, output one summary row before the detail rows. The summary row is for reconciliation only and must not calculate P&L. Detail rows calculate P&L.

## FIFO

Build position lots by `market + currency + code`.

Buy lots are consumed in ascending `trade_date`, then stable source order. When a sell crosses multiple buy lots, split the calculation into multiple detail rows.

For each matched segment:

```text
sell_allocated_amount = sell_gross_amount * segment_quantity / sell_quantity
sell_allocated_fee = sell_fee_total * segment_quantity / sell_quantity
buy_allocated_amount = buy_lot_gross_amount * segment_quantity / original_buy_lot_quantity
buy_allocated_fee = buy_lot_fee_total * segment_quantity / original_buy_lot_quantity
transaction_fee = buy_allocated_fee + sell_allocated_fee
pnl = sell_allocated_amount - buy_allocated_amount - buy_allocated_fee - sell_allocated_fee
```

If there are not enough buy lots for a sell, leave cost and P&L blank for the missing segment and record an exception. Never force missing cost to zero.

## Period Weighted-Average Cost

Calculate by `market + currency + code + period`.

This is period weighted-average cost, not moving average. All buys in the period are included in the average even if the buy date is later than a sell date inside that same period.

```text
weighted_average_unit_cost =
  (opening_position_total_cost + period_buy_gross_amount + period_buy_fees)
  / (opening_quantity + period_buy_quantity)

sell_deductible_cost =
  weighted_average_unit_cost * sell_quantity + sell_fee_total

pnl =
  sell_gross_amount - weighted_average_unit_cost * sell_quantity - sell_fee_total
```

Opening cost comes from prior records or prior period carry-forward. If opening or period buy cost is missing, affected sells must remain blank and enter exceptions.

## Dividends and Company Actions

List detail rows first. After all rows for each period, append one row named `年度股息/分派合计` or fiscal-year equivalent. Put the total in a dedicated total column. Leave detail-row total cells blank.

## Financing Interest

Use the same annual-total layout as dividends. Detail rows first; one period total row at the end; total value in a dedicated total column.

## US Stock FX

US-stock detail values keep original currency. Add HKD conversion outputs using official PBOC central parity for the period end date:

- China calendar year: December 31.
- Hong Kong fiscal year: March 31.

If no official quote exists for the period end date, use the previous available official quote and display the actual FX date. The final workbook column must show the FX rate and date used.
