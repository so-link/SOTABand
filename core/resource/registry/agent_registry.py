"""Agent 注册中心"""

import json
import os
import time
from pathlib import Path

from core.resource.registry.registry_base import BaseRegistry

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
        with open(REGISTRY_FILE) as f:
            return json.load(f)

    def _write(self, data: list[dict]):
        with open(REGISTRY_FILE, "w") as f:
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
            with open(spec_path, "w") as f:
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

    async def list_all(self) -> list[dict]:
        return self._read()

    async def update(self, resource_id: str, updates: dict) -> None:
        data = self._read()
        for e in data:
            if e["id"] == resource_id:
                e.update(updates)
        self._write(data)
