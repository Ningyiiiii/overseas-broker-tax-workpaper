# Normalized Schema

All broker parsers must output normalized records before any tax calculation. The tax engine must not read broker-specific PDF or Excel layouts directly.

## Trade Record

Required fields:

- `broker`
- `market`: `HK` or `US`
- `currency`
- `code`
- `name`
- `side`: `BUY` or `SELL`
- `trade_date`
- `settle_date`
- `order_id`
- `trade_id`
- `quantity`
- `price`
- `gross_amount`
- `fee_total`
- `source_file`
- `source_page`
- `source_row`
- `raw_text`

## Income and Company-Action Record

Required fields:

- `broker`
- `market`
- `currency`
- `date`
- `code`
- `name`
- `category`: `股息/分派`, `利息`, `税费扣减`, `公司行动`, or `未知`
- `amount`
- `tax_withheld`
- `fee`
- `source_file`
- `source_page`
- `source_row`
- `raw_text`

## Financing Interest Record

Required fields:

- `broker`
- `market`
- `currency`
- `date`
- `amount`
- `source_file`
- `source_page`
- `source_row`
- `raw_text`

## Security Master Record

Required fields:

- `market`
- `code`
- `name_zh`
- `name_en`
- `first_seen_source`
- `last_seen_source`
- `confidence`

Stock names must be complete and non-garbled. If unresolved, leave the output name blank and record the issue in `缺名称与异常`.
