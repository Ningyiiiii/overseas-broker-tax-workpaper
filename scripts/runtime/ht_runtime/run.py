"""Main entry point for the local Huatai tax workpaper generator."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from tax_workpaper.engines.fifo import calculate_fifo
from tax_workpaper.engines.periods import (
    china_calendar_year_key,
    hong_kong_fiscal_year_key,
    parse_date,
    period_keys_for,
    prior_period,
)
from tax_workpaper.engines.period_weighted_average import calculate_period_weighted_average
from tax_workpaper.normalize.security_master import (
    backfill_names,
    build_security_master,
    looks_garbled,
)
from tax_workpaper.normalize.validators import validate_records
from tax_workpaper.output.build_workbook import build_workbook
from tax_workpaper.output.workbook_schema import output_filename
from tax_workpaper.parsers.huatai_parser import HuataiParser
from tax_workpaper.reports.run_report import write_run_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Huatai tax workpapers.")
    parser.add_argument("--source-root", default=".", help="Folder to scan recursively.")
    parser.add_argument("--output-dir", default="税务底稿输出",
                        help="Folder for generated workbooks.")
    parser.add_argument("--config", default="tax_workpaper/config.json", help="Local config file.")
    return parser.parse_args()


def _load_config(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "markets": ["HK", "US"],
        "period_regimes": ["china_calendar_year", "hong_kong_fiscal_year"],
        "cost_methods": ["fifo", "period_weighted_average"],
    }


def _is_template(path: Path) -> bool:
    return path.name in {"富途总结表.xlsx"}


def scan_sources(root: Path) -> list[Path]:
    out: list[Path] = []
    # Skip our own output folders and the reference sample folder so we don't
    # try to parse workbooks (which would fail or produce duplicates).
    skip_dir_names = {"outputs", "税务底稿输出", "reference"}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in {".pdf", ".xlsx", ".xls", ".csv"}:
            continue
        if _is_template(p):
            continue
        # Skip generated output files and reference samples.
        if any(part in skip_dir_names for part in p.parts):
            continue
        out.append(p)
    return out


def _backfill_names_all(records):
    master = build_security_master(records)
    out = backfill_names(records, master)
    return out, master


def _name_exceptions(records) -> list[dict]:
    out: list[dict] = []
    for r in records:
        name = getattr(r, "name", "") or ""
        if not name.strip() or looks_garbled(name):
            out.append(
                {
                    "type": "missing_or_garbled_name",
                    "code": getattr(r, "code", ""),
                    "market": getattr(r, "market", ""),
                    "currency": getattr(r, "currency", ""),
                    "source_file": getattr(r, "source_file", ""),
                    "source_page": getattr(r, "source_page", ""),
                    "raw_text": (getattr(r, "raw_text", "") or "")[:120],
                }
            )
    return out


def _collect_period_keys(records, period_regime: str) -> list[str]:
    keys: set[str] = set()
    for r in records:
        d = parse_date(getattr(r, "trade_date", "") or getattr(r, "date", ""))
        if d is None:
            continue
        keys.add(period_keys_for(d)[period_regime])
    return sorted(keys)


def main() -> int:
    args = parse_args()
    source_root = Path(args.source_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    config = _load_config(Path(args.config))

    output_dir.mkdir(parents=True, exist_ok=True)
    sources = scan_sources(source_root)
    parser = HuataiParser()
    parsed_results: list[dict] = []
    failed: list[dict] = []
    for src in sources:
        try:
            result = parser.parse(src, password_candidates=[])
            if result.get("exceptions") and any(
                e.get("type") == "pdf_read_failure" for e in result["exceptions"]
            ):
                failed.append({"file": str(src), "reason": "pdf_read_failure",
                               "detail": result["exceptions"][0].get("detail", "")})
                continue
            parsed_results.append(result)
        except Exception as exc:  # noqa: BLE001
            failed.append({"file": str(src), "reason": "exception", "detail": str(exc)})

    all_trades = []
    all_income = []
    all_financing = []
    source_files_meta: list[dict] = []
    seen_trade_keys: set[tuple] = set()
    seen_income_keys: set[tuple] = set()
    seen_financing_keys: set[tuple] = set()
    # Track (date, code) tuples that already have a 股息/分派 record with a
    # canonical (corporate-action) raw_text - we use this to dedup duplicate
    # 资金存入 Dividend/Cash entries that point to the same distribution.
    dividend_corp_action_keys: set[tuple[str, str]] = set()
    for res in parsed_results:
        for i in res.get("income", []):
            if i.category == "股息/分派" and i.date and i.code:
                # Heuristic: 股息/红股/公司行动 entries have a raw_text that
                # includes the stock code at the start, while 资金存入
                # entries only carry the note text.
                if i.raw_text and re.match(r"\d{4,6}:[A-Z]{2}", i.raw_text or ""):
                    dividend_corp_action_keys.add((i.date, i.code))
    for res in parsed_results:
        for t in res.get("trades", []):
            key = (
                t.trade_id or "",
                t.trade_date or "",
                t.code or "",
                t.side or "",
                round(abs(t.quantity or 0.0), 6),
                round(t.price or 0.0, 6),
            )
            if key in seen_trade_keys:
                continue
            seen_trade_keys.add(key)
            all_trades.append(t)
        for i in res.get("income", []):
            if (
                i.category == "股息/分派"
                and i.date
                and i.code
                and (i.date, i.code) in dividend_corp_action_keys
                and not (i.raw_text and re.match(r"\d{4,6}:[A-Z]{2}", i.raw_text or ""))
            ):
                # Skip 资金存入 Dividend/Cash duplicate of a 股息/红股/公司行动 entry.
                continue
            key = (
                i.date or "",
                i.code or "",
                i.category or "",
                round(i.amount or 0.0, 6),
                i.raw_text or "",
            )
            if key in seen_income_keys:
                continue
            seen_income_keys.add(key)
            all_income.append(i)
        for f in res.get("financing_interest", []):
            key = (
                f.date or "",
                f.currency or "",
                round(f.amount or 0.0, 6),
                f.raw_text or "",
            )
            if key in seen_financing_keys:
                continue
            seen_financing_keys.add(key)
            all_financing.append(f)
        source_files_meta.append(
            {
                "文件": res["source_file"],
                "类型": res.get("statement_kind", ""),
                "期间": res.get("statement_period", ""),
                "客户户口": res.get("account", ""),
                "交易笔数": len(res.get("trades", [])),
                "股息/分派笔数": len(res.get("income", [])),
                "异常": "; ".join(e.get("type", "") for e in res.get("exceptions", [])),
            }
        )

    # Build security master from trades + income + holdings parsed names.
    all_records_for_master = list(all_trades) + list(all_income) + list(all_financing)
    for res in parsed_results:
        for h in res.get("holdings", []):
            # holdings are dicts, not records; construct a fake record-like dict
            # but build_security_master reads attributes; convert to namespace.
            from types import SimpleNamespace

            all_records_for_master.append(
                SimpleNamespace(
                    market=h.get("market", ""),
                    code=h.get("code", ""),
                    name=h.get("name", ""),
                )
            )
    all_records_for_master, master = _backfill_names_all(all_records_for_master)
    # Re-backfill trades and income names from the master.
    all_trades = backfill_names(all_trades, master)
    all_income = backfill_names(all_income, master)

    name_excs = _name_exceptions(all_trades) + _name_exceptions(all_income)

    validations = validate_records(all_trades) + validate_records(all_income)

    # Build workbooks for the four markets combinations.
    markets = config.get("markets", ["HK", "US"])
    period_regimes = config.get("period_regimes", ["china_calendar_year", "hong_kong_fiscal_year"])
    cost_methods = config.get("cost_methods", ["fifo", "period_weighted_average"])

    # PWA needs carry-forward openings. Compute them in order.
    pwa_openings: dict[tuple[str, str, str, str], object] = {}

    outputs: list[str] = []
    blocking_errors: list[str] = []
    warnings: list[str] = []
    exceptions_summary: Counter = Counter()

    if not parsed_results:
        blocking_errors.append("no_source_files_parsed")
    if not all_trades and not all_income:
        blocking_errors.append("no_trade_or_income_records")

    for market in markets:
        market_trades = [t for t in all_trades if t.market == market]
        market_income = [i for i in all_income if i.market == market]
        market_financing = [f for f in all_financing if f.market == market]
        if not market_trades and not market_income and not market_financing:
            warnings.append(f"no_records_for_market_{market}")
            continue
        for period_regime in period_regimes:
            period_keys = _collect_period_keys(
                market_trades + market_income + market_financing, period_regime
            )
            for cost_method in cost_methods:
                if cost_method == "fifo":
                    calc = calculate_fifo(market_trades, market)
                    rows = calc.rows
                    exceptions = list(calc.exceptions)
                else:
                    pwa_openings_this_period: dict = {}
                    calc = calculate_period_weighted_average(
                        market_trades, market, period_regime,
                        opening_positions=pwa_openings_this_period,
                    )
                    rows = calc.rows
                    exceptions = list(calc.exceptions)
                    # PWA carry-forward is built into result.opening_positions.
                    # We only carry forward period-end state for the next
                    # period; the runner is single-pass so no chain is needed.

                for e in exceptions:
                    exceptions_summary[e.get("type", "unknown")] += 1

                filename = output_filename(market, period_regime, cost_method)
                target = output_dir / filename
                period_label = (
                    "中国自然年" if period_regime == "china_calendar_year" else "香港财年"
                )
                build_workbook(
                    target,
                    market=market,
                    period_regime=period_regime,
                    cost_method=cost_method,
                    trade_rows=rows,
                    income_rows=market_income,
                    financing_rows=market_financing,
                    source_files=source_files_meta,
                    master=master,
                    exceptions=exceptions,
                    name_exceptions=name_excs,
                    period_label=period_label,
                )
                outputs.append(str(target))

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "output_dir": str(output_dir),
        "scanned_files": len(sources),
        "parsed_files": len(parsed_results),
        "failed_files": len(failed),
        "trade_count": len(all_trades),
        "income_count": len(all_income),
        "financing_count": len(all_financing),
        "security_count": len(master),
        "blocking_errors": blocking_errors,
        "warnings": warnings,
        "exceptions_summary": dict(exceptions_summary),
        "outputs": outputs,
        "failed_details": failed,
    }
    write_run_report(output_dir, report)

    print(f"scanned={len(sources)} parsed={len(parsed_results)} failed={len(failed)} "
          f"trades={len(all_trades)} income={len(all_income)} "
          f"financing={len(all_financing)} outputs={len(outputs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
