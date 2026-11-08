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
import re
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


# ── 工具引用的归一化（id ↔ 显示名）────────────────────────────────────
# MD 规范文档里，工具引用常写成显示名（需求描述输入 $ 选工具时自动带出
# 【【问候程序】】）。而工具实现目录与执行层只认 id。registry 本身就是
# 「id ↔ name」的唯一权威，故解析逻辑放在这里，供代码生成 / 规范解析 /
# 接口层共用，避免各处各写一套 JSON 遍历。
# 注意：这里只读文件，不建目录、不写 registry（可在只读部署下安全调用）。

_CLEAN_RE = re.compile(r"^[\s`'\"【】\[\]]+|[\s`'\"【】\[\]]+$")


def normalize_ref(ref) -> str:
    """去掉【【】】/反引号/引号/首尾空白，得到干净的工具引用。"""
    if not isinstance(ref, str):
        return ""
    s = ref.strip()
    prev = None
    while prev != s:
        prev = s
        s = _CLEAN_RE.sub("", s)
    return s.strip()


def iter_tool_entries() -> list[dict]:
    """合并「内置 + 私有」两处 registry.json（私有同 id 覆盖内置）。

    纯读取，不产生任何副作用（不 mkdir / 不写文件）。
    搜索顺序跟 resource_roots()（私有在前）：同 id 取先命中的（覆盖层语义）。
    """
    merged: dict[str, dict] = {}
    for root in paths.resource_roots():
        rf = root / "tools" / "registry.json"
        if not rf.exists():
            continue
        try:
            entries = json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(entries, list):
            continue
        for e in entries:
            if isinstance(e, dict) and str(e.get("id") or "").strip():
                merged.setdefault(str(e["id"]).strip(), e)
    return list(merged.values())


def find_tool_entry(ref) -> dict | None:
    """按「工具 id」或「显示名」查 registry 条目，查不到返回 None。"""
    ref_n = normalize_ref(ref)
    if not ref_n:
        return None
    entries = iter_tool_entries()

    for e in entries:                                    # 1) id 精确
        if str(e.get("id") or "").strip() == ref_n:
            return e
    for e in entries:                                    # 2) 显示名精确
        if normalize_ref(e.get("name")) == ref_n:
            return e
    low = ref_n.lower()                                  # 3) 大小写不敏感
    for e in entries:
        if str(e.get("id") or "").strip().lower() == low:
            return e
    for e in entries:
        if normalize_ref(e.get("name")).lower() == low:
            return e
    return None


def _impl_exists(tool_id: str) -> bool:
    for root in paths.resource_roots():
        if (root / "tools" / "implementations" / tool_id / "tool.py").exists():
            return True
    return False


def resolve_tool_id(ref) -> str:
    """把工具引用（id 或显示名）解析为真实工具 id。

    解析不到时原样返回 —— 由执行层给出「工具不存在」的明确报错，
    不在这里吞掉错误。
    """
    ref_n = normalize_ref(ref)
    if not ref_n:
        return ref_n
    entry = find_tool_entry(ref_n)
    if entry is not None:
        return str(entry.get("id") or "").strip() or ref_n
    if _impl_exists(ref_n):
        return ref_n
    return ref_n


# registry.json 读-改-写锁：并发注册/删除时防止后写覆盖前写丢条目
_WRITE_LOCK = threading.Lock()


class ToolRegistry(BaseRegistry):
    """工具注册中心。

    读：合并「内置」与「私有」两个目录的 registry.json（私有在后，同 id 覆盖内置）。
    写：新建 / 更新的工具一律写入私有目录，保证本地新建资源不污染内置资源。
    """

    def __init__(self):
        paths.ensure_private_dirs("tools")

    # ── 读写：读合并、写私有 ──

    def _private_registry_file(self) -> Path:
        d = paths.ensure_private_dirs("tools")
        f = d / "registry.json"
        if not f.exists():
            _write_json(f, [])
        return f

    def _read(self) -> list[dict]:
        """合并内置与私有 registry（**私有优先**），并注入使用计数。

        搜索顺序跟 resource_roots()（现为私有在前）：同 id 取先命中的，
        即用户副本覆盖内置——覆盖层语义（2026-11-04 定，方案 C）。

        返回的条目是深拷贝视图：调用方（如 list_all 后前端展示、执行器
        读参数）可随意改，不会意外成为写路径。
        """
        merged: dict[str, dict] = {}
        for root in paths.resource_roots():
            rf = root / "tools" / "registry.json"
            if not rf.exists():
                continue
            # 命中根的先后即优先级：先命中的（私有）胜出
            source = paths.source_of(root)
            try:
                for e in json.loads(rf.read_text(encoding="utf-8")):
                    if isinstance(e, dict) and e.get("id") and e["id"] not in merged:
                        # source 是派生字段（不落盘）：前端据此显示
                        # 「内置 / 我的副本 / 我的工具」徽章
                        e["source"] = source
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

    # ── 多目录定位（读：**私有优先**，找不到再查内置）──
    # 用户「复制为副本」后的修改在私有根，必须优先命中才生效；未 fork 的
    # 内置资源只在 resources/ 下，兜底命中。打开详情若只用私有目录会读不到
    # 未 fork 的内置工具内容 → 界面空白。下面两个方法给出「实际存在」的路径。

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

    async def unregister(self, tool_id: str, scope: str = "all"):
        """按层移除该工具的注册条目。

        scope（公共层与个人定制版相互独立，删一层不影响另一层）：
        - ``"system"``（兼容旧名 ``"builtin"``）：只移除公共层条目——个人副本原样保留；
        - ``"private"``：只移除私有 registry 条目——公共条目不动；
        - ``"all"``（默认）：两层都移除，供彻底清除该 id 的场景使用。

        两种删除都只动 registry，不删磁盘文件：实现目录与定义文件保留在
        系统用户目录中，重新挂回走 register/导入。「删除后刷新又出现」是旧 bug，
        但当年用「两层连坐删除」修复过头了——那会让删除公共版时连个人
        定制版一起消失，违背覆盖层模型的独立性约定。
        """
        if scope == "private":
            roots = [paths.private_root()]
        elif scope in ("system", "builtin"):
            roots = [paths.system_root()]
        else:
            roots = paths.resource_roots()
        for root in roots:
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
            forked.pop("source", None)  # 派生字段不落盘（命中的根是运行时算出的）
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
