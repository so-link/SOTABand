"""工具发现器"""

from core.resource.discoverer.discoverer_base import BaseDiscoverer
from core.resource.registry.tool_registry import ToolRegistry


class ToolDiscoverer(BaseDiscoverer):
    """工具发现器 — 按多维度检索工具"""

    def __init__(self):
        self.registry = ToolRegistry()

    async def search(self, query: str = None, **filters) -> list[dict]:
        tools = await self.registry.list_all()
        results = []
        for t in tools:
            if t.get("status") != "active":
                continue
            # 关键词匹配
            if query:
                q = query.lower()
                if not (q in t.get("name", "").lower() or
                        q in t.get("id", "").lower() or
                        any(q in tag.lower() for tag in t.get("tags", []))):
                    continue
            # 类型过滤
            if filters.get("type") and t.get("type") != filters["type"]:
                continue
            # 标签过滤
            if filters.get("tags"):
                if not any(tag in t.get("tags", []) for tag in filters["tags"]):
                    continue
            results.append(t)
        return results

    async def list_all(self) -> list[dict]:
        return await self.registry.list_all()

    async def get_by_tags(self, tags: list[str]) -> list[dict]:
        return await self.search(tags=tags)

    async def get_by_input_format(self, fmt: str) -> list[dict]:
        """按输入数据格式查找工具"""
        tools = await self.registry.list_all()
        results = []
        for t in tools:
            schema = t.get("input_schema", {})
            formats = schema.get("formats", [])
            if fmt in formats:
                results.append(t)
        return results

    async def match_by_capability(self, description: str) -> list[dict]:
        """根据能力描述匹配工具（语义搜索）"""
        tools = await self.registry.list_all()
        # 简单关键词匹配
        keywords = description.lower().split()
        scored = []
        for t in tools:
            if t.get("status") != "active":
                continue
            text = f"{t.get('name', '')} {t.get('id', '')} {' '.join(t.get('tags', []))}".lower()
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                scored.append((score, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored]
