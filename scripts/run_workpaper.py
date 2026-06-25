"""统一税务底稿生成入口（券商无关）。

自动识别 PDF 所属券商，解析为标准化记录，用统一的税务引擎计算，
生成 8 个工作簿 + 运行报告。

用法:
    python run_workpaper.py --source-root <源文件夹> --output-dir <输出文件夹> [--password <PDF密码>]

支持自动识别的券商:
    - 华泰 (HuataiParser)
    - 华盛通 (HuashengParser)
    - 盈立 (UsmartParser)

如果要接入新券商，只需:
    1. 写一个 <broker>_parser.py，实现 BrokerParser Protocol (can_parse + parse)
    2. 在 parsers/__init__.py 注册表里加一行
    3. 不需要改这个文件，也不需要改引擎或 builder
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# 确保能 import skill 仓库的模块
# skill 仓库的 scripts 目录就是 tax_workpaper 包
_SKILL_ROOT = Path(r"c:\Users\Ning\.trae-cn\skills\overseas-broker-tax-workpaper")
_SKILL_SCRIPTS = _SKILL_ROOT / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

# 把 scripts 目录注册为 tax_workpaper 包（因为所有模块都用 from tax_workpaper.xxx import）
import importlib
import types
_tax_workpaper_pkg = types.ModuleType("tax_workpaper")
_tax_workpaper_pkg.__path__ = [str(_SKILL_SCRIPTS)]
sys.modules["tax_workpaper"] = _tax_workpaper_pkg

from tax_workpaper.engines.fifo import calculate_fifo
from tax_workpaper.engines.periods import (
    parse_date,
    period_keys_for,
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
from tax_workpaper.reports.run_report import write_run_report


# ---- 文件扫描 ----

_SKIP_DIR_NAMES = {"outputs", "税务底稿输出", "reference", ".git", "__pycache__"}
_TEMPLATE_FILES = {"富途总结表.xlsx"}


def scan_sources(root: Path) -> list[Path]:
    """递归扫描源文件，跳过输出目录和模板文件。"""
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in {".pdf", ".xlsx", ".xls", ".csv"}:
            continue
        if p.name in _TEMPLATE_FILES:
            continue
        if any(part in _SKIP_DIR_NAMES for part in p.parts):
            continue
        out.append(p)
    return out


# ---- 券商自动识别与解析 ----

def _load_parsers() -> list:
    """加载所有已注册的 parser 实例。"""
    # 优先从 parsers 包加载
    try:
        from tax_workpaper.parsers import _get_parsers
        parsers = _get_parsers()
        if parsers:
            return parsers
    except ImportError:
        pass

    # fallback: 直接 import 各 parser 模块
    parsers = []
    parser_specs = [
        ("tax_workpaper.parsers.huatai_parser", "HuataiParser"),
        ("tax_workpaper.parsers.huasheng_parser", "HuashengParser"),
        ("tax_workpaper.parsers.usmart_parser", "UsmartParser"),
    ]
    for mod_path, cls_name in parser_specs:
        try:
            mod = __import__(mod_path, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
            parsers.append(cls())
        except (ImportError, AttributeError) as e:
            print(f"  警告: 无法加载 {cls_name}: {e}")
            continue
    return parsers


def _select_parser(path: Path, parsers: list, password_candidates: list[str] | None = None):
    """遍历 parser 列表，返回第一个 can_parse 命中的。

    对于加密 PDF，can_parse 可能因打不开而返回 False。
    这时用密码候选打开后再检查内容特征。
    """
    for parser in parsers:
        try:
            if parser.can_parse(path):
                return parser
        except Exception:
            continue

    # 如果无密码打不开，尝试用密码候选打开后检查内容
    if password_candidates:
        import pdfplumber
        for pwd in password_candidates:
            try:
                with pdfplumber.open(str(path), password=pwd) as pdf:
                    if not pdf.pages:
                        continue
                    text = (pdf.pages[0].extract_text() or "")
                    if len(pdf.pages) > 1:
                        text += "\n" + (pdf.pages[1].extract_text() or "")
            except Exception:
                continue
            # 用文本特征判断券商
            for parser in parsers:
                try:
                    # 调用 parser 的 can_parse_with_text（如果有的话）
                    if hasattr(parser, "can_parse_with_text"):
                        if parser.can_parse_with_text(text):
                            return parser
                    else:
                        # fallback: 检查 parser 的特征词
                        keywords = getattr(parser, "_detect_keywords", None)
                        if keywords and any(kw in text for kw in keywords):
                            return parser
                except Exception:
                    continue
            break  # 只要第一个能打开的密码
    return None


def parse_all_sources(
    sources: list[Path],
    parsers: list,
    password_candidates: list[str],
) -> tuple[list[dict], list[dict], dict[str, int]]:
    """自动识别券商并解析所有源文件。

    Returns:
        (results, errors, broker_stats)
        - results: 成功解析的结果列表
        - errors: 失败列表
        - broker_stats: {broker_name: file_count}
    """
    results: list[dict] = []
    errors: list[dict] = []
    broker_stats: dict[str, int] = {}

    for src in sources:
        parser = _select_parser(src, parsers, password_candidates)
        if parser is None:
            errors.append({"file": src.name, "reason": "no_parser",
                           "detail": "无 parser 认领此文件（未知券商格式）"})
            continue

        try:
            result = parser.parse(src, password_candidates=password_candidates)
            if result.get("exceptions") and any(
                e.get("type") == "pdf_read_failure" for e in result.get("exceptions", [])
            ):
                errors.append({"file": src.name, "reason": "pdf_read_failure",
                               "detail": result["exceptions"][0].get("detail", "")})
                continue
            if not result.get("trades") and not result.get("income") and not result.get("financing_interest") and not result.get("statement_kind"):
                errors.append({"file": src.name, "reason": "empty_result",
                               "detail": "解析结果为空"})
                continue
            results.append(result)
            broker_name = result.get("broker", parser.broker)
            broker_stats[broker_name] = broker_stats.get(broker_name, 0) + 1
        except Exception as exc:
            errors.append({"file": src.name, "reason": "exception", "detail": str(exc)})

    return results, errors, broker_stats


# ---- 记录收集与去重 ----

def collect_records(results: list[dict]) -> tuple[list, list, list, list[dict]]:
    """从解析结果收集所有记录，做跨文件去重。

    Returns:
        (all_trades, all_income, all_financing, source_files_meta)
    """
    all_trades = []
    all_income = []
    all_financing = []
    source_files_meta: list[dict] = []
    seen_trade_keys: set = set()
    seen_income_keys: set = set()
    seen_financing_keys: set = set()

    for res in results:
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
            key = (
                i.date or "",
                i.code or "",
                i.category or "",
                round(i.amount or 0.0, 6),
                i.currency or "",
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
            )
            if key in seen_financing_keys:
                continue
            seen_financing_keys.add(key)
            all_financing.append(f)

        source_files_meta.append({
            "文件": res.get("source_file", ""),
            "类型": res.get("statement_kind", ""),
            "期间": res.get("statement_period", ""),
            "客户户口": res.get("account", ""),
            "交易笔数": len(res.get("trades", [])),
            "股息/分派笔数": len(res.get("income", [])),
            "异常": "; ".join(e.get("type", "") for e in res.get("exceptions", [])),
        })

    return all_trades, all_income, all_financing, source_files_meta


def _name_exceptions(records) -> list[dict]:
    out: list[dict] = []
    for r in records:
        name = getattr(r, "name", "") or ""
        if not name.strip() or looks_garbled(name):
            out.append({
                "type": "missing_or_garbled_name",
                "code": getattr(r, "code", ""),
                "market": getattr(r, "market", ""),
                "currency": getattr(r, "currency", ""),
                "source_file": getattr(r, "source_file", ""),
                "source_page": getattr(r, "source_page", ""),
                "raw_text": (getattr(r, "raw_text", "") or "")[:120],
            })
    return out


def _collect_period_keys(records, period_regime: str) -> list[str]:
    keys: set[str] = set()
    for r in records:
        d = parse_date(getattr(r, "trade_date", "") or getattr(r, "date", ""))
        if d is None:
            continue
        keys.add(period_keys_for(d)[period_regime])
    return sorted(keys)


# ---- 主流程 ----

def main() -> int:
    args = parse_args()
    source_root = Path(args.source_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # 密码候选
    password_candidates: list[str] = []
    if args.password:
        password_candidates.append(args.password)
    # 尝试从 config 加载更多密码
    config_path = Path(args.config) if args.config else source_root / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            for p in cfg.get("passwords", []):
                if p not in password_candidates:
                    password_candidates.append(p)
        except (json.JSONDecodeError, OSError):
            pass

    config = {
        "markets": ["HK", "US"],
        "period_regimes": ["china_calendar_year", "hong_kong_fiscal_year"],
        "cost_methods": ["fifo", "period_weighted_average"],
    }
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            for k in config:
                if k in cfg:
                    config[k] = cfg[k]
        except (json.JSONDecodeError, OSError):
            pass

    print(f"=== 海外券商税务底稿生成（统一入口）===")
    print(f"源目录: {source_root}")
    print(f"输出目录: {output_dir}")
    print(f"密码候选: {len(password_candidates)} 个")
    print()

    # Step 1: 扫描文件
    print("步骤1: 扫描源文件...")
    sources = scan_sources(source_root)
    print(f"  找到 {len(sources)} 个文件")

    # Step 2: 加载 parser 注册表
    print("\n步骤2: 加载 parser 注册表...")
    parsers = _load_parsers()
    if not parsers:
        print("  错误: 无可用 parser")
        return 1
    print(f"  已加载 {len(parsers)} 个 parser: {[p.broker for p in parsers]}")

    # Step 3: 自动识别并解析
    print("\n步骤3: 自动识别券商并解析...")
    results, errors, broker_stats = parse_all_sources(sources, parsers, password_candidates)
    print(f"  解析成功: {len(results)} 个文件")
    print(f"  解析失败: {len(errors)} 个文件")
    for broker, count in sorted(broker_stats.items()):
        print(f"    {broker}: {count} 个文件")
    for e in errors[:5]:
        print(f"    错误: {e['file']} - {e.get('detail', e.get('reason', ''))}")
    if len(errors) > 5:
        print(f"    ... 还有 {len(errors) - 5} 个错误")
    print()

    # Step 4: 收集记录 + 去重
    print("步骤4: 收集记录并去重...")
    all_trades, all_income, all_financing, source_files_meta = collect_records(results)
    print(f"  交易: {len(all_trades)} 笔")
    print(f"  收入: {len(all_income)} 笔")
    print(f"  融资利息: {len(all_financing)} 笔")
    print()

    # Step 5: 名称回填
    print("步骤5: 名称回填...")
    all_records_for_master = list(all_trades) + list(all_income) + list(all_financing)
    # 加入 holdings 用于名称回填
    from types import SimpleNamespace
    for res in results:
        for h in res.get("holdings", []):
            all_records_for_master.append(SimpleNamespace(
                market=h.get("market", ""),
                code=h.get("code", ""),
                name=h.get("name", ""),
            ))
    all_records_for_master, master = _backfill_names_all(all_records_for_master)
    all_trades = backfill_names(all_trades, master)
    all_income = backfill_names(all_income, master)
    name_excs = _name_exceptions(all_trades) + _name_exceptions(all_income)
    print(f"  证券数: {len(master)}")
    print(f"  名称异常: {len(name_excs)} 个")
    print()

    # Step 6: 验证
    validations = validate_records(all_trades) + validate_records(all_income)

    # Step 7: 生成工作簿
    print("步骤7: 生成税务底稿工作簿...")
    markets = config["markets"]
    period_regimes = config["period_regimes"]
    cost_methods = config["cost_methods"]

    outputs: list[str] = []
    blocking_errors: list[str] = []
    warnings: list[str] = []
    exceptions_summary: Counter = Counter()

    if not results:
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
            for cost_method in cost_methods:
                if cost_method == "fifo":
                    calc = calculate_fifo(market_trades, market)
                    rows = calc.rows
                    exceptions = list(calc.exceptions)
                else:
                    calc = calculate_period_weighted_average(
                        market_trades, market, period_regime,
                        opening_positions={},
                    )
                    rows = calc.rows
                    exceptions = list(calc.exceptions)

                for e in exceptions:
                    exceptions_summary[e.get("type", "unknown")] += 1

                filename = output_filename(market, period_regime, cost_method)
                target = output_dir / filename
                period_label = "中国自然年" if period_regime == "china_calendar_year" else "香港财年"
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
                print(f"  生成: {filename}")

    print()

    # Step 8: 生成运行报告
    print("步骤8: 生成运行报告...")
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "output_dir": str(output_dir),
        "scanned_files": len(sources),
        "parsed_files": len(results),
        "failed_files": len(errors),
        "broker_stats": broker_stats,
        "trade_count": len(all_trades),
        "income_count": len(all_income),
        "financing_count": len(all_financing),
        "security_count": len(master),
        "blocking_errors": blocking_errors,
        "warnings": warnings,
        "exceptions_summary": dict(exceptions_summary),
        "outputs": outputs,
        "failed_details": errors,
    }
    write_run_report(output_dir, report)
    print(f"  生成: 运行报告.md")
    print(f"  生成: run_report.json")
    print()
    print(f"=== 完成! 共生成 {len(outputs)} 个工作簿 ===")
    return 0


def _backfill_names_all(records):
    master = build_security_master(records)
    out = backfill_names(records, master)
    return out, master


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="海外券商税务底稿生成（统一入口，自动识别券商）"
    )
    parser.add_argument("--source-root", default=".", help="源文件夹")
    parser.add_argument("--output-dir", default="税务底稿输出", help="输出文件夹")
    parser.add_argument("--password", default="", help="PDF 密码")
    parser.add_argument("--config", default="", help="配置文件路径")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
