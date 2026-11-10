"""LLM 客户端 — 默认 DeepSeek v4，兼容任意 OpenAI 协议服务商"""

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import AsyncGenerator

from openai import AsyncOpenAI

from config.settings import LLMConfig, settings


def _parse_tool_calls(raw, stream_shape: bool = False) -> list[dict]:
    """把 SDK 的 tool_calls 对象（或流式拼接后的槽位）统一为纯 dict 列表。

    输出 ``[{"id","name","arguments"}]``，arguments 尽量 json 解析为 dict，
    非法 JSON 时保留原始字符串，由调用方决定如何报错给模型。
    """
    out = []
    for tc in raw or []:
        if stream_shape:
            tc_id, name, args = tc["id"], tc["name"], tc["arguments"]
        else:
            fn = getattr(tc, "function", None)
            tc_id = getattr(tc, "id", "") or ""
            name = getattr(fn, "name", "") if fn else ""
            args = getattr(fn, "arguments", "") if fn else ""
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except (ValueError, TypeError):
                pass  # 参数非法 JSON：原样保留，网关侧会回错误让模型自纠
        out.append({"id": tc_id, "name": name, "arguments": args})
    return out


# LLM 并发锁按用途分三池，避免长生成把短任务「堵车」（评审 M-1）：
#   chat —— 对话/工具调用主链路（默认 2）
#   gen  —— 规格/代码/图片等长生成链（默认 1：单次生成重，网关并发容忍度低）
#   aux  —— 标题摘要、标签、工具匹配等秒级后台短任务（默认 4：不被长任务饿死）
# 上限可在「系统设置」中调整（.env：LLM_MAX_CONCURRENCY / LLM_GEN_MAX_CONCURRENCY /
# LLM_AUX_MAX_CONCURRENCY），运行时即时生效。
LLM_POOLS = ("chat", "gen", "aux")
_POOL_ENV_VARS = {
    "chat": ("LLM_MAX_CONCURRENCY", "2"),
    "gen": ("LLM_GEN_MAX_CONCURRENCY", "1"),
    "aux": ("LLM_AUX_MAX_CONCURRENCY", "4"),
}


def _pool_default(pool: str) -> int:
    env_var, fallback = _POOL_ENV_VARS[pool]
    try:
        return max(1, int(os.getenv(env_var, fallback) or fallback))
    except ValueError:
        return max(1, int(fallback))


_LLM_CONCURRENCY: dict[str, int] = {p: _pool_default(p) for p in LLM_POOLS}
_LLM_SEMAPHORES: dict[str, asyncio.Semaphore] = {
    p: asyncio.Semaphore(_LLM_CONCURRENCY[p]) for p in LLM_POOLS
}


def get_llm_concurrency(pool: str = "chat") -> int:
    """指定池的当前 LLM 并发上限"""
    return _LLM_CONCURRENCY[pool]


def set_llm_concurrency(limit: int, pool: str = "chat") -> int:
    """动态调整指定池的 LLM 并发上限。

    重建该池信号量：已持有旧信号量的请求继续在旧对象上释放，新请求使用新信号量。
    调用方在 acquire 前应先把当前信号量取到局部变量，避免 finally 里读到新对象。
    """
    if pool not in LLM_POOLS:
        raise ValueError(f"未知 LLM 池: {pool}（可选 {LLM_POOLS}）")
    _LLM_CONCURRENCY[pool] = max(1, int(limit))
    _LLM_SEMAPHORES[pool] = asyncio.Semaphore(_LLM_CONCURRENCY[pool])
    return _LLM_CONCURRENCY[pool]


