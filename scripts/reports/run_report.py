"""Run report helpers."""

from __future__ import annotations

import json
from pathlib import Path


def write_run_report(output_dir: Path, report: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "run_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 运行报告", ""]
    for key, value in report.items():
        lines.append(f"- {key}: {value}")
    (output_dir / "运行报告.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
