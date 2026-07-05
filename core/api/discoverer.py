"""API 发现器"""

from core.api.registry import ApiRegistry


class ApiDiscoverer:
    """API 发现器 — 按类别/标签/关键词检索"""

    def __init__(self):
        self.registry = ApiRegistry()

    async def search(self, query: str = None, category: str = None, tags: list = None) -> list[dict]:
        apis = await self.registry.list_all()
        results = []
        for api in apis:
            if api.get("status") != "active":
                continue
            if category and api.get("category") != category:
                continue
            if query:
                q = query.lower()
                if not (q in api.get("name", "").lower() or
                        q in api.get("id", "").lower() or
                        any(q in t.lower() for t in api.get("tags", []))):
                    continue
            if tags and not any(t in api.get("tags", []) for t in tags):
                continue
            results.append(api)
        return results

    async def match_by_intent(self, description: str) -> list[dict]:
        """根据功能描述语义匹配 API（简单关键词匹配）"""
        apis = await self.registry.list_all()
        keywords = description.lower().split()
        scored = []
        for api in apis:
            if api.get("status") != "active":
                continue
            text = f"{api['name']} {api['id']} {' '.join(api.get('tags', []))}".lower()
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                scored.append((score, api))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [a for _, a in scored]