class LLMClient(ABC):
    """LLM 客户端抽象"""

    @abstractmethod
    async def chat_stream(
        self, messages: list[dict], **kwargs
    ) -> AsyncGenerator[str, None]:
        """流式对话，逐 token yield"""
        ...

    @abstractmethod
    async def chat(self, messages: list[dict], **kwargs) -> str:
        """非流式对话，返回完整响应"""
        ...

    async def chat_stream_events(
        self, messages: list[dict], **kwargs
    ) -> AsyncGenerator[tuple[str, str], None]:
        """流式事件流，逐片 yield ``(kind, text)``。

        kind 取值：
        - ``reasoning``：推理模型的思考过程（DeepSeek/网关的 reasoning 字段）
        - ``content``：最终回答正文

        默认实现退化为仅正文，子类可覆盖以暴露推理内容。
        """
        async for token in self.chat_stream(messages, **kwargs):
            yield ("content", token)

    async def chat_full(
        self, messages: list[dict], tools: list[dict] | None = None,
        tool_choice: str | dict | None = None, **kwargs
    ) -> dict:
        """非流式对话（function calling 版），返回完整 assistant 消息。

        返回 ``{"content": str, "tool_calls": [{"id","name","arguments"}],
        "finish_reason": str}``。不传 tools 时行为等价于 chat()，
        tool_calls 恒为空列表。
        """
        content = await self.chat(messages, **kwargs)
        return {"content": content, "tool_calls": [], "finish_reason": "stop"}

    async def chat_stream_with_tools(
        self, messages: list[dict], tools: list[dict] | None = None,
        tool_choice: str | dict | None = None, **kwargs
    ) -> AsyncGenerator[tuple, None]:
        """流式对话 + function calling。

        逐片 yield ``(kind, payload)``：
        - ``("reasoning"|"content", str)``：与 chat_stream_events 相同
        - 流结束时若模型请求调用工具，最后 yield
          ``("tool_calls", [{"id","name","arguments": dict}, ...])``

        流式 tool_calls 按 index 分槽拼接 arguments 增量片段，
        收齐后统一 json.loads；解析失败视为参数非法（原样回传字符串）。
        默认实现忽略 tools（服务商不支持时降级为纯文本），子类可覆盖。
        """
        async for kind, text in self.chat_stream_events(messages, **kwargs):
            yield (kind, text)


