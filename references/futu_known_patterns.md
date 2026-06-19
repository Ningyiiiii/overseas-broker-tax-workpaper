# Futu Known Patterns

Use these notes when parsing Futu statements.

- PDF password should be configurable. In the original project, the known Futu password was `20162270`; do not commit real passwords to public repos.
- `富途总结表.xlsx` is an output-format reference, not source data.
- Old and new Futu statement layouts differ; parser logic should branch by detected table structure, not only file date.
- Company actions may contain dividends, especially after late 2024.
- Older dividend/company-action descriptions may include patterns such as `F/D-HKD...`, `Scrip Charge`, and `Handling Charge`.
- IPO allotments may create cost basis that is not a normal secondary-market buy.
- Source files added later must trigger a full recomputation.
