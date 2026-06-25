"""华盛通税务底稿生成主程序。

扫描 D:\\TRAE\\LLB2026_HST 目录下的所有 PDF 月结单/日结单文件，
生成港股/美股 × 中国自然年/香港财年 × FIFO/期间加权平均成本法 共8个税务底稿工作簿。

输出目录: D:\\TRAE\\LLB2026_HST\\税务底稿输出
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# 将当前脚本所在目录加入 sys.path 以导入同目录下的模块
sys.path.insert(0, str(Path(__file__).parent))

from huasheng_parser import parse_all_pdfs, ParsedStatement
from workbook_builder import build_workbook


def main():
    # 源目录: 上级目录（包含所有 PDF 月结单）
    source_root = Path(r"d:\TRAE\LLB2026_HST")
    # 输出目录: 在源目录下新建一个子文件夹
    output_dir = source_root / "税务底稿输出"
    output_dir.mkdir(exist_ok=True)
    # PDF 密码
    password = "5147"

    print("=" * 60)
    print("=== 华盛通海外券商税务底稿生成 ===")
    print("=" * 60)
    print(f"源目录: {source_root.resolve()}")
    print(f"输出目录: {output_dir.resolve()}")
    print(f"PDF密码: {'*' * len(password)}")
    print()

    # Step 1: 解析所有 PDF 文件
    print("步骤1: 解析PDF文件...")
    statements, errors = parse_all_pdfs(source_root, password)
    print(f"  解析完成: {len(statements)} 个文件成功, {len(errors)} 个错误")
    if errors:
        print("  错误明细:")
        for e in errors:
            print(f"    - {e['file']}: {e['error']}")
    print()

    # 收集所有记录（parser 已内置去重和名称回填）
    all_trades = []
    all_incomes = []
    all_financing = []
    for stmt in statements:
        all_trades.extend(stmt.trades)
        all_incomes.extend(stmt.incomes)
        all_financing.extend(stmt.financing_interests)

    print(f"  总计: {len(all_trades)} 笔交易, {len(all_incomes)} 笔收入, {len(all_financing)} 笔融资利息")
    print()

    # 统计市场覆盖
    markets_present = set(t.market for t in all_trades) | set(i.market for i in all_incomes)
    print(f"  市场覆盖: {sorted(markets_present)}")

    # 按市场分组统计交易
    hk_trades = [t for t in all_trades if t.market == "HK"]
    us_trades = [t for t in all_trades if t.market == "US"]
    print(f"  港股交易: {len(hk_trades)} 笔")
    print(f"  美股交易: {len(us_trades)} 笔")
    print()

    # 按期间统计
    from tax_engines import get_period_key
    cy_periods = set()
    fy_periods = set()
    for t in all_trades:
        cy_periods.add(get_period_key(t.trade_date, "china_calendar_year"))
        fy_periods.add(get_period_key(t.trade_date, "hong_kong_fiscal_year"))
    for inc in all_incomes:
        cy_periods.add(get_period_key(inc.date, "china_calendar_year"))
        fy_periods.add(get_period_key(inc.date, "hong_kong_fiscal_year"))
    for fin in all_financing:
        cy_periods.add(get_period_key(fin.date, "china_calendar_year"))
        fy_periods.add(get_period_key(fin.date, "hong_kong_fiscal_year"))

    print(f"  中国自然年期间: {sorted(cy_periods)}")
    print(f"  香港财年期间: {sorted(fy_periods)}")
    print()

    # Step 2: 生成8个工作簿
    print("步骤2: 生成税务底稿工作簿...")
    outputs = []
    markets_to_generate = ["HK", "US"]
    for market in markets_to_generate:
        for period_regime in ["china_calendar_year", "hong_kong_fiscal_year"]:
            for cost_method in ["fifo", "period_weighted_average"]:
                market_label = {"HK": "港股", "US": "美股"}.get(market, market)
                regime_label = {
                    "china_calendar_year": "中国自然年",
                    "hong_kong_fiscal_year": "香港财年"
                }.get(period_regime, period_regime)
                method_label = {
                    "fifo": "FIFO",
                    "period_weighted_average": "期间加权平均成本法"
                }.get(cost_method, cost_method)
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

    # Step 3: 生成运行报告
    print("步骤3: 生成运行报告...")
    report = {
        "运行时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "券商": "华盛通 (Valuable Capital Limited)",
        "源目录": str(source_root.resolve()),
        "扫描文件数": len(statements) + len(errors),
        "解析成功": len(statements),
        "解析失败": len(errors),
        "交易总数": len(all_trades),
        "收入总数": len(all_incomes),
        "融资利息总数": len(all_financing),
        "港股交易数": len(hk_trades),
        "美股交易数": len(us_trades),
        "市场覆盖": sorted(markets_present),
        "中国自然年期间": sorted(cy_periods),
        "香港财年期间": sorted(fy_periods),
        "输出文件": outputs,
        "警告": [],
        "阻塞错误": [],
    }

    # 添加警告
    if errors:
        report["警告"].append(f"{len(errors)} 个文件解析失败")
    if "US" not in markets_present:
        report["警告"].append("无美股交易数据，美股工作簿为空")
    if not all_financing:
        report["警告"].append("无融资利息数据")

    # 添加解析失败文件明细到报告
    if errors:
        report["解析失败明细"] = [{"文件": e["file"], "错误": e["error"]} for e in errors]

    # 写 JSON 报告
    report_path = output_dir / "run_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 写 Markdown 报告
    md_path = output_dir / "运行报告.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# 华盛通税务底稿运行报告\n\n")
        f.write(f"- **运行时间**: {report['运行时间']}\n")
        f.write(f"- **券商**: {report['券商']}\n")
        f.write(f"- **源目录**: {report['源目录']}\n")
        f.write(f"- **扫描文件数**: {report['扫描文件数']}\n")
        f.write(f"- **解析成功**: {report['解析成功']}\n")
        f.write(f"- **解析失败**: {report['解析失败']}\n")
        f.write(f"- **交易总数**: {report['交易总数']}\n")
        f.write(f"- **收入总数**: {report['收入总数']}\n")
        f.write(f"- **融资利息总数**: {report['融资利息总数']}\n")
        f.write(f"- **港股交易数**: {report['港股交易数']}\n")
        f.write(f"- **美股交易数**: {report['美股交易数']}\n")
        f.write(f"- **市场覆盖**: {', '.join(report['市场覆盖'])}\n")
        f.write(f"- **中国自然年期间**: {', '.join(report['中国自然年期间'])}\n")
        f.write(f"- **香港财年期间**: {', '.join(report['香港财年期间'])}\n")
        f.write(f"\n## 输出文件\n\n")
        for o in outputs:
            f.write(f"- {o}\n")
        if report["警告"]:
            f.write(f"\n## 警告\n\n")
            for w in report["警告"]:
                f.write(f"- {w}\n")
        if report.get("解析失败明细"):
            f.write(f"\n## 解析失败明细\n\n")
            f.write("| 文件名 | 错误 |\n")
            f.write("|--------|------|\n")
            for item in report["解析失败明细"]:
                f.write(f"| {item['文件']} | {item['错误']} |\n")
        if report["阻塞错误"]:
            f.write(f"\n## 阻塞错误\n\n")
            for e in report["阻塞错误"]:
                f.write(f"- {e}\n")

    print(f"  生成: 运行报告.md")
    print(f"  生成: run_report.json")
    print()
    print("=" * 60)
    print(f"=== 完成! 共生成 {len(outputs)} 个工作簿 ===")
    print(f"=== 输出目录: {output_dir.resolve()} ===")
    print("=" * 60)


if __name__ == "__main__":
    main()
