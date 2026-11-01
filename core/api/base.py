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

    def call(self, **kwargs) -> dict[str, Any]:
        """调用 API（同步，供 sync 工具代码 / 同步 helper 使用）。

        注意：在事件循环线程里调用异步 API 时，不能用
        ``run_coroutine_threadsafe`` + ``future.result()``——协程被排到当前
        循环，而 ``result()`` 又阻塞同一个线程等待，形成自我死锁（60 秒超时）。
        这里改为在独立线程的新事件循环中执行，调用方最多阻塞到超时。
        """
        fn = self._get_callable()
        if not self.spec.get("is_async", True):
            return fn(**kwargs)

        import asyncio
        import threading

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 当前线程没有事件循环 → 直接新建循环执行
            return asyncio.run(fn(**kwargs))

        # 当前线程已有事件循环 → 挪到独立线程执行，避免自锁
        box: dict[str, Any] = {}
        error: list[BaseException] = []

        def _runner():
            try:
                box["value"] = asyncio.run(fn(**kwargs))
            except BaseException as e:  # 原样抛回调用线程
                error.append(e)

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join(60)
        if thread.is_alive():
            raise TimeoutError(f"API {self.api_id} 调用超时（60 秒）")
        if error:
            raise error[0]
        return box.get("value")
