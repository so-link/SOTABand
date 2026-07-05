"""API 基类"""

import importlib
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator


class BaseApi(ABC):
    """API 基类 — 封装对核心层模块方法的调用"""

    def __init__(self, spec: dict):
        self.spec = spec
        self._instance = None

    @property
    def api_id(self) -> str:
        return self.spec.get("id", "")

    def _get_callable(self):
        """根据 spec 中的 impl_module/impl_class/impl_method 动态加载"""
        if self._instance:
            return self._instance

        module_path = self.spec.get("impl_module", "")
        class_name = self.spec.get("impl_class", "")
        method_name = self.spec.get("impl_method", "")

        if not module_path or not method_name:
            raise ValueError(f"API {self.api_id}: impl_module/impl_method 未定义")

        module = importlib.import_module(module_path)
        if class_name:
            cls = getattr(module, class_name)
            self._instance = getattr(cls(), method_name)
        else:
            self._instance = getattr(module, method_name)

        return self._instance

    async def call(self, **kwargs) -> dict[str, Any]:
        """调用 API（异步）"""
        fn = self._get_callable()
        if self.spec.get("is_async", True):
            return await fn(**kwargs)
        else:
            return fn(**kwargs)

    def call_sync(self, **kwargs) -> dict[str, Any]:
        """调用 API（同步）"""
        fn = self._get_callable()
        return fn(**kwargs)
