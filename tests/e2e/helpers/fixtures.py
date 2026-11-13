"""读取编排器准备的资源夹具。"""

from __future__ import annotations

import json
import os
from pathlib import Path


def load_fixtures(path: Path | None = None) -> dict:
    raw = str(path or os.environ.get("E2E_FIXTURES", "")).strip()
    if not raw:
        return {}
    target = Path(raw)
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def datasets(fixtures: dict) -> list[dict]:
    return [item for item in (fixtures.get("datasets") or []) if item.get("id")]


def pick_dataset(fixtures: dict, *, fmt: str | None = None) -> dict:
    items = datasets(fixtures)
    if not items:
        raise AssertionError("E2E 夹具中没有可用数据集")
    if fmt:
        for item in items:
            formats = [str(x).lower() for x in (item.get("formats") or [])]
            if fmt.lower() in formats:
                return item
    return items[0]


def pick_tool(fixtures: dict, tool_id: str | None = None) -> dict:
    items = [item for item in (fixtures.get("tools") or []) if item.get("id")]
    if not items:
        raise AssertionError("E2E 夹具中没有可用工具")
    if tool_id:
        for item in items:
            if item["id"] == tool_id:
                return item
    return items[0]


def pick_agent(fixtures: dict, agent_id: str | None = None) -> dict:
    items = [item for item in (fixtures.get("agents") or []) if item.get("id")]
    if not items:
        raise AssertionError("E2E 夹具中没有可用 Agent")
    if agent_id:
        for item in items:
            if item["id"] == agent_id:
                return item
    return items[0]
