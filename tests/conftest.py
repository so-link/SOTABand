"""pytest 分层与运行开关。

分层（marker）：

- ``unit``  纯逻辑，无网络、无外部进程
- ``api``   真实 HTTP 调用后端（需 8201 已启动；未启动自动 skip）
- ``e2e``   真实浏览器（Playwright，需前端 5173 + 后端 8201 + 登录密码）

默认只跑 ``unit`` + ``api``；浏览器用例要显式开启：

    ./venv/bin/python -m pytest -q                 # unit + api
    ./venv/bin/python -m pytest -q -m api          # 只跑某一层
    ./venv/bin/python -m pytest tests/e2e -q --run-e2e   # 浏览器（单独一次）

**e2e 建议单独一次运行**：Playwright 同步 API 会在进程内常驻一个事件循环，
与项目里 ``@pytest.mark.asyncio`` 的协程用例（pytest-asyncio 驱动）属于同一
进程的两种事件循环模型，混跑时 async 用例会报 cannot be called from a
running event loop。因此：

    ./venv/bin/python -m pytest -q                      # 快速回归：unit + api
    ./venv/bin/python -m pytest tests/e2e -q --run-e2e  # 浏览器：单独一次
"""

from __future__ import annotations

import asyncio
import contextvars
import threading

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-e2e", action="store_true", default=False,
        help="运行浏览器端到端用例（需要前端与后端都已启动）")


def pytest_collection_modifyitems(config, items):
    """浏览器用例默认不执行（按 marker），需要 --run-e2e。"""
    if config.getoption("--run-e2e"):
        return
    skip = pytest.mark.skip(reason="浏览器用例默认不执行，需要 --run-e2e")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)


def run_coro(coro):
    """在独立线程里跑协程并取回结果（异常原样抛出）。

    为什么不用 ``asyncio.run``：它要求当前线程没有事件循环在跑，而同一次
    运行里只要包含浏览器用例（Playwright 同步 API 会常驻内部事件循环），
    就会报 cannot be called from a running event loop。统一在新线程里自建
    事件循环，与运行时环境彻底隔离。
    """
    box: dict = {}
    # 上下文（当前用户身份等）必须显式带到新线程：set_current_user 用的
    # contextvars 不会自动跨线程继承，丢了会让被测代码以为「无人登录」。
    ctx = contextvars.copy_context()

    def target():
        try:
            box["value"] = ctx.run(asyncio.run, coro)
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")