class OpenAICompatibleClient(LLMClient):
    """OpenAI 兼容协议客户端

    支持所有遵循 OpenAI /v1/chat/completions 协议的服务商：
    DeepSeek / OpenAI / Kimi(Moonshot) / 智谱 GLM / 通义千问 /
    硅基流动 / MiniMax / MiMo Coding Plan / 豆包(火山方舟) 等，
    通过 base_url + api_key + model 即可接入。
    """

    def __init__(self, config: LLMConfig = None, config_resolver=None,
                 pool: str = "chat"):
        self._config = config
        # 并发池：chat/gen/aux（见模块级 LLM_POOLS）。信号量在每次 acquire 时
        # 经 _pool_semaphore() 现查，不在构造时捕获，保证设置页调整后即时生效。
        if pool not in LLM_POOLS:
            raise ValueError(f"未知 LLM 池: {pool}（可选 {LLM_POOLS}）")
        self._pool = pool
        # 动态配置来源：
        #   config_resolver 优先（如「生成模型」按 LLM_GEN_* 每次现算）；
        #   其次 config=None 表示每次读 settings.llm，
        # 以便运行时更新 api_key / model 后立即生效
        self._resolver = config_resolver
        self._dynamic = config is None
        # 最近一次构建的客户端：客户端是按需构建的，需留下引用供 aclose() 关闭，
        # 否则 httpx 连接池残留会在事件循环关闭时报错
        self._last_client: AsyncOpenAI | None = None

    @property
    def config(self) -> LLMConfig:
        if self._config is not None:
            return self._config
        if self._resolver is not None:
            return self._resolver()
        return settings.llm

    def _pool_semaphore(self) -> asyncio.Semaphore:
        """现查本池当前信号量（运行时调整并发后指向新对象）"""
        return _LLM_SEMAPHORES[self._pool]

    def _build_client(self) -> AsyncOpenAI:
        cfg = self.config
        client = AsyncOpenAI(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            # 关闭 SDK 自带重试：生成规范/代码这类长调用一旦超时，重试只会把
            # 等待时间 ×3 并在网关制造重复请求；超时由 cfg.timeout 控制，
            # 代理层 504 由路由的心跳流式响应兜底。
            max_retries=0,
        )
        self._last_client = client
        return client

    async def chat_stream_events(
        self, messages: list[dict], **kwargs
    ) -> AsyncGenerator[tuple[str, str], None]:
        """流式对话，区分「思考过程」与「正文」两类增量。

        推理模型（如 DeepSeek 系）会在 delta 里返回 reasoning 字段：
        原生字段名为 ``reasoning_content``，部分网关则用 ``reasoning``
        （openai SDK 会把它放进 ``delta.model_extra``）。两者都兼容。
        """
        # 本池并发锁：网关对单 Key 并发容忍度低，池内排队等待（先取局部引用，
        # 避免运行时并发上限被调整后 finally 释放到新信号量上）
        sem = self._pool_semaphore()
        await sem.acquire()
        try:
            client = self._build_client()
            cfg = self.config
            stream = await client.chat.completions.create(
                model=cfg.model,
                messages=messages,
                stream=True,
                max_tokens=kwargs.get("max_tokens", cfg.max_tokens),
                temperature=kwargs.get("temperature", cfg.temperature),
                timeout=cfg.timeout,
                stream_options={"include_usage": True},
            )
            finish_reason = None
            async for chunk in stream:
                # include_usage=True 时，流末尾会下发只含 usage 统计的 chunk，
                # 其 choices 为空列表；部分服务商还会下发 delta 为 None 的空 chunk。
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is not None:
                    reasoning = getattr(delta, "reasoning_content", None)
                    if not reasoning:
                        extra = getattr(delta, "model_extra", None) or {}
                        reasoning = extra.get("reasoning")
                    if reasoning:
                        yield ("reasoning", reasoning)
                    if delta.content:
                        yield ("content", delta.content)
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
            # 如果因为 token 限制被截断，yield 警告标记
            if finish_reason == "length":
                yield ("content", "\n\n# [WARNING] Response truncated due to max_tokens limit")
        finally:
            sem.release()

    async def chat_stream(
        self, messages: list[dict], **kwargs
    ) -> AsyncGenerator[str, None]:
        """流式对话，仅下发正文（思考过程被丢弃，保持旧调用方语义不变）"""
        async for kind, text in self.chat_stream_events(messages, **kwargs):
            if kind == "content":
                yield text

    def _tool_kwargs(self, tools, tool_choice) -> dict:
        """组装 tools/tool_choice 请求参数（None 不传，保持旧端点兼容）"""
        kw = {}
        if tools:
            kw["tools"] = tools
            if tool_choice is not None:
                kw["tool_choice"] = tool_choice
        return kw

    async def chat_full(
        self, messages: list[dict], tools: list[dict] | None = None,
        tool_choice: str | dict | None = None, **kwargs
    ) -> dict:
        """非流式对话（function calling 版），返回完整 assistant 消息。

        返回 ``{"content": str, "tool_calls": [{"id","name","arguments"}],
        "finish_reason": str}``；arguments 为 json 解析后的 dict，
        解析失败时保留原始字符串（由调用方判错）。
        """
        sem = self._pool_semaphore()
        await sem.acquire()
        try:
            client = self._build_client()
            cfg = self.config
            response = await client.chat.completions.create(
                model=cfg.model,
                messages=messages,
                stream=False,
                max_tokens=kwargs.get("max_tokens", cfg.max_tokens),
                temperature=kwargs.get("temperature", cfg.temperature),
                timeout=kwargs.get("timeout", cfg.timeout),
                **self._tool_kwargs(tools, tool_choice),
            )
            msg = response.choices[0].message
            return {
                "content": msg.content or "",
                "tool_calls": _parse_tool_calls(getattr(msg, "tool_calls", None)),
                "finish_reason": response.choices[0].finish_reason or "stop",
            }
        finally:
            sem.release()

    async def chat_stream_with_tools(
        self, messages: list[dict], tools: list[dict] | None = None,
        tool_choice: str | dict | None = None, **kwargs
    ) -> AsyncGenerator[tuple, None]:
        """流式对话 + function calling。

        tool_calls 增量按 index 分槽：id/name 取首个非空值，
        arguments 逐段拼接、流末统一 json 解析后以
        ``("tool_calls", [...])`` 收尾（无 tool_calls 则不 yield 该事件）。
        """
        sem = self._pool_semaphore()
        await sem.acquire()
        try:
            client = self._build_client()
            cfg = self.config
            stream = await client.chat.completions.create(
                model=cfg.model,
                messages=messages,
                stream=True,
                max_tokens=kwargs.get("max_tokens", cfg.max_tokens),
                temperature=kwargs.get("temperature", cfg.temperature),
                timeout=cfg.timeout,
                stream_options={"include_usage": True},
                **self._tool_kwargs(tools, tool_choice),
            )
            slots: dict[int, dict] = {}
            finish_reason = None
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is not None:
                    reasoning = getattr(delta, "reasoning_content", None)
                    if not reasoning:
                        extra = getattr(delta, "model_extra", None) or {}
                        reasoning = extra.get("reasoning")
                    if reasoning:
                        yield ("reasoning", reasoning)
                    if delta.content:
                        yield ("content", delta.content)
                    for tc in getattr(delta, "tool_calls", None) or []:
                        idx = getattr(tc, "index", 0) or 0
                        slot = slots.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if fn.name:
                                slot["name"] += fn.name
                            if fn.arguments:
                                slot["arguments"] += fn.arguments
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
            if finish_reason == "length":
                yield ("content", "\n\n# [WARNING] Response truncated due to max_tokens limit")
            if slots:
                yield ("tool_calls", _parse_tool_calls(
                    [slots[i] for i in sorted(slots)], stream_shape=True))
        finally:
            sem.release()

    async def aclose(self) -> None:
        """关闭底层异步客户端（httpx 连接池），避免事件循环关闭时残留任务报错"""
        try:
            if self._last_client is not None:
                await self._last_client.close()
        except Exception:
            pass

    async def chat(self, messages: list[dict], **kwargs) -> str:
        """非流式对话，返回完整响应"""
        sem = self._pool_semaphore()
        await sem.acquire()
        try:
            client = self._build_client()
            cfg = self.config
            response = await client.chat.completions.create(
                model=cfg.model,
                messages=messages,
                stream=False,
                max_tokens=kwargs.get("max_tokens", cfg.max_tokens),
                temperature=kwargs.get("temperature", cfg.temperature),
                timeout=kwargs.get("timeout", cfg.timeout),
            )
            content = response.choices[0].message.content or ""
            if response.choices[0].finish_reason == "length":
                content += "\n\n# [WARNING] Response truncated due to max_tokens limit"
            return content
        finally:
            sem.release()

    async def images_generate(self, model: str, prompt: str, **kwargs) -> list[str]:
        """图片生成/编辑（OpenAI 兼容 /images/generations），返回图片 URL 列表。

        豆包 Ark 等兼容端点支持该路由；Ark 专有的 watermark 等参数经
        extra_body 透传。图片生成属长生成负载，恒走 gen 池排队，
        不占用客户端自身所属池（通常是 chat）的名额。
        """
        sem = _LLM_SEMAPHORES["gen"]
        await sem.acquire()
        try:
            client = self._build_client()
            params: dict = {
                "model": model,
                "prompt": prompt,
                "size": kwargs.get("size") or "2K",
                "response_format": "url",
                "n": int(kwargs.get("n") or 1),
            }
            if kwargs.get("image"):
                params["image"] = kwargs["image"]
            if "watermark" in kwargs:
                params["extra_body"] = {"watermark": bool(kwargs["watermark"])}
            resp = await client.images.generate(**params)
            return [d.url for d in (resp.data or []) if getattr(d, "url", None)]
        finally:
            sem.release()


