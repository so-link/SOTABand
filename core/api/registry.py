"""API 注册中心"""

import json
import time
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
REGISTRY_FILE = API_DIR / "registry.json"


class ApiRegistry:
    """API 注册中心"""

    def __init__(self):
        if not REGISTRY_FILE.exists():
            self._write([])

    def _read(self) -> list[dict]:
        with open(REGISTRY_FILE) as f:
            return json.load(f)

    def _write(self, data: list[dict]):
        with open(REGISTRY_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def register(self, resource: dict) -> str:
        api_id = resource.get("id", f"api-{int(time.time())}")
        entry = {
            "id": api_id,
            "name": resource.get("name", api_id),
            "version": resource.get("version", "0.1.0"),
            "category": resource.get("category", "system"),
            "status": "active",
            "spec_path": f"definitions/{api_id}.md",
            "impl_module": resource.get("impl_module", ""),
            "impl_class": resource.get("impl_class", ""),
            "impl_method": resource.get("impl_method", ""),
            "is_async": resource.get("is_async", True),
            "input_schema": resource.get("input_schema", {}),
            "output_schema": resource.get("output_schema", {}),
            "tags": resource.get("tags", []),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        data = self._read()
        existing = [i for i, e in enumerate(data) if e["id"] == api_id]
        if existing:
            data[existing[0]] = entry
        else:
            data.append(entry)
        self._write(data)

        # 保存 MD 规范文档
        if "raw_md" in resource:
            spec_path = API_DIR / "definitions" / f"{api_id}.md"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(resource["raw_md"])

        return api_id

    async def get(self, api_id: str) -> dict | None:
        for e in self._read():
            if e["id"] == api_id:
                return e
        return None

    async def list_all(self, category: str = None) -> list[dict]:
        apis = self._read()
        if category:
            apis = [a for a in apis if a.get("category") == category]
        return apis
