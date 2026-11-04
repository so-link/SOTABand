"""Agent 注册中心

【归属】**超管创建的 Agent 供全员使用，普通用户创建的仅自己可用**。

- 内置 Agent（``resources/agents/``，随仓库分发，无 owner）→ 所有人可见；
- 超管注册的 Agent（``visibility=public``）→ 所有人可见；
- 普通用户注册的 Agent（``visibility=private``）→ 仅属主可见。

Agent 是代码资产 + 编排逻辑，实现代码位于 ``resources/agents/implementations/``
或用户私有目录，代码本身完整，因此不存在"看得见但跑不了"的问题（这是它与
数据集的关键区别）。但**谁能用**由 visibility 决定，读写接口都要按它过滤。

注意：Agent 执行时若通过工具访问数据集，仍受数据集的私有归属约束——
由 ``core/api/implementations/api_data.py`` 统一按 owner 过滤。
共享的是 Agent 本身，不是它要访问的数据。

详见 ``core/user_context.py`` 顶部的归属模型说明。
"""

import json
import os
import threading
import time
from pathlib import Path

from core.resource.registry.registry_base import BaseRegistry
from core.resource import paths
from core.resource.json_store import write_json_atomic
from core.user_context import (
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    get_current_user_id,
)


def _write_json(path: Path, data) -> None:
    write_json_atomic(path, data)


REGISTRY_DIR = paths.BUILTIN_ROOT / "agents"
REGISTRY_FILE = REGISTRY_DIR / "registry.json"

