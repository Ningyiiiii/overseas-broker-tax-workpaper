# Broker Parser Contract

Broker parsers must convert raw source files into normalized records. They must not calculate tax P&L.

Each parser should:

1. Identify supported file types.
2. Try configured password candidates for encrypted PDFs.
3. Extract trades, income/company actions, financing interest, source summaries, and parser exceptions.
4. Preserve source file, page, row, and raw text evidence.
5. Emit normalized records matching `normalized_schema.md`.
6. Avoid outputting garbled stock names.

Adding a new broker should require a new parser only. Do not fork the common tax engine or workbook format for broker-specific quirks.
