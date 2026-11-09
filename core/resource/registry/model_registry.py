"""模型注册中心"""

import json
import shutil
import threading
import time
from pathlib import Path

from core.resource.registry.registry_base import BaseRegistry
from core.resource import paths
from core.resource.json_store import write_json_atomic


def _write_json(path: Path, data) -> None:
    write_json_atomic(path, data)


# 删除掩码：出厂种子导入的条目被删后，重跑种子脚本会让它复活；
# 保留 status=deleted 占位能力，_read 合并后过滤（常规删除已改为真删）。
DELETED = "deleted"

# registry.json 读-改-写锁：并发注册/删除时防止后写覆盖前写丢条目
_WRITE_LOCK = threading.Lock()


class ModelRegistry(BaseRegistry):
    """模型注册中心。

    读：合并「私有」与「系统（公共）」两层 registry.json（私有同 id 覆盖公共）。
    写：private 落私有目录；公共层运行期可写（仅系统身份，闸门在路由层）。
    """

    def __init__(self):
        pd = paths.ensure_private_dirs("models")
        (pd / "definitions").mkdir(parents=True, exist_ok=True)
        (pd / "models").mkdir(parents=True, exist_ok=True)

    def _private_registry_file(self) -> Path:
        d = paths.ensure_private_dirs("models")
        f = d / "registry.json"
        if not f.exists():
            _write_json(f, [])
        return f

    @staticmethod
    def _registry_file(root: Path) -> Path:
        return root / "models" / "registry.json"

    @staticmethod
    def _load(rf: Path) -> list[dict]:
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            return []
        return [e for e in data if isinstance(e, dict) and e.get("id")]

    def _writable_roots(self) -> list[Path]:
        """可写层：私有 + 公共（系统用户目录）。"""
        return paths.resource_roots()

    def _read(self) -> list[dict]:
        """合并私有与公共 registry（**私有优先**：同 id 取先命中的私有条目；status=deleted 为删除掩码）。"""
        merged: dict[str, dict] = {}
        for root in paths.resource_roots():
            rf = root / "models" / "registry.json"
            if not rf.exists():
                continue
            source = paths.source_of(root)
            try:
                for e in json.loads(rf.read_text(encoding="utf-8")):
                    if isinstance(e, dict) and e.get("id") and e["id"] not in merged:
                        e["source"] = source
                        merged[e["id"]] = e
            except Exception:
                continue
        return [e for e in merged.values() if e.get("status") != DELETED]

    def _read_private(self) -> list[dict]:
        """只读私有 registry（不合并内置、不过滤删除掩码）。

        写路径必须基于本方法而非 _read：合并视图写回等于把内置条目批量
        复制进私有目录，产生陈旧副本永久遮蔽内置更新（工具空间已发生
        过，见 tool_registry._read_private 注释）。删除掩码条目保留——
        它们本来就存在私有文件里。
        """
        rf = self._private_registry_file()
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            return []
        return [e for e in data if isinstance(e, dict) and e.get("id")]

    def read_from(self, root: Path) -> list[dict]:
        rf = root / "models" / "registry.json"
        if not rf.exists():
            return []
        try:
            return json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _write(self, data: list[dict]):
        """写入私有 registry（保留删除掩码条目，防止 register/update 丢失掩码）。"""
        pf = self._private_registry_file()
        try:
            current = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            current = []
        ids = {e.get("id") for e in data if isinstance(e, dict)}
        masks = [
            e for e in current
            if isinstance(e, dict) and e.get("status") == DELETED and e.get("id") not in ids
        ]
        _write_json(pf, data + masks)

    def _get_def_dir(self) -> Path:
        return paths.ensure_private_dirs("models") / "definitions"

    def _get_models_dir(self) -> Path:
        return paths.ensure_private_dirs("models") / "models"

    async def register(self, resource: dict = None, **kwargs) -> str:
        if resource is None:
            resource = kwargs
        model_id = resource.get("id", f"model-{int(time.time())}")
        name = resource.get("name", model_id)

        # name 去重兜底：若同名模型已存在，追加递增序号，确保 name 唯一
        # 整个「读-改名-写」必须在锁内，否则并发注册会写出重名条目。
        # 判重用合并视图（内置名也算），写回基底用私有视图——
        # 合并视图直接写回会把内置条目复制进私有目录（写污染）。
        with _WRITE_LOCK:
            existing_names = {e.get("name") for e in self._read()}
            if name in existing_names:
                suffix = 1
                while f"{name}_{suffix}" in existing_names:
                    suffix += 1
                name = f"{name}_{suffix}"

            entry = {
                "id": model_id,
                "name": name,
                "version": resource.get("version", "0.1.0"),
                "type": "model",
                "status": "registered",
                "spec_path": f"definitions/{model_id}.md",
                "framework": resource.get("framework", ""),
                "model_path": self._norm_model_path(
                    resource.get("model_path", f"models/{model_id}/"), model_id),
                "input_format": resource.get("input_format", ""),
                "output_format": resource.get("output_format", ""),
                "tags": resource.get("tags", []),
                "associated_tool_id": resource.get("associated_tool_id", ""),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

            data = self._read_private()
            existing = [i for i, e in enumerate(data) if e["id"] == model_id]
            if existing:
                data[existing[0]] = entry
            else:
                data.append(entry)
            self._write(data)

        # 保存 MD 规范文档（中文内容，Windows 默认 GBK，必须显式 utf-8）
        if "raw_md" in resource:
            spec_path = self._get_def_dir() / f"{model_id}.md"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(resource["raw_md"], encoding="utf-8")

        return model_id

    async def unregister(self, resource_id: str):
        """彻底删除：各层删条目 + 删规范文档与产物目录。

        公共层运行期可写（写权限由路由层闸门把关），删除就是真删——
        不再写 status=deleted 全局掩码（旧掩码是为「内置层只读」设计的；
        恢复出厂 = 重跑 scripts/seed_system_resources.py）。

        两条历史缺陷的教训保留：
        1. 删除必须对所有层全局生效（不能只写当前用户私有层）；
        2. 不只注销条目：definitions/<id>.md 与产物目录一并删除，不留孤儿。
        """
        with _WRITE_LOCK:
            for root in self._writable_roots():
                rf = self._registry_file(root)
                if rf.exists():
                    data = self._load(rf)
                    next_data = [e for e in data if e.get("id") != resource_id]
                    if len(next_data) != len(data):
                        _write_json(rf, next_data)
                # 无条目也要清文件：旧版删除只注销条目，留下过孤儿规范/产物
                self._purge_files(root, resource_id)

    @staticmethod
    def _purge_files(root: Path, resource_id: str) -> None:
        """删除某层下该模型的规范文档与产物（幂等，只在资源空间内操作）。"""
        space = root / "models"
        ddir = space / "definitions"
        if ddir.is_dir():
            for p in ddir.glob(f"{resource_id}*"):
                if p.is_file():
                    p.unlink(missing_ok=True)
        # 产物两种落位：models/<id>/（默认相对路径）与 models/models/<id>/
        for candidate in (space / resource_id, space / "models" / resource_id):
            if candidate.is_dir():
                shutil.rmtree(candidate, ignore_errors=True)
            elif candidate.is_file():
                candidate.unlink(missing_ok=True)

    async def get(self, resource_id: str) -> dict | None:
        for e in self._read():
            if e["id"] == resource_id:
                return e
        return None

    async def list_all(self) -> list[dict]:
        return self._read()

    async def update(self, resource_id: str, updates: dict):
        """更新模型条目（只读写私有 registry）。

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
                return  # 资源不存在（或已被删除掩码），不创建空条目
            forked = dict(base)
            forked.update(updates)
            data.append(forked)
            self._write(data)

    async def purge_missing(self) -> list[dict]:
        """清理「注册条目还在、模型文件本机不存在」的僵尸条目。

        历史条目大量指向别人机器上的绝对路径（``/Users/leo/...``），本机从来
        没有这些文件，界面上却照样列出、点开即报错。逐条走 ``unregister``
        统一删除路径：可写层删条目与文件，内置层只写全局删除掩码，
        **内置文件随 git 分发不删**（git pull / 重新部署即恢复）。

        候选取各层条目的并集（跳过删除掩码）而不是合并视图：旧版把掩码写在
        个人私有层，合并视图里已被遮蔽，若只看合并视图这些条目会漏清，
        其他用户仍然看得到。

        返回被清理的条目（id / name / model_path），供接口回执。
        """
        entries: dict[str, dict] = {}
        masked: set[str] = set()
        for root in paths.resource_roots():
            for e in self._load(self._registry_file(root)):
                if e.get("status") == DELETED:
                    masked.add(e["id"])
                    continue
                entries.setdefault(e["id"], e)
        removed = [
            {"id": e["id"], "name": e.get("name", e["id"]),
             "model_path": (e.get("model_path") or "").strip()}
            for e in entries.values()
            if e["id"] not in masked and not self._artifact_exists(e)
        ]
        for e in removed:
            await self.unregister(e["id"])
        return removed

    @staticmethod
    def _norm_model_path(model_path: str, model_id: str) -> str:
        """把落在资源根 models/ 下的绝对路径归一为相对路径。

        读取端（_artifact_exists、工具侧 _model_data）一律按「资源根 / models / 相对路径」
        解析，只有相对路径能在用户目录迁移、私有根变更后继续解析成功；绝对路径会被
        固化为某台机器的历史位置，资源目录一挪就读不到模型产物。
        """
        raw = str(model_path or "").strip()
        if not raw:
            return f"models/{model_id}/"
        p = Path(raw.replace("\\", "/"))
        if not p.is_absolute():
            return raw
        for root in paths.resource_roots():
            base = Path(str(root)) / "models"
            try:
                return str(p.relative_to(base))
            except ValueError:
                continue
        return raw

    @staticmethod
    def _artifact_exists(entry: dict) -> bool:
        """模型产物本机是否可得。相对路径按各资源根下的 models/ 解析。"""
        model_path = (entry.get("model_path") or "").strip()
        if model_path:
            p = Path(model_path)
            if p.is_absolute():
                return p.exists()
            if any((root / "models" / model_path).exists() for root in paths.resource_roots()):
                return True
        rid = str(entry.get("id") or "")
        if not rid or not paths.is_valid_resource_id(rid):
            return False
        return any((root / "models" / rid).is_dir() for root in paths.resource_roots())
