"""Security master helpers for name backfill."""

from __future__ import annotations

import re

GARBLED_MARKERS = ("�", "\ufffd")


def looks_garbled(text: str) -> bool:
    if not text:
        return False
    return any(marker in text for marker in GARBLED_MARKERS)


def build_security_master(records: list[object]) -> dict[tuple[str, str], str]:
    master: dict[tuple[str, str], str] = {}
    for record in records:
        market = getattr(record, "market", "")
        code = getattr(record, "code", "")
        name = getattr(record, "name", "")
        if market and code and name and not looks_garbled(str(name)):
            master.setdefault((str(market), str(code)), str(name).strip())
    return _merge_hk_fund_codes(master)


def _merge_hk_fund_codes(master: dict[tuple[str, str], str]) -> dict[tuple[str, str], str]:
    """If both a HK fund prefix and its tail appear as separate keys, merge them.

    Heuristic: an HK fund code is HK + 10 digits. The PDF sometimes wraps
    the code across lines. We merge when the joined code is exactly HK + 10
    digits and the tail is purely numeric.
    """
    out = dict(master)
    hk_keys = [k for k in out if k[1].startswith("HK") and k[1][2:].isdigit()]
    for prefix_key in hk_keys:
        prefix = prefix_key[1]
        prefix_digits = prefix[2:]
        for tail_key in list(out.keys()):
            tail = tail_key[1]
            if not tail.isdigit() or tail_key == prefix_key or tail_key[0] != prefix_key[0]:
                continue
            # HK fund code is HK + 10 digits.
            if len(prefix_digits) + len(tail) != 10:
                continue
            full_code = prefix + tail
            if (prefix_key[0], full_code) not in out:
                out[(prefix_key[0], full_code)] = (
                    out[prefix_key] + " " + out[tail_key]
                ).strip()
            if prefix_key in out:
                out.pop(prefix_key, None)
            if tail_key in out:
                out.pop(tail_key, None)
            break
    return out


def backfill_names(records: list[object], master: dict[tuple[str, str], str]) -> list[object]:
    out: list[object] = []
    for record in records:
        name = getattr(record, "name", "")
        market = getattr(record, "market", "")
        code = getattr(record, "code", "")
        if (not name or looks_garbled(str(name))) and master.get((str(market), str(code))):
            new_kwargs = dict(record.__dict__)
            new_kwargs["name"] = master[(str(market), str(code))]
            out.append(type(record)(**new_kwargs))
        else:
            out.append(record)
    return out
