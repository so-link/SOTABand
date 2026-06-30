"""数据注册中心"""

import json
import time
from pathlib import Path

from core.resource.registry.registry_base import BaseRegistry

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "resources" / "data"
REGISTRY_FILE = DATA_DIR / "registry.json"


class DataRegistry(BaseRegistry):
    """数据注册中心"""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not REGISTRY_FILE.exists():
            self._write([])

    def _read(self) -> list[dict]:
        with open(REGISTRY_FILE) as f:
            return json.load(f)

    def _write(self, data: list[dict]):
        with open(REGISTRY_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_def_dir(self) -> Path:
        return DATA_DIR / "definitions"

    def _get_data_dir(self) -> Path:
        return DATA_DIR / "datasets"

    async def register(self, resource: dict) -> str:
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
        }

        data = self._read()
        existing = [i for i, e in enumerate(data) if e["id"] == ds_id]
        if existing:
            data[existing[0]] = entry
        else:
            data.append(entry)
        self._write(data)

        # 保存 MD 规范文档
        if "raw_md" in resource:
            spec_path = self._get_def_dir() / f"{ds_id}.md"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(resource["raw_md"])

        return ds_id

    async def unregister(self, resource_id: str):
        data = self._read()
        for e in data:
            if e["id"] == resource_id:
                e["status"] = "archived"
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
