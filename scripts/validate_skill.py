"""Local skill validator without external dependencies.

This mirrors the essential checks from skill-creator's quick_validate.py, while
reading SKILL.md as UTF-8 so it works reliably on Windows folders containing
Chinese content.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ALLOWED_FRONTMATTER_KEYS = {"name", "description", "license", "allowed-tools", "metadata"}
MAX_SKILL_NAME_LENGTH = 64


def parse_simple_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("No YAML frontmatter found")
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        raise ValueError("Invalid frontmatter format")
    frontmatter: dict[str, str] = {}
    for raw in match.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Unsupported frontmatter line: {line}")
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip("\"'")
    return frontmatter


def validate_skill(skill_path: Path) -> tuple[bool, str]:
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    frontmatter = parse_simple_frontmatter(skill_md.read_text(encoding="utf-8"))
    unexpected = set(frontmatter) - ALLOWED_FRONTMATTER_KEYS
    if unexpected:
        allowed = ", ".join(sorted(ALLOWED_FRONTMATTER_KEYS))
        return False, f"Unexpected frontmatter keys: {', '.join(sorted(unexpected))}. Allowed: {allowed}"

    name = frontmatter.get("name", "").strip()
    description = frontmatter.get("description", "").strip()
    if not name:
        return False, "Missing 'name' in frontmatter"
    if not description:
        return False, "Missing 'description' in frontmatter"
    if not re.match(r"^[a-z0-9-]+$", name):
        return False, f"Name '{name}' should be hyphen-case lowercase letters, digits, and hyphens"
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False, f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens"
    if len(name) > MAX_SKILL_NAME_LENGTH:
        return False, f"Name is too long ({len(name)} characters)"
    if "<" in description or ">" in description:
        return False, "Description cannot contain angle brackets"
    if len(description) > 1024:
        return False, f"Description is too long ({len(description)} characters)"

    required = [
        "agents/openai.yaml",
        "references/algorithm_spec.md",
        "references/output_workbook_spec.md",
        "references/normalized_schema.md",
        "references/error_handling.md",
        "references/broker_parser_contract.md",
        "references/futu_known_patterns.md",
        "references/fx_rate_rules.md",
        "references/validation_checklist.md",
        "scripts/run_workpaper.py",
    ]
    missing = [path for path in required if not (skill_path / path).exists()]
    if missing:
        return False, "Missing required files: " + ", ".join(missing)

    return True, "Skill is valid!"


def main() -> int:
    skill_path = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    try:
      valid, message = validate_skill(skill_path)
    except Exception as exc:
      valid, message = False, str(exc)
    print(message)
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
