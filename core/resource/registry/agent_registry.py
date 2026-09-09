"""Agent 注册中心

【归属】Agent 是**共享资源**，不做按用户的隔离。

Agent 是代码资产 + 编排逻辑，实现代码位于
``resources/agents/implementations/``，随项目目录一起复制，代码本身完整，
因此不存在"看得见但跑不了"的问题（这是它与数据集的关键区别）。

注意：Agent 执行时若通过工具访问数据集，仍受数据集的私有归属约束——
由 ``core/api/implementations/api_data.py`` 统一按 owner 过滤。
共享的是 Agent 本身，不是它要访问的数据。

详见 ``core/user_context.py`` 顶部的归属模型说明。
"""

import json
import os
import time
from pathlib import Path

from core.resource.registry.registry_base import BaseRegistry
from core.user_context import get_current_user_id, is_visible_to, VISIBILITY_PUBLIC

REGISTRY_DIR = Path(__file__).resolve().parent.parent.parent.parent / "resources" / "agents"
REGISTRY_FILE = REGISTRY_DIR / "registry.json"


class AgentRegistry(BaseRegistry):
    """Agent 注册中心"""

    def __init__(self):
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        if not REGISTRY_FILE.exists():
            self._write([])

    def _get_spec_dir(self) -> Path:
        """获取 MD 规范文档目录"""
        return REGISTRY_DIR / "definitions"

    def _get_impl_dir(self) -> Path:
        """获取 Agent 实现代码根目录"""
        return REGISTRY_DIR / "implementations"

    def _read(self) -> list[dict]:
        with open(REGISTRY_FILE, encoding='utf-8') as f:
            return json.load(f)

    def _write(self, data: list[dict]):
        with open(REGISTRY_FILE, "w", encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def register(self, resource: dict) -> str:
        agent_id = resource.get("id", f"agent-{int(time.time())}")
        entry = {
            "id": agent_id,
            "name": resource.get("name", agent_id),
            "version": resource.get("version", "0.1.0"),
            "role": resource.get("role", "task"),
            "status": "active",
            "spec_path": f"definitions/{agent_id}.md",
            "impl_path": f"implementations/{agent_id}/",
            "tools": resource.get("required_tools", []),
            "tags": resource.get("tags", []),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "health": "healthy",
            # 归属与可见性：Agent 默认共享（public），owner 记录创建者。
            # 未来若要支持「私有 Agent / 公共池」，改 visibility 即可，
            # 判定逻辑已预置在 is_visible() 中。
            "owner": get_current_user_id(),
            "visibility": VISIBILITY_PUBLIC,
        }

        data = self._read()
        existing = [i for i, e in enumerate(data) if e["id"] == agent_id]
        if existing:
            data[existing[0]] = entry
        else:
            data.append(entry)
        self._write(data)

        # 保存 MD 规范文档
        if "raw_md" in resource:
            spec_path = REGISTRY_DIR / "definitions" / f"{agent_id}.md"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            with open(spec_path, "w", encoding='utf-8') as f:
                f.write(resource["raw_md"])

        return agent_id

    async def unregister(self, resource_id: str) -> None:
        data = self._read()
        data = [e for e in data if e["id"] != resource_id]
        self._write(data)

    async def get(self, resource_id: str) -> dict | None:
        data = self._read()
        for e in data:
            if e["id"] == resource_id:
                return e
        return None

    def is_visible(self, entry: dict, user_id: str) -> bool:
        """Agent 对指定用户是否可见。

        Agent 默认共享（public），历史条目缺 visibility 时也按 public 处理，
        因此当前所有 Agent 对所有人可见——与加字段前的行为一致。
        未来若支持私有 Agent，把 visibility 设为 private 即可。
        """
        return is_visible_to(entry, "agent", user_id)

    async def list_all(self, user_id: str | None = None) -> list[dict]:
        """列出 Agent。

        Args:
            user_id: 传入则只返回对该用户可见的 Agent（public + 自己的 private）。
                     不传则返回全部。当前 Agent 默认 public，故传与不传结果相同。
        """
        data = self._read()
        if user_id is None:
            return data
        return [e for e in data if self.is_visible(e, user_id)]

    async def update(self, resource_id: str, updates: dict) -> None:
        data = self._read()
        for e in data:
            if e["id"] == resource_id:
                e.update(updates)
        self._write(data)
