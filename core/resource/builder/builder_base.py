"""资源构建基类"""

from abc import ABC, abstractmethod


class BaseBuilder(ABC):
    """资源构建基类 — 所有构建器继承此类"""

    @abstractmethod
    async def validate_spec(self, spec: dict) -> bool:
        """校验规格文档"""
        ...

    @abstractmethod
    async def build(self, spec: dict) -> str:
        """根据规格构建资源，返回产出内容"""
        ...

    @abstractmethod
    async def dry_run(self, code: str) -> dict:
        """沙箱预跑，返回测试结果"""
        ...
