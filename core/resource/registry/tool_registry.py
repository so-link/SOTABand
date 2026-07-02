"""工具注册中心"""

import json
import time
from pathlib import Path

from core.resource.registry.registry_base import BaseRegistry

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "resources" / "tools"
REGISTRY_FILE = TOOLS_DIR / "registry.json"


class ToolRegistry(BaseRegistry):
    """工具注册中心"""

    def __init__(self):
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        if not REGISTRY_FILE.exists():
            self._write([])

    def _read(self) -> list[dict]:
        with open(REGISTRY_FILE) as f:
            return json.load(f)

    def _write(self, data: list[dict]):
        with open(REGISTRY_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_def_dir(self) -> Path:
        return TOOLS_DIR / "definitions"

    def _get_impl_dir(self) -> Path:
        return TOOLS_DIR / "implementations"

    async def register(self, resource: dict) -> str:
        tool_id = resource.get("id", f"tool-{int(time.time())}")
        entry = {
            "id": tool_id,
            "name": resource.get("name", tool_id),
            "version": resource.get("version", "0.1.0"),
            "type": resource.get("type", "function"),
            "language": resource.get("language", "python"),
            "status": "active",
            "spec_path": f"definitions/{tool_id}.md",
            "impl_path": f"implementations/{tool_id}/",
            "input_schema": resource.get("input_schema", {}),
            "output_schema": resource.get("output_schema", {}),
            "param_meta": resource.get("param_meta", []),
            "tags": resource.get("tags", []),
            "usage_count": 0,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        data = self._read()
        existing = [i for i, e in enumerate(data) if e["id"] == tool_id]
        if existing:
            data[existing[0]] = entry
        else:
            data.append(entry)
        self._write(data)

        # 保存 MD 规范文档
        if "raw_md" in resource:
            spec_path = self._get_def_dir() / f"{tool_id}.md"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(resource["raw_md"])

        # 保存代码
        if resource.get("code", "").strip():
            impl_dir = self._get_impl_dir() / tool_id
            impl_dir.mkdir(parents=True, exist_ok=True)
            (impl_dir / "tool.py").write_text(resource["code"])
            if "raw_md" in resource:
                (impl_dir / "spec.md").write_text(resource["raw_md"])
            # 保存测试数据
            if resource.get("test_data"):
                tests_dir = impl_dir / "tests"
                tests_dir.mkdir(exist_ok=True)
                for name, data in resource["test_data"].items():
                    if data:
                        (tests_dir / f"test_{name}.json").write_text(
                            json.dumps(data, ensure_ascii=False, indent=2)
                        )

        return tool_id

    async def unregister(self, tool_id: str):
        data = self._read()
        data = [e for e in data if e["id"] != tool_id]
        self._write(data)

    async def get(self, tool_id: str) -> dict | None:
        for e in self._read():
            if e["id"] == tool_id:
                return e
        return None

    async def list_all(self) -> list[dict]:
        return self._read()

    async def update(self, tool_id: str, updates: dict):
        data = self._read()
        for e in data:
            if e["id"] == tool_id:
                e.update(updates)
        self._write(data)
