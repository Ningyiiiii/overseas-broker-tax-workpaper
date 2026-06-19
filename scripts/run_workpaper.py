"""Command wrapper for the overseas broker tax workpaper skill.

This is a scaffold entry point. Implement the parser, engine, output, and report
modules behind this stable command contract.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate overseas broker tax workpapers.")
    parser.add_argument("--source-root", default=".", help="Folder to scan recursively.")
    parser.add_argument("--output-dir", default="outputs", help="Folder for generated workbooks.")
    parser.add_argument("--config", default="config/config.json", help="Optional local config file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = Path(args.source_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    print(f"source_root={source_root}")
    print(f"output_dir={output_dir}")
    print("TODO: wire scan_sources, parsers, engines, workbook builder, validators, and reports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
