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
from core.resource import paths
from core.user_context import get_current_user_id, is_visible_to, VISIBILITY_PUBLIC


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


REGISTRY_DIR = paths.BUILTIN_ROOT / "agents"
REGISTRY_FILE = REGISTRY_DIR / "registry.json"


class AgentRegistry(BaseRegistry):
    """Agent 注册中心。

    读：合并「内置」与「私有」两个目录的 registry.json（私有同 id 覆盖内置）。
    写：新建 / 更新的 Agent 一律写入私有目录。
    """

    def __init__(self):
        paths.ensure_private_dirs("agents")
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        if not REGISTRY_FILE.exists():
            _write_json(REGISTRY_FILE, [])

    def _private_registry_file(self) -> Path:
        d = paths.ensure_private_dirs("agents")
        f = d / "registry.json"
        if not f.exists():
            _write_json(f, [])
        return f

    def _get_spec_dir(self) -> Path:
        """MD 规范文档目录（私有）——新建的规范写入此处。"""
        return paths.ensure_private_dirs("agents") / "definitions"

    def _get_impl_dir(self) -> Path:
        """实现代码根目录（私有）——新建的实现写入此处。"""
        return paths.ensure_private_dirs("agents") / "implementations"

    def get_spec_search_dirs(self) -> list[Path]:
        """规范文档的检索目录（内置 + 私有），用于「读」时定位已有 MD。"""
        return [root / "agents" / "definitions" for root in paths.resource_roots()]

    def get_impl_search_dirs(self) -> list[Path]:
        """实现目录的检索目录（内置 + 私有）。"""
        return [root / "agents" / "implementations" for root in paths.resource_roots()]

    def _read(self) -> list[dict]:
        """合并内置与私有 registry（私有同 id 覆盖内置）。"""
        merged: dict[str, dict] = {}
        for root in paths.resource_roots():
            rf = root / "agents" / "registry.json"
            if not rf.exists():
                continue
            try:
                for e in json.loads(rf.read_text(encoding="utf-8")):
                    if isinstance(e, dict) and e.get("id"):
                        merged[e["id"]] = e
            except Exception:
                continue
        return list(merged.values())

    def read_from(self, root: Path) -> list[dict]:
        rf = root / "agents" / "registry.json"
        if not rf.exists():
            return []
        try:
            return json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _write(self, data: list[dict]):
        """写入私有 registry。"""
        _write_json(self._private_registry_file(), data)

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
            spec_path = self._get_spec_dir() / f"{agent_id}.md"
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
