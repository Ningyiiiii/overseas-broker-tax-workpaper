"""Python workbook builder for overseas broker tax workpapers."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtime"
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

import futu_workpaper_runtime as runtime


def build_workbook(payload: dict[str, Any], output_path: Path | str) -> Path:
    """Write one workbook from a normalized calculation payload."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_workbook(path, payload)
    return path


def main() -> int:
    input_path = os.environ.get("WORKPAPER_INPUT_JSON")
    output_path = os.environ.get("WORKPAPER_OUTPUT_XLSX")
    if not input_path or not output_path:
        print("WORKPAPER_INPUT_JSON and WORKPAPER_OUTPUT_XLSX are required", file=sys.stderr)
        return 2
    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    build_workbook(payload, output_path)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
