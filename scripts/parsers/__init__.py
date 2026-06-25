"""券商 parser 注册表。

每个 parser 必须实现 BrokerParser Protocol:
- broker: str 属性
- can_parse(path: Path) -> bool  # 内容级探测
- parse(path: Path, password_candidates: list[str]) -> dict

注册顺序很重要：更具体的 parser 放在前面，通用/兜底的放在后面。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


_PARSER_INSTANCES: list[Any] | None = None


def _get_parsers() -> list[Any]:
    """懒加载所有已注册的 parser 实例。返回按优先级排序的列表。"""
    global _PARSER_INSTANCES
    if _PARSER_INSTANCES is not None:
        return _PARSER_INSTANCES

    instances: list[Any] = []

    # 按优先级顺序加载（更具体的在前）
    try:
        from .huatai_parser import HuataiParser
        instances.append(HuataiParser())
    except ImportError:
        pass

    try:
        from .huasheng_parser import HuashengParser
        instances.append(HuashengParser())
    except ImportError:
        pass

    try:
        from .usmart_parser import UsmartParser
        instances.append(UsmartParser())
    except ImportError:
        pass

    _PARSER_INSTANCES = instances
    return instances


def select_parser(path: Path) -> Any | None:
    """遍历注册表，返回第一个 can_parse 命中的 parser。"""
    for parser in _get_parsers():
        try:
            if parser.can_parse(path):
                return parser
        except Exception:
            continue
    return None


def list_parsers() -> list[str]:
    """返回所有已注册 parser 的 broker 名称。"""
    return [p.broker for p in _get_parsers()]