# registry.json 读-改-写锁：并发注册/删除时防止后写覆盖前写丢条目
_WRITE_LOCK = threading.Lock()


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

    def _shared_registry_file(self) -> Path:
        d = paths.shared_space_dir("agents")
        d.mkdir(parents=True, exist_ok=True)
        f = d / "registry.json"
        if not f.exists():
            _write_json(f, [])
        return f

    def _registry_file(self, visibility: str) -> Path:
        """该可见性的 registry 落在哪个根：public 共享、private 私有。"""
        return (self._shared_registry_file() if visibility == VISIBILITY_PUBLIC
                else self._private_registry_file())

    def write_root(self, visibility: str) -> Path:
        """该可见性的资源写入哪个根（超管发布的公共 Agent 落共享根）。"""
        return (paths.shared_root() if visibility == VISIBILITY_PUBLIC
                else paths.private_root())

    def _get_spec_dir(self, visibility: str = VISIBILITY_PRIVATE) -> Path:
        """MD 规范文档目录——新建的规范写入此处。"""
        d = self.write_root(visibility) / "agents" / "definitions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _get_impl_dir(self, visibility: str = VISIBILITY_PRIVATE) -> Path:
        """实现代码根目录——新建的实现写入此处。"""
        d = self.write_root(visibility) / "agents" / "implementations"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get_spec_search_dirs(self) -> list[Path]:
        """规范文档的检索目录（内置 + 私有），用于「读」时定位已有 MD。"""
        return [root / "agents" / "definitions" for root in paths.resource_roots()]

    def get_impl_search_dirs(self) -> list[Path]:
        """实现目录的检索目录（内置 + 私有）。"""
        return [root / "agents" / "implementations" for root in paths.resource_roots()]

    def _read_scope(self, visibility: str) -> list[dict]:
        """只读某一层的 registry（不合并其它层）。

        写路径必须基于本方法而非 _read：合并视图写回等于把内置条目批量
        复制进私有目录，产生陈旧副本永久遮蔽内置更新（工具空间已发生
        过，见 tool_registry._read_private 注释）。Agent 尤其危险：
        interactive-agent 等内置 Agent 被陈旧副本遮蔽后，行为漂移难以
        排查。
        """
        try:
            data = json.loads(self._registry_file(visibility).read_text(encoding="utf-8"))
        except Exception:
            return []
        return [e for e in data if isinstance(e, dict) and e.get("id")]

    def _write_scope(self, data: list[dict], visibility: str) -> None:
        _write_json(self._registry_file(visibility), data)

    def _read_private(self) -> list[dict]:
        """只读私有层（兼容旧调用）。"""
        return self._read_scope(VISIBILITY_PRIVATE)

    def _read(self) -> list[dict]:
        """合并内置与私有 registry（**私有优先**：同 id 取先命中的私有条目）。"""
        merged: dict[str, dict] = {}
        for root in paths.resource_roots():
            rf = root / "agents" / "registry.json"
            if not rf.exists():
                continue
            # 命中根的先后即优先级：先命中的（私有）胜出
            if root == paths.private_root():
                source = "private"
            elif root == paths.shared_root():
                source = "shared"
            else:
                source = "builtin"
            try:
                for e in json.loads(rf.read_text(encoding="utf-8")):
                    if isinstance(e, dict) and e.get("id") and e["id"] not in merged:
                        # source 是派生字段（不落盘）：前端徽章「内置/我的副本/我的」
                        e["source"] = source
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
        """写入私有 registry（兼容旧调用）。"""
        self._write_scope(data, VISIBILITY_PRIVATE)

    async def register(self, resource: dict) -> str:
        agent_id = resource.get("id", f"agent-{int(time.time())}")
        visibility = resource.get("visibility") or VISIBILITY_PUBLIC
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
            # 归属与可见性：owner 记录创建者；visibility 由调用方按角色决定
            # （超管建的 public 供全员使用，普通用户建的 private 仅自己可用），
            # 缺省 public 仅为兼容不传该字段的旧调用方。
            "owner": get_current_user_id(),
            "visibility": visibility,
        }

        # public 落共享根（全员可见），private 落当前用户的私有根
        with _WRITE_LOCK:
            data = self._read_scope(visibility)
            existing = [i for i, e in enumerate(data) if e["id"] == agent_id]
            if existing:
                data[existing[0]] = entry
            else:
                data.append(entry)
            self._write_scope(data, visibility)

        # 保存 MD 规范文档——与 registry 条目同根（内置根随仓库分发，运行期不写）
        if "raw_md" in resource:
            spec_path = self._get_spec_dir(visibility) / f"{agent_id}.md"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            with open(spec_path, "w", encoding='utf-8') as f:
                f.write(resource["raw_md"])

        return agent_id

    async def unregister(self, resource_id: str, scope: str = "all") -> None:
        # 按层移除注册条目（与 tool_registry.unregister 同款多态）：
        # "builtin" 只删内置条目、"shared" 只删共享条目、"private" 只删私有
        # 条目、"all" 各层全删。层与层之间独立——删除任一层不得牵连另一层。
        if scope == "private":
            roots = [paths.private_root()]
        elif scope == "shared":
            roots = [paths.shared_root()]
        elif scope == "builtin":
            roots = [paths.BUILTIN_ROOT]
        else:
            roots = paths.resource_roots()
        for root in roots:
            rf = root / "agents" / "registry.json"
            if not rf.exists():
                continue
            with _WRITE_LOCK:
                try:
                    data = json.loads(rf.read_text(encoding="utf-8"))
                except Exception:
                    continue
                next_data = [e for e in data if isinstance(e, dict) and e.get("id") != resource_id]
                if len(next_data) != len(data):
                    _write_json(rf, next_data)

    async def get(self, resource_id: str) -> dict | None:
        data = self._read()
        for e in data:
            if e["id"] == resource_id:
                return e
        return None

    def is_visible(self, entry: dict, user_id: str) -> bool:
        """Agent 对指定用户是否可见。

        - ``visibility=public``（超管创建 / 明确公开）→ 所有人可见；
        - 无 ``owner`` 的条目是**内置 Agent**（随仓库分发）→ 所有人可见；
        - 其余（普通用户创建的私有 Agent）→ 仅属主可见。

        历史条目没有 visibility 字段：无 owner 的按内置处理，有 owner 的
        按新规则归为私有——普通用户创建的 Agent 本来就不该被别人使用。
        """
        if entry.get("visibility") == VISIBILITY_PUBLIC:
            return True
        if not entry.get("owner"):
            return True
        return entry.get("owner") == user_id

    async def list_all(self, user_id: str | None = None) -> list[dict]:
        """列出 Agent。

        Args:
            user_id: 传入则只返回对该用户可见的 Agent（public + 自己的 private）。
                     不传则返回全部（系统级遍历用）。
        """
        data = self._read()
        if user_id is None:
            return data
        return [e for e in data if self.is_visible(e, user_id)]

    async def update(self, resource_id: str, updates: dict) -> None:
        """更新 Agent 条目——写回它当前所在的那一层。

        - 共享层（超管发布的 public）→ 就地更新共享 registry；
        - 私有层 → 就地更新私有 registry；
        - 内置层 → 从合并视图取基底落入私有目录再打补丁（显式单条 fork，
          仅由调用方主动 update 触发），避免写只读的内置根。
        """
        with _WRITE_LOCK:
            base = next((e for e in self._read() if e["id"] == resource_id), None)
            if base is None:
                return  # 资源不存在，不创建空条目

            if base.get("source") == "shared":
                data = self._read_scope(VISIBILITY_PUBLIC)
                for e in data:
                    if e["id"] == resource_id:
                        e.update(updates)
                        self._write_scope(data, VISIBILITY_PUBLIC)
                        return
                return

            data = self._read_scope(VISIBILITY_PRIVATE)
            for e in data:
                if e["id"] == resource_id:
                    e.update(updates)
                    self._write_scope(data, VISIBILITY_PRIVATE)
                    return
            forked = dict(base)
            forked.pop("source", None)  # 派生字段不落盘（命中的根是运行时算出的）
            forked.update(updates)
            data.append(forked)
            self._write_scope(data, VISIBILITY_PRIVATE)
