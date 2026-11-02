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
import threading
import time
from pathlib import Path

from core.user_context import get_current_user_id, is_visible_to, VISIBILITY_PUBLIC

from core.resource.registry.registry_base import BaseRegistry
from core.resource import paths
from core.resource.json_store import write_json_atomic


def _write_json(path: Path, data) -> None:
    """写入 JSON 文件（原子替换，自动创建父目录）。"""
    write_json_atomic(path, data)


# 内置资源根（随仓库分发）与私有资源根（本地新建，不入 git）
TOOLS_DIR = paths.BUILTIN_ROOT / "tools"
REGISTRY_FILE = TOOLS_DIR / "registry.json"

# registry.json 读-改-写锁：并发注册/删除时防止后写覆盖前写丢条目
_WRITE_LOCK = threading.Lock()


class ToolRegistry(BaseRegistry):
    """工具注册中心。

    读：合并「内置」与「私有」两个目录的 registry.json（私有在后，同 id 覆盖内置）。
    写：新建 / 更新的工具一律写入私有目录，保证本地新建资源不污染内置资源。
    """

    def __init__(self):
        paths.ensure_private_dirs("tools")
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        if not REGISTRY_FILE.exists():
            _write_json(REGISTRY_FILE, [])

    # ── 读写：读合并、写私有 ──

    def _private_registry_file(self) -> Path:
        d = paths.ensure_private_dirs("tools")
        f = d / "registry.json"
        if not f.exists():
            _write_json(f, [])
        return f

    def _read(self) -> list[dict]:
        """合并内置与私有 registry（私有同 id 覆盖内置），并注入使用计数。

        返回的条目是深拷贝视图：调用方（如 list_all 后前端展示、执行器
        读参数）可随意改，不会意外成为写路径。
        """
        merged: dict[str, dict] = {}
        for root in paths.resource_roots():
            rf = root / "tools" / "registry.json"
            if not rf.exists():
                continue
            try:
                for e in json.loads(rf.read_text(encoding="utf-8")):
                    if isinstance(e, dict) and e.get("id"):
                        merged[e["id"]] = e
            except Exception:
                continue
        data = list(merged.values())
        usage = self._read_usage()
        for e in data:
            if e["id"] in usage:
                e["usage_count"] = usage[e["id"]]
        return data

    def _read_private(self) -> list[dict]:
        """只读私有 registry（不合并内置）。

        写路径（register/update/bump_usage_count）必须基于本方法而非 _read：
        _read 的合并视图含全部内置条目，写回等于把内置条目批量复制进私有
        目录——一次 update 就会让私有 registry 混入几十个陈旧副本，后果：
        ① 内置资源后续更新被陈旧副本永久遮蔽（如天气工具新加的参数界
        面看不到）；② 内置目录中已删除的工具被私有副本"复活"；③ 私有
        文件无意义膨胀。
        """
        rf = self._private_registry_file()
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            return []
        return [e for e in data if isinstance(e, dict) and e.get("id")]

    def _write(self, data: list[dict]):
        """写入私有 registry（内置资源不在此维护）。"""
        _write_json(self._private_registry_file(), data)

    def read_from(self, root: Path) -> list[dict]:
        """读取指定根目录下的 registry（用于区分内置 / 私有）。"""
        rf = root / "tools" / "registry.json"
        if not rf.exists():
            return []
        try:
            return json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _get_def_dir(self) -> Path:
        return paths.ensure_private_dirs("tools") / "definitions"

    def _get_impl_dir(self) -> Path:
        return paths.ensure_private_dirs("tools") / "implementations"

    # ── 多目录定位（读：内置优先，找不到再查私有）──
    # 内置工具（随仓库分发的示例工具）的 spec/代码只存在于 resources/ 下，
    # 私有工具才在 resources-private/ 下。打开详情若只用私有目录会读不到内置
    # 工具的内容 → 界面空白。下面两个方法给出「实际存在」的路径。

    def find_def_file(self, tool_id: str, suffix: str = ".md") -> Path | None:
        """在内置 / 私有目录中定位工具的定义文件（存在才返回）。"""
        for root in paths.resource_roots():
            p = root / "tools" / "definitions" / f"{tool_id}{suffix}"
            if p.exists():
                return p
        return None

    def find_impl_dir(self, tool_id: str) -> Path | None:
        """在内置 / 私有目录中定位工具的实现目录（含 tool.py 才返回）。"""
        for root in paths.resource_roots():
            d = root / "tools" / "implementations" / tool_id
            if (d / "tool.py").exists():
                return d
        return None

    def read_def_text(self, tool_id: str, suffix: str = ".md") -> tuple[str, bool]:
        """读取工具定义文件内容，返回 (内容, 是否存在)。"""
        p = self.find_def_file(tool_id, suffix)
        if p is None:
            return "", False
        try:
            return p.read_text(encoding="utf-8"), True
        except Exception:
            return "", False

    def read_code_text(self, tool_id: str) -> str:
        """读取工具实现代码（tool.py）；内置 / 私有均支持。"""
        d = self.find_impl_dir(tool_id)
        if d is None:
            return ""
        try:
            return (d / "tool.py").read_text(encoding="utf-8")
        except Exception:
            return ""

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
            # 多模式支持：modes 列表（含 id/name/desc）；单模式工具为空或缺省
            "modes": resource.get("modes", []),
            "tags": resource.get("tags", []),
            "usage_count": 0,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            # 归属与可见性：工具默认共享（public），owner 记录创建者。
            # 未来若要支持「私有工具 / 公共池」，改 visibility 即可，
            # 判定逻辑已预置在 is_visible() 中。
            "owner": get_current_user_id(),
            "visibility": VISIBILITY_PUBLIC,
        }

        with _WRITE_LOCK:
            data = self._read_private()
            existing = [i for i, e in enumerate(data) if e["id"] == tool_id]
            if existing:
                data[existing[0]] = entry
            else:
                data.append(entry)
            self._write(data)

        # 保存 MD 规范文档（中文内容，Windows 默认 GBK，必须显式 utf-8）
        if "raw_md" in resource:
            spec_path = self._get_def_dir() / f"{tool_id}.md"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(resource["raw_md"], encoding="utf-8")

        # 保存代码
        if resource.get("code", "").strip():
            impl_dir = self._get_impl_dir() / tool_id
            impl_dir.mkdir(parents=True, exist_ok=True)
            (impl_dir / "tool.py").write_text(resource["code"], encoding="utf-8")
            if "raw_md" in resource:
                (impl_dir / "spec.md").write_text(resource["raw_md"], encoding="utf-8")
            # 保存测试数据
            if resource.get("test_data"):
                tests_dir = impl_dir / "tests"
                tests_dir.mkdir(exist_ok=True)
                for name, data in resource["test_data"].items():
                    if data:
                        (tests_dir / f"test_{name}.json").write_text(
                            json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

        return tool_id

    async def unregister(self, tool_id: str):
        """从内置与私有两个 registry 中移除该工具。

        旧实现只写私有 registry：若工具在内置 registry 中存在，刷新后
        ``_read`` 合并内置条目又会把它加载回来，表现为「删除后刷新又出现」。
        """
        for root in paths.resource_roots():
            rf = root / "tools" / "registry.json"
            if not rf.exists():
                continue
            with _WRITE_LOCK:
                try:
                    data = json.loads(rf.read_text(encoding="utf-8"))
                except Exception:
                    continue
                next_data = [e for e in data if isinstance(e, dict) and e.get("id") != tool_id]
                if len(next_data) != len(data):
                    _write_json(rf, next_data)

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
        """更新工具条目（只读写私有 registry）。

        内置工具（条目不在私有 registry）更新时，从合并视图取当前条目
        为基底落入私有目录再打补丁——这是「覆盖内置工具字段」的显式
        fork，仅由调用方主动 update 触发，不再由批量写回隐式发生。
        """
        with _WRITE_LOCK:
            data = self._read_private()
            for e in data:
                if e["id"] == tool_id:
                    e.update(updates)
                    self._write(data)
                    return
            base = next((e for e in self._read() if e["id"] == tool_id), None)
            if base is None:
                return  # 工具不存在，不创建空条目
            forked = dict(base)
            forked.update(updates)
            data.append(forked)
            self._write(data)

    def bump_usage_count(self, tool_id: str) -> None:
        """工具被使用一次：计数 +1（同步方法，供执行器调用）。

        计数单独存私有目录的 usage.json，不写 registry.json 的
        usage_count 字段：内置工具条目在 git 管理的资源目录里，运行时
        不该改；往私有目录 fork 整个条目又会遮蔽内置的后续更新。计数
        与条目分离后，内置与私有工具行为一致，读取时由 _read 注入合并。
        """
        with _WRITE_LOCK:
            usage = self._read_usage()
            usage[tool_id] = int(usage.get(tool_id, 0)) + 1
            _write_json(self._usage_file(), usage)

    def _usage_file(self) -> Path:
        return paths.ensure_private_dirs("tools") / "usage.json"

    def _read_usage(self) -> dict:
        try:
            return json.loads(self._usage_file().read_text(encoding="utf-8"))
        except Exception:
            return {}
