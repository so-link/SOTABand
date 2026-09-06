"""LLM 客户端 — 默认 DeepSeek v4，兼容 OpenAI 协议"""

from abc import ABC, abstractmethod
from typing import AsyncGenerator

from openai import AsyncOpenAI

from config.settings import LLMConfig, settings


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


class DeepSeekClient(LLMClient):
    """DeepSeek v4 客户端（OpenAI 兼容协议）"""

    def __init__(self, config: LLMConfig = None):
        self._config = config
        # 若未显式传入 config，则每次调用动态读取 settings.llm，
        # 以便运行时更新 api_key / model 后立即生效
        self._dynamic = config is None

    @property
    def config(self) -> LLMConfig:
        return self._config if self._config is not None else settings.llm

    def _build_client(self) -> AsyncOpenAI:
        cfg = self.config
        return AsyncOpenAI(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
        )

    async def chat_stream(
        self, messages: list[dict], **kwargs
    ) -> AsyncGenerator[str, None]:
        """流式调用 DeepSeek v4"""
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
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason
        # 如果因为 token 限制被截断，yield 警告标记
        if finish_reason == "length":
            yield "\n\n# [WARNING] Response truncated due to max_tokens limit"

    async def chat(self, messages: list[dict], **kwargs) -> str:
        """非流式调用 DeepSeek v4"""
        client = self._build_client()
        cfg = self.config
        response = await client.chat.completions.create(
            model=cfg.model,
            messages=messages,
            stream=False,
            max_tokens=kwargs.get("max_tokens", cfg.max_tokens),
            temperature=kwargs.get("temperature", cfg.temperature),
            timeout=cfg.timeout,
        )
        content = response.choices[0].message.content or ""
        if response.choices[0].finish_reason == "length":
            content += "\n\n# [WARNING] Response truncated due to max_tokens limit"
        return content


def create_llm_client(config: LLMConfig = None) -> LLMClient:
    """工厂函数：根据配置创建对应的 LLM 客户端"""
    cfg = config or settings.llm
    if cfg.provider == "deepseek":
        return DeepSeekClient(cfg)
    # 其他 OpenAI 兼容提供商
    return DeepSeekClient(cfg)