# 向后兼容别名：历史生成的 Agent 代码可能直接引用 DeepSeekClient
DeepSeekClient = OpenAICompatibleClient


def create_llm_client_for(provider: str, model: str = None,
                          **overrides) -> LLMClient:
    """按 provider 创建客户端，key/端点/模型自动从 .env 解析。

    工具作者的便捷入口——等价于
    ``create_llm_client(LLMConfig(provider=provider, model=model, **overrides))``。
    密钥来源是 .env 的 ``<PROVIDER>_API_KEY``（主 key 为 LLM_PROVIDER 指向的那家，
    其余为副 key），调用方**不需要也无法接触到明文 key**。

    用法（工具代码里切换副 key）::

        client = create_llm_client_for("doubao")            # 用 .env 里的 DOUBAO_API_KEY
        client = create_llm_client_for("mimo", model="mimo-v2.5-pro")

    Raises:
        ValueError: 该 provider 无预设且未提供 base_url，或 .env 中没有对应 key。
    """
    cfg = LLMConfig(provider=provider, model=model, **overrides)
    if not cfg.api_key:
        raise ValueError(
            f".env 中没有 {provider.upper()}_API_KEY，"
            f"请先在 .env 添加该条目（主 key 为 LLM_API_KEY）"
        )
    return create_llm_client(cfg)


def create_llm_client(config: LLMConfig = None, pool: str = "chat") -> LLMClient:
    """工厂函数：根据配置创建对应的 LLM 客户端

    - 所有 OpenAI 兼容协议的服务商统一走 OpenAICompatibleClient
      （provider 由 LLMConfig 携带，配置在 config/settings.py 的 PROVIDER_PRESETS）
    - 未来接入 Anthropic Claude / Google Gemini 等非兼容协议时，
      在此新增对应 Client 子类分支即可，调用方无需改动。
    - pool 指定并发池（chat/gen/aux）：对话主链路用默认 chat，
      标题/标签/匹配等秒级后台短任务用 pool="aux"，避免被长生成排队拖累。
    """
    cfg = config or settings.llm
    # if cfg.provider == "claude": return ClaudeClient(cfg)
    # if cfg.provider == "gemini": return GeminiClient(cfg)
    return OpenAICompatibleClient(cfg, pool=pool)


def create_gen_llm_client() -> LLMClient:
    """创建「生成/推理增强模型」客户端 —— 工具与 Agent 的规格/代码生成链专用。

    与对话客户端的区别：对话模型必须支持 function calling（交互式 Agent 主循环
    依赖它），而生成规范/写代码更适合用不并发调用、但推理更强的模型。
    本工厂返回的客户端**每次调用时**才解析配置（见 resolve_gen_llm_config），
    因此设置页保存后立即生效；未配置 LLM_GEN_PROVIDER 时自动回退全局对话模型，
    行为与 create_llm_client() 完全一致。并发恒走 gen 池，不与对话互相排队。
    """
    from config.settings import resolve_gen_llm_config

    return OpenAICompatibleClient(config_resolver=resolve_gen_llm_config, pool="gen")
