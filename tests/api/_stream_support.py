"""进程内流式生成端点测试的基础设施（TestClient + FakeLLM）。

与 tests/api 的其它用例（真实 8201 进程）互补：生成类端点都是 SSE，且
依赖 LLM 与沙箱子进程，只能在**进程内替换 LLM 客户端**才能稳定断言事件
序列。这里只负责搭台子，业务断言放在具体子测试文件里。

要点：
- ``client``：进程内 app（TestClient）。单用户模式下匿名即超管，无需登录。
- ``fake_llm`` / ``patch_llm``：把假客户端同时挂到路由模块的 ``llm`` 与
  它的 ``builder.llm``（两处各自持有实例，漏一处就会真调模型）。
- ``patch_sandbox``：把工具/Agent 的沙箱执行打桩为「直接成功」，避免真起
  子进程；需要真执行验证的用例单独不加桩。
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.api


# ────────────────────────── 固定输出样本 ──────────────────────────

TOOL_SPEC_MD = """# 平方计算器

## 功能概述

计算输入数字的平方。

## 输入规范

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| n | int | 是 | 待计算数字 |

## 输出规范

| 字段 | 类型 | 说明 |
|---|---|---|
| square | int | 平方结果 |

## 依赖环境

无

## 运行机制

读取参数 n，返回 n 的平方。
"""

TOOL_CODE = '''"""平方计算器"""


def main(params):
    n = int(params.get("n", 0))
    return {"status": "success", "data": {"square": n * n}}
'''

AGENT_SPEC_MD = """# 平方 Agent

## 功能概述

把用户输入的数字平方后返回。

## 角色定位

交互式助手：接收一个数字，返回它的平方。

## 输入规范

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| n | int | 是 | 待计算数字 |

## 输出规范

| 字段 | 类型 | 说明 |
|---|---|---|
| square | int | 平方结果 |

## 执行流程

1. 读取输入 n
2. 计算 n 的平方
3. 返回结果
"""


class FakeLLM:
    """鸭子类型的 LLM 客户端，覆盖路由用到的三种调用方式。

    - ``chat``：一次性补全（标签生成、代码修改、工具匹配）
    - ``chat_stream``：逐 token 的 async 生成器
    - ``chat_stream_events``：**异步生成器**，逐个 yield ``(kind, token)``
      ——路由侧是按元组解包的（`async for kind, token in ...`），不是 dict

    事件顺序固定为 reasoning → delta，最后以 ``("done", ...)`` 收尾。
    """

    def __init__(self, reply: str = "[]", spec: str = TOOL_SPEC_MD,
                 reason: str = '{"matches": [], "reason": "单元测试固定输出"}',
                 async_chat: bool = False):
        # 默认 chat 返回空标签数组：generate-spec 用它补标签，返回代码会让
        # 标签解析出一堆乱码，干扰断言
        self.reply = reply
        self.spec = spec
        self.reason = reason
        # 不同路由对 chat 的调用方式不一致（有的 await，有的直接取返回值），
        # 用一个开关切换形态，避免为每个路由写一套假客户端。
        self.async_chat = async_chat

    def chat(self, messages, **kwargs):
        if self.async_chat:
            async def _coro():
                return self.reply
            return _coro()
        return self.reply

    async def chat_stream(self, messages, **kwargs):
        for piece in ("固定", "流式", "输出"):
            yield {"type": "delta", "token": piece}

    async def chat_stream_events(self, messages, **kwargs):
        yield ("reasoning", "正在分析需求")
        for i in range(0, len(self.spec), 40):
            yield ("delta", self.spec[i:i + 40])
        yield ("done", "")


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def client():
    """进程内应用实例（单用户模式：匿名请求按超管执行）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def patch_llm(monkeypatch, module, llm):
    """把假客户端挂到路由模块与它的 builder（两处各自持有 llm 实例）。"""
    monkeypatch.setattr(module, "llm", llm, raising=False)
    builder = getattr(module, "builder", None)
    if builder is not None:
        monkeypatch.setattr(builder, "llm", llm, raising=False)


def patch_sandbox(monkeypatch, module) -> None:
    """把沙箱执行打成「立即成功」，避免真起子进程跑用户代码。"""
    async def ok_execute(*args, **kwargs):
        return {
            "exit_code": 0,
            "stdout": json.dumps({"status": "success", "data": {"square": 25}}),
            "stderr": "",
            "success": True,
        }

    builder = getattr(module, "builder", None)
    if builder is None:
        return
    for name in ("sandbox_execute", "_run_agent_once", "dry_run"):
        if hasattr(builder, name):
            monkeypatch.setattr(builder, name, ok_execute, raising=False)


def read_sse(client, method: str, url: str, **kwargs) -> list[tuple[str, dict]]:
    """把 SSE 响应解析为 [(event, data), ...]；非 200 时返回单个 http_error。"""
    with client.stream(method, url, **kwargs) as resp:
        if resp.status_code != 200:
            return [("http_error", {"status": resp.status_code,
                                    "body": resp.read().decode(errors="replace")})]
        events: list[tuple[str, dict]] = []
        name = ""
        for line in resp.iter_lines():
            line = line if isinstance(line, str) else line.decode()
            if not line.strip():
                name = ""
                continue
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                raw = line.split(":", 1)[1].strip()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"raw": raw}
                events.append((name or "message",
                               payload if isinstance(payload, dict) else {"value": payload}))
        return events


def names(events) -> list[str]:
    return [n for n, _ in events]
