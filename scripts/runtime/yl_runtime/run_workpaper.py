"""Main runner: parse PDFs, run engines, generate workbooks and reports."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from usmart_parser import parse_all_pdfs, ParsedStatement
from workbook_builder import build_workbook


def deduplicate_incomes(statements: list[ParsedStatement]):
    """Remove duplicate income records across statements."""
    seen = set()
    for stmt in statements:
        unique = []
        for inc in stmt.incomes:
            key = (inc.date, inc.code, inc.category, inc.amount, inc.currency)
            if key not in seen:
                seen.add(key)
                unique.append(inc)
        stmt.incomes = unique


def deduplicate_financing_interests(statements: list[ParsedStatement]):
    """Remove duplicate financing interest records across statements.
    Monthly statements may carry forward previous month's interest entries.
    """
    seen = set()
    for stmt in statements:
        unique = []
        for fin in stmt.financing_interests:
            key = (fin.date, fin.currency, fin.amount)
            if key not in seen:
                seen.add(key)
                unique.append(fin)
        stmt.financing_interests = unique


def backfill_names(statements: list[ParsedStatement]):
    """Build security master from trades and backfill missing names by market+code.
    Also normalizes names so all records for the same code use the same name.
    """
    # Traditional -> Simplified mapping for common stock name characters
    t2s = {
        "國": "国", "萊": "莱", "醫": "医", "械": "械", "買": "买", "賣": "卖",
        "結": "结", "單": "单", "証": "证", "券": "券", "寶": "宝", "實": "实",
        "電": "电", "訊": "讯", "氣": "气", "車": "车", "銀": "银", "行": "行",
        "中國": "中国", "石油": "石油", "控股": "控股", "集團": "集团",
    }

    def to_simplified(s: str) -> str:
        for t, sp in t2s.items():
            s = s.replace(t, sp)
        return s

    # Build name map from all trades (prefer Simplified Chinese names)
    name_map: dict[tuple[str, str], str] = {}
    for stmt in statements:
        for t in stmt.trades:
            if t.name and t.code:
                key = (t.market, t.code)
                name = to_simplified(t.name)
                if key not in name_map or name != t.name:
                    # Prefer the simplified version
                    if key not in name_map:
                        name_map[key] = name
                    elif to_simplified(name_map[key]) != name_map[key] and name == to_simplified(name):
                        # Current map value is Traditional, new one is already Simplified
                        name_map[key] = name

    # Normalize all trade names to match the name map
    for stmt in statements:
        for t in stmt.trades:
            if t.code:
                key = (t.market, t.code)
                if key in name_map:
                    t.name = name_map[key]

    # Backfill income names
    for stmt in statements:
        for inc in stmt.incomes:
            if not inc.name and inc.code:
                key = (inc.market, inc.code)
                if key in name_map:
                    inc.name = name_map[key]
            elif inc.name:
                inc.name = to_simplified(inc.name)


def main():
    source_root = Path(".")
    output_dir = Path("税务底稿输出")
    output_dir.mkdir(exist_ok=True)
    password = "910971"

    print(f"=== 海外券商税务底稿生成 ===")
    print(f"源目录: {source_root.resolve()}")
    print(f"输出目录: {output_dir.resolve()}")
    print(f"PDF密码: {'*' * len(password)}")
    print()

    # Step 1: Parse all PDFs
    print("步骤1: 解析PDF文件...")
    statements, errors = parse_all_pdfs(source_root, password)
    print(f"  解析完成: {len(statements)} 个文件成功, {len(errors)} 个错误")
    for e in errors:
        print(f"    错误: {e}")
    print()

    # Deduplicate incomes (same entry may appear in multiple statements)
    deduplicate_incomes(statements)

    # Deduplicate financing interests (monthly statements may carry forward)
    deduplicate_financing_interests(statements)

    # Backfill missing income names from trade records (security master)
    backfill_names(statements)

    # Collect all records
    all_trades = []
    all_incomes = []
    all_financing = []
    for stmt in statements:
        all_trades.extend(stmt.trades)
        all_incomes.extend(stmt.incomes)
        all_financing.extend(stmt.financing_interests)

    print(f"  总计: {len(all_trades)} 笔交易, {len(all_incomes)} 笔收入, {len(all_financing)} 笔融资利息")
    print()

    # Determine markets present
    markets_present = set(t.market for t in all_trades) | set(i.market for i in all_incomes)
    # Always generate HK and US per spec
    markets_to_generate = ["HK", "US"]
    print(f"  市场覆盖: {markets_present}")
    print(f"  生成市场: {markets_to_generate}")
    print()

    # Step 2: Generate workbooks
    print("步骤2: 生成税务底稿工作簿...")
    outputs = []
    for market in markets_to_generate:
        for period_regime in ["china_calendar_year", "hong_kong_fiscal_year"]:
            for cost_method in ["fifo", "period_weighted_average"]:
                market_label = {"HK": "港股", "US": "美股"}.get(market, market)
                regime_label = {"china_calendar_year": "中国自然年", "hong_kong_fiscal_year": "香港财年"}.get(period_regime, period_regime)
                method_label = {"fifo": "FIFO", "period_weighted_average": "期间加权平均成本法"}.get(cost_method, cost_method)
                filename = f"{market_label}_{regime_label}_{method_label}_税务底稿.xlsx"
                output_path = output_dir / filename

                build_workbook(
                    output_path, market, period_regime, cost_method,
                    all_trades, all_incomes, all_financing,
                    statements, errors,
                )
                outputs.append(str(output_path))
                print(f"  生成: {filename}")

    print()

    # Step 3: Generate run report
    print("步骤3: 生成运行报告...")
    report = {
        "运行时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "源目录": str(source_root.resolve()),
        "扫描文件数": len(statements) + len(errors),
        "解析成功": len(statements),
        "解析失败": len(errors),
        "交易总数": len(all_trades),
        "收入总数": len(all_incomes),
        "融资利息总数": len(all_financing),
        "市场覆盖": sorted(markets_present),
        "输出文件": outputs,
        "警告": [],
        "阻塞错误": [],
    }

    # Add warnings
    if errors:
        report["警告"].append(f"{len(errors)} 个文件解析失败")
    market_trades = set(t.market for t in all_trades)
    if "US" not in market_trades:
        report["警告"].append("无美股交易数据，美股工作簿为空")
    if not all_financing:
        report["警告"].append("无融资利息数据")

    # Write JSON report
    report_path = output_dir / "run_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # Write Markdown report
    md_path = output_dir / "运行报告.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# 运行报告\n\n")
        f.write(f"- **运行时间**: {report['运行时间']}\n")
        f.write(f"- **源目录**: {report['源目录']}\n")
        f.write(f"- **扫描文件数**: {report['扫描文件数']}\n")
        f.write(f"- **解析成功**: {report['解析成功']}\n")
        f.write(f"- **解析失败**: {report['解析失败']}\n")
        f.write(f"- **交易总数**: {report['交易总数']}\n")
        f.write(f"- **收入总数**: {report['收入总数']}\n")
        f.write(f"- **融资利息总数**: {report['融资利息总数']}\n")
        f.write(f"- **市场覆盖**: {', '.join(report['市场覆盖'])}\n")
        f.write(f"\n## 输出文件\n\n")
        for o in outputs:
            f.write(f"- {o}\n")
        if report["警告"]:
            f.write(f"\n## 警告\n\n")
            for w in report["警告"]:
                f.write(f"- {w}\n")
        if report["阻塞错误"]:
            f.write(f"\n## 阻塞错误\n\n")
            for e in report["阻塞错误"]:
                f.write(f"- {e}\n")

    print(f"  生成: 运行报告.md")
    print(f"  生成: run_report.json")
    print()
    print(f"=== 完成! 共生成 {len(outputs)} 个工作簿 ===")


if __name__ == "__main__":
    main()
