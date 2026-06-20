# Normalized Schema

All broker parsers must output normalized records before any tax calculation. The tax engine must not read broker-specific PDF or Excel layouts directly.

## Trade Record

Required fields:

- `broker`
- `account_id`
- `market`: `HK` or `US`
- `exchange`
- `currency`
- `code`
- `name`
- `side`: `BUY` or `SELL`
- `side_source`: raw side text or inference note
- `trade_date`
- `trade_time`
- `settle_date`
- `order_id`
- `trade_id`
- `quantity`
- `price`
- `gross_amount`
- `cash_change`
- `fee_total`
- `fee_detail`
- `source_file`
- `source_page`
- `source_row`
- `source_coord`
- `raw_text`
- `parser_layout`: broker-specific layout label
- `exception`

## Income and Company-Action Record

Required fields:

- `broker`
- `account_id`
- `market`
- `currency`
- `date`
- `settle_date`
- `code`
- `name`
- `category`: `股息/分派`, `利息`, `税费扣减`, `公司行动`, or `未知`
- `amount`
- `tax_withheld`
- `fee`
- `description`
- `source_file`
- `source_page`
- `source_row`
- `source_coord`
- `raw_text`
- `exception`

## Financing Interest Record

Required fields:

- `broker`
- `account_id`
- `market`
- `currency`
- `date`
- `amount`
- `description`
- `source_file`
- `source_page`
- `source_row`
- `raw_text`
- `exception`

## Parser Exception Record

Required fields:

- `broker`
- `source_file`
- `source_page`
- `record_type`
- `severity`
- `message`
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
