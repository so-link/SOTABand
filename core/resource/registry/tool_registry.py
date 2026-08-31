"""工具注册中心

【归属】工具是**共享资源**，不做按用户的隔离。

工具是代码资产，实现代码位于 ``resources/tools/implementations/``，
随项目目录一起复制，代码本身完整，不存在"看得见但跑不了"的问题
（这是它与数据集的关键区别）。

注意：工具执行时若访问数据集，仍受数据集的私有归属约束——
由 ``core/api/implementations/api_data.py`` 统一按 owner 过滤。
共享的是工具本身，不是它要访问的数据。

详见 ``core/user_context.py`` 顶部的归属模型说明。
"""

import json
import time
from pathlib import Path

from core.user_context import get_current_user_id, is_visible_to, VISIBILITY_PUBLIC

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
        with open(REGISTRY_FILE, encoding='utf-8') as f:
            return json.load(f)

    def _write(self, data: list[dict]):
        with open(REGISTRY_FILE, "w", encoding='utf-8') as f:
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
            # 归属与可见性：工具默认共享（public），owner 记录创建者。
            # 未来若要支持「私有工具 / 公共池」，改 visibility 即可，
            # 判定逻辑已预置在 is_visible() 中。
            "owner": get_current_user_id(),
            "visibility": VISIBILITY_PUBLIC,
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

    def is_visible(self, entry: dict, user_id: str) -> bool:
        """工具对指定用户是否可见。

        工具默认共享（public），历史条目缺 visibility 时也按 public 处理，
        因此当前所有工具对所有人可见——与加字段前的行为一致。
        未来若支持私有工具，把 visibility 设为 private 即可。
        """
        return is_visible_to(entry, "tool", user_id)

    async def list_all(self, user_id: str | None = None) -> list[dict]:
        """列出工具。

        Args:
            user_id: 传入则只返回对该用户可见的工具（public + 自己的 private）。
                     不传则返回全部。当前工具默认 public，故传与不传结果相同。
        """
        data = self._read()
        if user_id is None:
            return data
        return [e for e in data if self.is_visible(e, user_id)]

    async def update(self, tool_id: str, updates: dict):
        data = self._read()
        for e in data:
            if e["id"] == tool_id:
                e.update(updates)
        self._write(data)
