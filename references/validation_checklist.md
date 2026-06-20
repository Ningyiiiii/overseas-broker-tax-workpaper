# Validation Checklist

Before finalizing, verify:

- All found source files are listed in the run report.
- Encrypted file failures are reported.
- Normalized records have dates, markets, codes, currencies, quantities, amounts, and source references.
- Parser coverage is checked before calculation when layouts changed.
- For Futu, October 2024 migration files from both old and new accounts are included by record date.
- For Futu, post-migration HK-stock folders are checked for US trades.
- US execution venues such as `EDGX`, `BATS`, `MEMX`, `XNAS`, `XNYS`, `NYSE`, `NASDAQ`, `AMEX`, `ARCA`, and `IEX` are recognized when paired with `USD`.
- Holdings, account-upgrade rows, money-market fund rows, valuation rows, and cash transfers are not emitted as stock trades.
- Stock names are complete and non-garbled, or recorded in `缺名称与异常`.
- FIFO and period weighted-average calculations run independently from normalized data.
- Missing cost is blank in outputs and listed in `缺成本与异常`.
- Dividend/company-action rows include patterns such as `F/D`, `S/D`, `Dividend`, `Coupon`, `Scrip Charge`, and `Handling Charge` when present.
- Dividend total rows equal the period detail sums.
- Financing-interest total rows equal the period detail sums.
- Annual trade total rows equal detail sums.
- US-stock FX columns use the correct period-end official rate or the documented previous quote.
- Future-period FX rates are not invented.
- All eight workbook files are generated unless the user explicitly narrows the scope.
- `运行报告.md` and `run_report.json` summarize outputs, warnings, and exceptions.
