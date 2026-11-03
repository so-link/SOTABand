"""数据注册中心"""

import json
import threading
import time
from pathlib import Path

from core.resource.registry.registry_base import BaseRegistry
from core.resource import paths
from core.resource.json_store import write_json_atomic
from core.user_context import (
    get_current_user_id,
    detect_storage,
    get_visibility,
    is_visible_to,
    VISIBILITY_PRIVATE,
)


def _write_json(path: Path, data) -> None:
    write_json_atomic(path, data)


DATA_DIR = paths.BUILTIN_ROOT / "data"
REGISTRY_FILE = DATA_DIR / "registry.json"

# registry.json 读-改-写锁：并发注册/删除时防止后写覆盖前写丢条目
_WRITE_LOCK = threading.Lock()


class DataRegistry(BaseRegistry):
    """数据注册中心。

    读：合并「内置」与「私有」两个目录的 registry.json（私有同 id 覆盖内置）。
    写：新建 / 更新的数据集一律写入私有目录。
    """

    def __init__(self):
        paths.ensure_private_dirs("data")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not REGISTRY_FILE.exists():
            _write_json(REGISTRY_FILE, [])
        # 一次性迁移：把早期没有 owner 字段、但本机确实有数据的条目
        # 认领给当前用户。之后列表走精确的归属匹配，不再依赖
        # "本机是否有数据"的启发式判断。
        # 只认领本机真实存在的条目，因此不会误认他人环境的数据。
        try:
            self.claim_local_orphans()
        except (OSError, ValueError):
            pass  # 迁移失败不影响正常使用，退化为运行时启发式判断

    def _private_registry_file(self) -> Path:
        d = paths.ensure_private_dirs("data")
        f = d / "registry.json"
        if not f.exists():
            _write_json(f, [])
        return f

    def _read(self) -> list[dict]:
        """合并内置与私有 registry（**私有优先**：同 id 取先命中的私有条目）。"""
        merged: dict[str, dict] = {}
        for root in paths.resource_roots():
            rf = root / "data" / "registry.json"
            if not rf.exists():
                continue
            try:
                for e in json.loads(rf.read_text(encoding="utf-8")):
                    if isinstance(e, dict) and e.get("id") and e["id"] not in merged:
                        merged[e["id"]] = e
            except Exception:
                continue
        return list(merged.values())

    def _read_private(self) -> list[dict]:
        """只读私有 registry（不合并内置）。

        写路径（register/update/unregister/claim_local_orphans）必须基于
        本方法而非 _read：_read 的合并视图含全部内置条目，写回等于把内置
        条目批量复制进私有目录——一次 update 就产生几十个陈旧副本，永久
        遮蔽内置资源后续更新、让内置已删除条目"复活"（工具空间已发生过，
        见 tool_registry._read_private 注释）。
        """
        rf = self._private_registry_file()
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            return []
        return [e for e in data if isinstance(e, dict) and e.get("id")]

    def read_from(self, root: Path) -> list[dict]:
        rf = root / "data" / "registry.json"
        if not rf.exists():
            return []
        try:
            return json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _write(self, data: list[dict]):
        """写入私有 registry。"""
        _write_json(self._private_registry_file(), data)

    def _get_def_dir(self) -> Path:
        return paths.ensure_private_dirs("data") / "definitions"

    def _get_data_dir(self) -> Path:
        return paths.ensure_private_dirs("data") / "datasets"

    def resolve_data_path(self, data_path: str) -> Path:
        """把注册表中的 data_path 解析为绝对路径。

        注册时可能写入相对路径（如 "datasets/xxx/"）或绝对路径，
        这里统一处理，避免各调用方各自拼字符串导致不一致。
        """
        if not data_path:
            return DATA_DIR
        p = Path(data_path)
        return p if p.is_absolute() else DATA_DIR / p

    def check_availability(self, entry: dict) -> dict:
        """检查数据集在本机是否真的可用。

        registry.json 可能来自他人（例如从老师机器上复制过来的项目），
        其中的 data_path 指向对方机器的目录，本机并不存在。
        若不校验就直接展示，使用者会看到大量能选中、但一运行就报
        “路径不存在”的幽灵数据集。

        返回补充字段：
        - data_path_abs : 解析后的绝对路径
        - available     : 目录是否存在且有文件
        - file_count_actual : 本机实际统计到的文件数（用于纠正注册时填写的数量）
        """
        storage = entry.get("storage") or detect_storage(entry.get("data_path", ""))
        abs_path = self.resolve_data_path(entry.get("data_path", ""))

        # 远端数据集（未来上云）：无法用本地文件系统校验。
        # 若在此返回 available=False，前端的 available_only 过滤会把
        # 云上数据集全部剔除，导致一条都不显示。因此标记为可用，
        # 并注明未做本地校验。
        if storage == "remote":
            return {
                **entry,
                "storage": "remote",
                "data_path_abs": str(abs_path),
                "available": True,
                "file_count_actual": None,
                "availability_note": "远端数据集，未做本地校验",
            }

        exists = abs_path.exists() and abs_path.is_dir()
        actual = 0
        if exists:
            try:
                # 只统计文件，不递归到深层目录，避免大目录卡顿
                actual = sum(1 for f in abs_path.rglob("*") if f.is_file())
            except (OSError, PermissionError):
                actual = 0
        return {
            **entry,
            "storage": "local",
            "data_path_abs": str(abs_path),
            "available": exists and actual > 0,
            "file_count_actual": actual,
        }

    def is_present_locally(self, entry: dict) -> bool:
        """轻量判断数据集在本机是否真的有数据（只查是否有内容，不统计总数）。

        比 check_availability 更快，适合在列表过滤时逐个条目调用。
        """
        data_path = entry.get("data_path", "")
        # 空路径不能当成"有数据"：resolve_data_path 会把空串解析成
        # resources/data 根目录，而该目录恒非空，会导致没有数据的
        # 幽灵条目被误判为本机可用。
        if not data_path or not str(data_path).strip():
            return False

        abs_path = self.resolve_data_path(data_path)
        if not (abs_path.exists() and abs_path.is_dir()):
            return False
        try:
            return any(abs_path.iterdir())
        except (OSError, PermissionError):
            return False

    def belongs_to(self, entry: dict, user_id: str) -> bool:
        """判断数据集是否属于指定用户。

        迁移兼容：早期版本的注册项没有 owner 字段。对此采用规则——
        「本机确实有数据的，视为当前用户的」，这样：
        - 本机有数据的历史条目 → 保留（不会平白消失）
        - 他人环境复制过来的条目（本机无数据）→ 排除
        新注册的条目一律带 owner，直接按 owner 精确匹配。
        """
        owner = entry.get("owner")
        if owner:
            return owner == user_id
        return self.is_present_locally(entry)

    def is_visible(self, entry: dict, user_id: str) -> bool:
        """判断数据集对指定用户是否可见。

        在归属判定之上叠加可见性：
        - public  → 所有人可见（未来云上共享数据集走这条）
        - private → 需归属匹配（含「无 owner 但本机有数据」的历史启发式）

        现在所有数据集都是 private，因此行为与纯归属判定一致；
        上云后把 visibility 改为 public 即可共享，无需改动判定逻辑。
        """
        if is_visible_to(entry, "data", user_id):
            return True
        # private 且 owner 不匹配时，仍要保留历史启发式：
        # 无 owner 且本机有数据的历史条目应视为自己的。
        if not entry.get("owner"):
            return self.is_present_locally(entry)
        return False

    def claim_local_orphans(self, user_id: str | None = None) -> int:
        """把本机有数据、但没有 owner 的历史条目认领给指定用户。

        用于一次性迁移：确认过这些确实是自己的数据后，给它们打上归属，
        之后就走精确的 owner 匹配，不再依赖"本机是否有数据"的启发式判断。

        返回被认领的条目数。
        """
        uid = user_id or get_current_user_id()
        with _WRITE_LOCK:
            data = self._read_private()
            # 只认领私有条目：内置条目是共享示例资源，不该被任何用户
            # 私有化（写 owner 进私有 registry 等于 fork 内置条目）
            claimed = 0
            changed = False
            for entry in data:
                # 补全缺失的 visibility：数据集一律补 private
                if not entry.get("visibility"):
                    entry["visibility"] = VISIBILITY_PRIVATE
                    changed = True
                # 补全缺失的 storage
                if not entry.get("storage"):
                    entry["storage"] = detect_storage(entry.get("data_path", ""))
                    changed = True
                if not entry.get("owner") and self.is_present_locally(entry):
                    entry["owner"] = uid
                    claimed += 1
                    changed = True
            if changed:
                self._write(data)
        return claimed

    async def register(self, resource: dict = None, **kwargs) -> str:
        if resource is None:
            resource = kwargs
        ds_id = resource.get("id", f"dataset-{int(time.time())}")
        entry = {
            "id": ds_id,
            "name": resource.get("name", ds_id),
            "version": resource.get("version", "0.1.0"),
            "type": resource.get("type", "generic"),
            "status": "active",
            "spec_path": f"definitions/{ds_id}.md",
            "data_path": resource.get("data_path", f"datasets/{ds_id}/"),
            "file_count": resource.get("file_count", 0),
            "total_size": resource.get("total_size", 0),
            "formats": resource.get("formats", []),
            "tags": resource.get("tags", []),
            "quality_score": resource.get("quality_score"),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            # 归属：数据集是「谁的」资源。不接受调用方传入，一律取当前用户，
            # 避免伪造 owner 看到别人的数据。
            "owner": get_current_user_id(),
            # 可见性：数据集默认私有（数据在本机，他人本就访问不到）。
            # 未来数据集上云后，可设为 public 让所有人访问。
            "visibility": VISIBILITY_PRIVATE,
            # 存储位置：local / remote。上云后 data_path 会是远端地址，
            # 需要据此跳过本地文件校验，否则会被误判为"不可用"而过滤掉。
            "storage": detect_storage(resource.get("data_path", "")),
        }

        with _WRITE_LOCK:
            data = self._read_private()
            existing = [i for i, e in enumerate(data) if e["id"] == ds_id]
            if existing:
                data[existing[0]] = entry
            else:
                data.append(entry)
            self._write(data)

        # 保存 MD 规范文档（中文内容，Windows 默认 GBK，必须显式 utf-8）
        if "raw_md" in resource:
            spec_path = self._get_def_dir() / f"{ds_id}.md"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(resource["raw_md"], encoding="utf-8")

        return ds_id

    async def unregister(self, resource_id: str):
        with _WRITE_LOCK:
            data = self._read_private()
            data = [e for e in data if e["id"] != resource_id]
            self._write(data)

    async def get(self, resource_id: str) -> dict | None:
        for e in self._read():
            if e["id"] == resource_id:
                return e
        return None

    async def list_all(self, owner: str | None = None) -> list[dict]:
        """列出数据集。

        Args:
            owner: 只返回属于该用户的数据集；为 None 时返回全部（含他人的）。
                   业务上应始终传入当前用户，实现资源按归属隔离。
        """
        data = self._read()
        if owner is None:
            return data
        # 可见性 = public 全员可见 OR private 且归属匹配
        return [e for e in data if self.is_visible(e, owner)]

    async def update(self, resource_id: str, updates: dict):
        """更新数据集条目（只读写私有 registry）。

        内置条目不在私有 registry 时，从合并视图取当前条目为基底落入
        私有目录再打补丁——显式单条 fork，仅由调用方主动 update 触发。
        """
        with _WRITE_LOCK:
            data = self._read_private()
            for e in data:
                if e["id"] == resource_id:
                    e.update(updates)
                    self._write(data)
                    return
            base = next((e for e in self._read() if e["id"] == resource_id), None)
            if base is None:
                return  # 资源不存在，不创建空条目
            forked = dict(base)
            forked.update(updates)
            data.append(forked)
            self._write(data)
