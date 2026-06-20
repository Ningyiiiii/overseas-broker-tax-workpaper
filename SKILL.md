---
name: overseas-broker-tax-workpaper
description: Generate China tax-resident overseas brokerage income workpapers from Futu or other overseas broker PDF, Excel, or CSV statements. Use when the user wants to scan a project folder, parse HK and US trades even when mixed in the same statement, validate normalized transaction data, and produce eight tax workbooks across China calendar-year and Hong Kong fiscal-year periods with FIFO and period weighted-average cost, dividends/company actions, financing interest, FX conversion, exception reporting, and source reconciliation.
---

# Overseas Broker Tax Workpaper

Use this skill to scan the current project folder and generate overseas brokerage tax workpapers for a China tax resident.

## Default Behavior

Treat the current working directory as the source project folder. Recursively scan broker PDF, Excel, and CSV files. Do not rely on folder names or file names to determine years; assign records to periods using the dates inside transaction, income, financing-interest, or statement records.

Generate all eight workbook outputs by default:

1. `港股_中国自然年_FIFO_税务底稿.xlsx`
2. `港股_中国自然年_期间加权平均成本法_税务底稿.xlsx`
3. `港股_香港财年_FIFO_税务底稿.xlsx`
4. `港股_香港财年_期间加权平均成本法_税务底稿.xlsx`
5. `美股_中国自然年_FIFO_税务底稿.xlsx`
6. `美股_中国自然年_期间加权平均成本法_税务底稿.xlsx`
7. `美股_香港财年_FIFO_税务底稿.xlsx`
8. `美股_香港财年_期间加权平均成本法_税务底稿.xlsx`

Also generate `运行报告.md`, `run_report.json`, and a normalized parsing audit table before final workbook generation when parser coverage has changed.

## Workflow

1. Load configuration from `config/config.json` if present; otherwise use sensible defaults and password candidates from the user or local config.
2. Scan the current folder recursively for source files.
3. Parse source files through broker-specific parsers into normalized records.
4. If parser coverage changed or a new statement layout appears, emit a normalized parsing audit table and inspect coverage before calculating P&L.
5. Build a security master from all sources and backfill missing names by `market + code`.
6. Validate normalized records before calculation.
7. Calculate each market, period regime, and cost method independently from normalized source data.
8. Build the eight workbooks using the fixed output format.
9. Run workbook and data validation.
10. Report blocking errors, warnings, exceptions, and output paths.

## Required References

Read these files when implementing or modifying the workflow:

- `references/normalized_schema.md`: normalized input schema.
- `references/algorithm_spec.md`: FIFO, period weighted-average, dividends, financing interest, and FX rules.
- `references/output_workbook_spec.md`: workbook layout and formatting.
- `references/error_handling.md`: blocking errors, warnings, and exception tables.
- `references/broker_parser_contract.md`: how to add or update broker parsers.
- `references/futu_known_patterns.md`: Futu-specific parsing notes.
- `references/fx_rate_rules.md`: official FX conversion rules for US stocks.
- `references/validation_checklist.md`: final validation checklist.

## Hard Rules

- Keep broker-specific differences in the parser layer. Do not fork the tax engine for each broker.
- Recompute from all available source files after new files are added. Do not append new parsed records behind old calculated outputs.
- Do not treat output templates such as `富途总结表.xlsx` as primary source data.
- Do not classify trades by folder name. Futu HK-stock folders may contain US-stock trades after account migration; classify every record by exchange and currency.
- Do not treat holdings, account-upgrade cash movements, money-market fund activity, or valuation rows as stock trades.
- Do not output garbled or incomplete stock names. Backfill from the security master; if unresolved, leave the name blank and record an exception.
- Do not force missing cost basis to zero. Leave affected cost and P&L blank and record an exception.
- Output numeric workbook fields as real Excel numbers.
- Dividend and financing-interest detail rows must not repeat annual totals. Add one total row after all detail rows for each year or fiscal year.
- US-stock workbooks must preserve original currency details and include HKD conversion using the official PBOC central parity rate for the period end date.

## Resource Usage

Use `scripts/run_workpaper.py` as the primary command wrapper after the parser and output scripts are wired for the local data set. Use scripts in `scripts/parsers/`, `scripts/engines/`, `scripts/output/`, and `scripts/reports/` as implementation modules. Keep deterministic business rules in scripts and detailed rules in references.

When adapting this skill to a new statement set, first produce and inspect normalized records before generating final workbooks. If a new broker is encountered, add a parser that satisfies `references/broker_parser_contract.md`; do not change the output workbook contract unless the user explicitly approves a new format.
