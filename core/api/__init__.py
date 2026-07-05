"""API 子系统 — 核心层功能对外统一接口

用法:
    from core.api import get_api, list_apis, search_apis

    # 调用数据集注册 API
    api = get_api("api-data-register")
    result = await api.call(dataset_id="test", dataset_name="Test", ...)

    # 获取所有资源类 API
    apis = await list_apis(category="resource")

    # 搜索 API
    apis = await search_apis("注册")
"""

from core.api.base import BaseApi
from core.api.registry import ApiRegistry
from core.api.discoverer import ApiDiscoverer

_registry = ApiRegistry()
_discoverer = ApiDiscoverer()


def get_api(api_id: str) -> BaseApi:
    """获取 API 实例（同步创建，调用时用 await）"""
    spec = _registry._read()
    for s in spec:
        if s["id"] == api_id:
            return BaseApi(s)
    raise ValueError(f"API '{api_id}' 未注册")


async def list_apis(category: str = None) -> list[dict]:
    """列出所有 API"""
    return await _registry.list_all(category)


async def search_apis(query: str = None, category: str = None, tags: list = None) -> list[dict]:
    """搜索 API"""
    return await _discoverer.search(query=query, category=category, tags=tags)


async def match_apis(description: str) -> list[dict]:
    """根据功能描述匹配 API"""
    return await _discoverer.match_by_intent(description)
