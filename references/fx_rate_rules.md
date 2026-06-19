# FX Rate Rules

US-stock workbooks require HKD conversion outputs.

Use official PBOC central parity rates:

- Calendar-year workbooks: use December 31 of the year.
- Hong Kong fiscal-year workbooks: use March 31 at fiscal year end.

If the target date has no official quote, use the previous available official quote and show the actual date used.

The workbook must include a final column identifying the rate and date, for example:

```text
USD/HKD 7.8123 @ 2024-12-31
```

Do not infer FX from broker cash balances unless the user explicitly requests a broker-rate reconciliation.
