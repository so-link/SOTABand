"""资源注册基类"""

from abc import ABC, abstractmethod


class BaseRegistry(ABC):
    """资源注册基类 — 所有注册器继承此类"""

    @abstractmethod
    async def register(self, resource: dict) -> str:
        """注册资源，返回资源 ID"""
        ...

    @abstractmethod
    async def unregister(self, resource_id: str) -> None:
        """注销资源"""
        ...

    @abstractmethod
    async def get(self, resource_id: str) -> dict | None:
        """获取资源信息"""
        ...

    @abstractmethod
    async def list_all(self) -> list[dict]:
        """列出所有已注册资源"""
        ...

    @abstractmethod
    async def update(self, resource_id: str, updates: dict) -> None:
        """更新资源信息"""
        ...
