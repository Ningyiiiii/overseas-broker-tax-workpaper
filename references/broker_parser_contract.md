# Broker Parser Contract

Broker parsers must convert raw source files into normalized records. They must not calculate tax P&L.

Each parser should:

1. Identify supported file types.
2. Try configured password candidates for encrypted PDFs.
3. Extract trades, income/company actions, financing interest, source summaries, and parser exceptions.
4. Preserve source file, page, row or coordinate, and raw text evidence.
5. Emit normalized records matching `normalized_schema.md`.
6. Avoid outputting garbled stock names.

## Record-Level Market Routing

Do not use folder names as market truth. A parser must classify each trade or income record independently using source content:

- Use execution exchange and currency for trade market.
- Use source description, code, exchange hint, and currency for income/company-action market.
- If classification is ambiguous, emit an exception and keep the source evidence.

## Real Trade Gate

Only emit a normalized stock trade when the raw record contains enough execution evidence:

- side or a reliable side inference
- code
- exchange
- currency
- trade date
- trade time when available in the source
- settlement date when available in the source
- quantity
- price
- gross amount
- fee total or enough fee detail to compute it
- raw cash change or other evidence to verify side

Rows for holdings, account upgrades, fund subscriptions/redemptions, cash transfers, valuation, or position summaries must not become trades.

## Exceptions

Parser exceptions are part of the output contract. Emit them for:

- unsupported layout
- missing or contradictory side
- ambiguous market
- missing required execution fields
- garbled or unresolved security name
- cash movement that looks relevant but is not a stock trade
- encrypted or unreadable source file

Adding a new broker should require a new parser only. Do not fork the common tax engine or workbook format for broker-specific quirks.
