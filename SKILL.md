---
name: overseas-broker-tax-workpaper
description: Generate China tax-resident overseas brokerage income workpapers from broker PDF, Excel, or CSV source files. Use when the user wants to scan a project folder containing Futu or other overseas broker statements and produce HK-stock and US-stock tax workbooks across China calendar-year and Hong Kong fiscal-year periods, with FIFO and period weighted-average cost methods, dividends, financing interest, FX conversion, exception reporting, and source reconciliation.
---

# Overseas Broker Tax Workpaper

Use this skill to scan the current project folder and generate overseas brokerage tax workpapers for a China tax resident.

## Default Behavior

When invoked, treat the current working directory as the source project folder. Recursively scan for broker PDF, Excel, and CSV files. Do not rely on folder names or file names to determine years; assign records to periods using the dates inside each transaction, income, financing-interest, or statement record.

Generate all eight workbook outputs by default:

1. `港股_中国自然年_FIFO_税务底稿.xlsx`
2. `港股_中国自然年_期间加权平均成本法_税务底稿.xlsx`
3. `港股_香港财年_FIFO_税务底稿.xlsx`
4. `港股_香港财年_期间加权平均成本法_税务底稿.xlsx`
5. `美股_中国自然年_FIFO_税务底稿.xlsx`
6. `美股_中国自然年_期间加权平均成本法_税务底稿.xlsx`
7. `美股_香港财年_FIFO_税务底稿.xlsx`
8. `美股_香港财年_期间加权平均成本法_税务底稿.xlsx`

Also generate `运行报告.md` and `run_report.json`.

## Workflow

1. Load configuration from `config/config.json` if present; otherwise use sensible defaults and password candidates from the user or local config.
2. Scan current folder recursively for source files.
3. Parse source files through broker-specific parsers into normalized records.
4. Build a security master from all sources and backfill missing names by `market + code`.
5. Validate normalized records before calculation.
6. Calculate each market, period regime, and cost method independently from normalized source data.
7. Build the eight workbooks using the fixed output format.
8. Run workbook and data validation.
9. Report blocking errors, warnings, exceptions, and output paths.

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
- Do not output garbled or incomplete stock names. Backfill from the security master; if unresolved, leave the name blank and record an exception.
- Do not force missing cost basis to zero. Leave affected cost and P&L blank and record an exception.
- Output numeric workbook fields as real Excel numbers.
- Dividend and financing-interest detail rows must not repeat annual totals. Add one total row after all detail rows for each year or fiscal year.
- US-stock workbooks must preserve original currency details and include HKD conversion using the official PBOC central parity rate for the period end date.

## Resource Usage

Use `scripts/run_workpaper.py` as the primary command wrapper. Use scripts in `scripts/parsers/`, `scripts/engines/`, `scripts/output/`, and `scripts/reports/` as implementation modules. Keep deterministic business rules in scripts and detailed rules in references.

If a new broker is encountered, add a parser that satisfies `references/broker_parser_contract.md`; do not change the output workbook contract unless the user explicitly approves a new format.
