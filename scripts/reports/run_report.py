"""Run report helpers."""

from __future__ import annotations

import json
from pathlib import Path


def write_run_report(output_dir: Path, report: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "run_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = ["# 运行报告", ""]
    lines.append(f"- 生成时间: {report.get('generated_at','')}")
    lines.append(f"- 源目录: {report.get('source_root','')}")
    lines.append(f"- 输出目录: {report.get('output_dir','')}")
    lines.append(f"- 扫描文件数: {report.get('scanned_files', 0)}")
    lines.append(f"- 成功解析: {report.get('parsed_files', 0)}")
    lines.append(f"- 解析失败: {report.get('failed_files', 0)}")
    lines.append(f"- 交易笔数: {report.get('trade_count', 0)}")
    lines.append(f"- 股息/分派笔数: {report.get('income_count', 0)}")
    lines.append(f"- 融资利息笔数: {report.get('financing_count', 0)}")
    lines.append(f"- 证券数: {report.get('security_count', 0)}")
    lines.append("")
    lines.append("## 阻塞错误")
    blocking = report.get("blocking_errors") or []
    if not blocking:
        lines.append("- 无")
    else:
        for err in blocking:
            lines.append(f"- {err}")
    lines.append("")
    lines.append("## 警告")
    warnings = report.get("warnings") or []
    if not warnings:
        lines.append("- 无")
    else:
        for w in warnings:
            lines.append(f"- {w}")
    lines.append("")
    lines.append("## 异常 (按类型统计)")
    exceptions = report.get("exceptions_summary") or {}
    if not exceptions:
        lines.append("- 无")
    else:
        for k, v in exceptions.items():
            lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## 输出文件")
    outputs = report.get("outputs") or []
    if not outputs:
        lines.append("- 无")
    else:
        for o in outputs:
            lines.append(f"- {o}")
    (output_dir / "运行报告.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
