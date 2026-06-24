"""Command wrapper for the overseas broker tax workpaper skill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = SCRIPT_DIR / "runtime"
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

import futu_workpaper_runtime as runtime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate overseas broker tax workpapers.")
    parser.add_argument("--source-root", default=".", help="Folder to scan recursively.")
    parser.add_argument("--output-dir", default="outputs", help="Base folder for timestamped generated workbooks.")
    parser.add_argument("--config", default="config/config.json", help="Optional local config file.")
    parser.add_argument("--password", default="", help="PDF password. Overrides config/passwords.json when set.")
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Stop after normalized parsing audit output; use before final calculations when parser coverage changed.",
    )
    return parser.parse_args()


def load_password(args: argparse.Namespace) -> str:
    pwds = load_passwords(args)
    return pwds[0] if pwds else ""


def load_passwords(args: argparse.Namespace) -> list[str]:
    if args.password:
        return [args.password]
    candidates: list[Path] = []
    config_path = Path(args.config)
    if config_path.exists():
        candidates.append(config_path)
    source_root = Path(args.source_root).resolve()
    candidates.extend(
        [
            Path.cwd() / "config" / "passwords.json",
            source_root / "config" / "passwords.json",
            SCRIPT_DIR.parent / "config" / "passwords.json",
        ]
    )
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        values: list[str] = []
        if isinstance(data, dict):
            for key in ("password", "pdf_password", "futu_pdf_password"):
                if data.get(key):
                    values.append(str(data[key]))
            raw_candidates = data.get("password_candidates")
            if isinstance(raw_candidates, list):
                values.extend(str(item) for item in raw_candidates if item)
            per_broker = data.get("per_broker")
            if isinstance(per_broker, dict):
                for broker_pwds in per_broker.values():
                    if isinstance(broker_pwds, list):
                        values.extend(str(item) for item in broker_pwds if item)
                    elif broker_pwds:
                        values.append(str(broker_pwds))
            per_account = data.get("per_account")
            if isinstance(per_account, dict):
                values.extend(str(v) for v in per_account.values() if v)
            per_file = data.get("per_file")
            if isinstance(per_file, dict):
                values.extend(str(v) for v in per_file.values() if v)
        if values:
            # deduplicate while preserving order
            seen: set[str] = set()
            unique: list[str] = []
            for v in values:
                if v not in seen:
                    seen.add(v)
                    unique.append(v)
            return unique
    return []


def main() -> int:
    args = parse_args()
    source_root = Path(args.source_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    passwords = load_passwords(args)
    runtime.configure_runtime(source_root=source_root, output_dir=output_dir, passwords=passwords)
    report = runtime.run_workpaper(audit_only=args.audit_only)
    print(f"output_root={report.get('output_root')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
