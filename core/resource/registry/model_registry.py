"""模型注册中心"""

import json
import threading
import time
from pathlib import Path

from core.resource.registry.registry_base import BaseRegistry
from core.resource import paths
from core.resource.json_store import write_json_atomic


def _write_json(path: Path, data) -> None:
    write_json_atomic(path, data)


MODEL_DIR = paths.BUILTIN_ROOT / "models"
REGISTRY_FILE = MODEL_DIR / "registry.json"

# 删除掩码：内置 registry 只读，删除其中的条目时向私有 registry 写入
# status=deleted 占位条目，_read 合并后过滤，使删除对只读内置根同样生效。
DELETED = "deleted"

# registry.json 读-改-写锁：并发注册/删除时防止后写覆盖前写丢条目
_WRITE_LOCK = threading.Lock()


class ModelRegistry(BaseRegistry):
    """模型注册中心。

    读：合并「内置」与「私有」两个目录的 registry.json（私有同 id 覆盖内置）。
    写：新建 / 更新的模型一律写入私有目录。
    """

    def __init__(self):
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        (MODEL_DIR / "definitions").mkdir(parents=True, exist_ok=True)
        (MODEL_DIR / "models").mkdir(parents=True, exist_ok=True)
        pd = paths.ensure_private_dirs("models")
        (pd / "definitions").mkdir(parents=True, exist_ok=True)
        (pd / "models").mkdir(parents=True, exist_ok=True)
        if not REGISTRY_FILE.exists():
            _write_json(REGISTRY_FILE, [])

    def _private_registry_file(self) -> Path:
        d = paths.ensure_private_dirs("models")
        f = d / "registry.json"
        if not f.exists():
            _write_json(f, [])
        return f

    def _read(self) -> list[dict]:
        """合并内置与私有 registry（**私有优先**：同 id 取先命中的私有条目；status=deleted 为删除掩码）。"""
        merged: dict[str, dict] = {}
        for root in paths.resource_roots():
            rf = root / "models" / "registry.json"
            if not rf.exists():
                continue
            try:
                for e in json.loads(rf.read_text(encoding="utf-8")):
                    if isinstance(e, dict) and e.get("id") and e["id"] not in merged:
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
                "model_path": resource.get("model_path", f"models/{model_id}/"),
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
        with _WRITE_LOCK:
            data = self._read_private()
            data = [e for e in data if e["id"] != resource_id]
            # 内置 registry 只读：若条目存在于内置根，写 status=deleted 掩码使其不再出现
            builtin_ids = {e.get("id") for e in self.read_from(paths.BUILTIN_ROOT)}
            if resource_id in builtin_ids and not any(e["id"] == resource_id for e in data):
                data.append({"id": resource_id, "name": resource_id, "type": "model", "status": DELETED})
            self._write(data)

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
