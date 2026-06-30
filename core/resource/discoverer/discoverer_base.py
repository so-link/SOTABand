"""资源发现基类"""

from abc import ABC, abstractmethod


class BaseDiscoverer(ABC):
    """资源发现基类 — 所有发现器继承此类"""

    @abstractmethod
    async def search(self, query: str = None, **filters) -> list[dict]:
        """按关键词和过滤条件搜索"""
        ...

    @abstractmethod
    async def list_all(self) -> list[dict]:
        """列出所有已注册资源"""
        ...

    @abstractmethod
    async def get_by_tags(self, tags: list[str]) -> list[dict]:
        """按标签检索"""
        ...
