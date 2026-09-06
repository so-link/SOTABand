"""模型注册中心"""

import json
import time
from pathlib import Path

from core.resource.registry.registry_base import BaseRegistry

MODEL_DIR = Path(__file__).resolve().parent.parent.parent.parent / "resources" / "models"
REGISTRY_FILE = MODEL_DIR / "registry.json"


class ModelRegistry(BaseRegistry):
    """模型注册中心"""

    def __init__(self):
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        (MODEL_DIR / "definitions").mkdir(parents=True, exist_ok=True)
        (MODEL_DIR / "models").mkdir(parents=True, exist_ok=True)
        if not REGISTRY_FILE.exists():
            self._write([])

    def _read(self) -> list[dict]:
        with open(REGISTRY_FILE, encoding='utf-8') as f:
            return json.load(f)

    def _write(self, data: list[dict]):
        with open(REGISTRY_FILE, "w", encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_def_dir(self) -> Path:
        return MODEL_DIR / "definitions"

    def _get_models_dir(self) -> Path:
        return MODEL_DIR / "models"

    async def register(self, resource: dict = None, **kwargs) -> str:
        if resource is None:
            resource = kwargs
        model_id = resource.get("id", f"model-{int(time.time())}")
        name = resource.get("name", model_id)

        # name 去重兜底：若同名模型已存在，追加递增序号，确保 name 唯一
        data = self._read()
        existing_names = {e.get("name") for e in data}
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

        existing = [i for i, e in enumerate(data) if e["id"] == model_id]
        if existing:
            data[existing[0]] = entry
        else:
            data.append(entry)
        self._write(data)

        # 保存 MD 规范文档
        if "raw_md" in resource:
            spec_path = self._get_def_dir() / f"{model_id}.md"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(resource["raw_md"])

        return model_id

    async def unregister(self, resource_id: str):
        data = self._read()
        data = [e for e in data if e["id"] != resource_id]
        self._write(data)

    async def get(self, resource_id: str) -> dict | None:
        for e in self._read():
            if e["id"] == resource_id:
                return e
        return None

    async def list_all(self) -> list[dict]:
        return self._read()

    async def update(self, resource_id: str, updates: dict):
        data = self._read()
        for e in data:
            if e["id"] == resource_id:
                e.update(updates)
        self._write(data)
