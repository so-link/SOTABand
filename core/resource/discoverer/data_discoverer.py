"""数据发现器"""

from core.resource.discoverer.discoverer_base import BaseDiscoverer
from core.resource.registry.data_registry import DataRegistry


class DataDiscoverer(BaseDiscoverer):
    """数据发现器 — 按多维度检索数据集"""

    def __init__(self):
        self.registry = DataRegistry()

    async def search(self, query: str = None, **filters) -> list[dict]:
        datasets = await self.registry.list_all()
        results = []
        for ds in datasets:
            if ds.get("status") != "active":
                continue
            if query:
                q = query.lower()
                if not (q in ds.get("name", "").lower() or
                        q in ds.get("id", "").lower() or
                        any(q in tag.lower() for tag in ds.get("tags", []))):
                    continue
            if filters.get("type") and ds.get("type") != filters["type"]:
                continue
            if filters.get("format"):
                if filters["format"] not in ds.get("formats", []):
                    continue
            if filters.get("tags"):
                if not any(tag in ds.get("tags", []) for tag in filters["tags"]):
                    continue
            results.append(ds)
        return results

    async def list_all(self) -> list[dict]:
        return await self.registry.list_all()

    async def get_by_tags(self, tags: list[str]) -> list[dict]:
        return await self.search(tags=tags)

    async def get_by_format(self, fmt: str) -> list[dict]:
        return await self.search(format=fmt)
