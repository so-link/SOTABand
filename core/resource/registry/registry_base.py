"""资源注册基类"""

import re
from abc import ABC, abstractmethod

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def bump_patch_version(version: str | None) -> str | None:
    """把 semver 的 patch 位加一（``0.3.0`` → ``0.3.1``）。

    编辑后重新注册需要体现「这是一次新版本」。无法解析的版本号（如空串、
    ``v1``、日期串）返回 ``None``，由调用方决定退回原值 —— 不臆造版本号。
    工具与 Agent 的注册共用此规则。
    """
    m = _SEMVER_RE.match((version or "").strip())
    if not m:
        return None
    major, minor, patch = m.group(1), m.group(2), m.group(3)
    return f"{major}.{minor}.{int(patch) + 1}"


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
