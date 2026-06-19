# Validation Checklist

Before finalizing, verify:

- All found source files are listed in the run report.
- Encrypted file failures are reported.
- Normalized records have dates, markets, codes, currencies, quantities, amounts, and source references.
- Stock names are complete and non-garbled, or recorded in `缺名称与异常`.
- FIFO and period weighted-average calculations run independently from normalized data.
- Missing cost is blank in outputs and listed in `缺成本与异常`.
- Dividend total rows equal the period detail sums.
- Financing-interest total rows equal the period detail sums.
- Annual trade total rows equal detail sums.
- US-stock FX columns use the correct period-end official rate or the documented previous quote.
- All eight workbook files are generated unless the user explicitly narrows the scope.
- `运行报告.md` and `run_report.json` summarize outputs, warnings, and exceptions.
