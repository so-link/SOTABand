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
        self.config = config or settings.llm
        self.client = AsyncOpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
        )
        self.model = self.config.model

    async def chat_stream(
        self, messages: list[dict], **kwargs
    ) -> AsyncGenerator[str, None]:
        """流式调用 DeepSeek v4"""
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            temperature=kwargs.get("temperature", self.config.temperature),
            timeout=self.config.timeout,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    async def chat(self, messages: list[dict], **kwargs) -> str:
        """非流式调用 DeepSeek v4"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=False,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            temperature=kwargs.get("temperature", self.config.temperature),
            timeout=self.config.timeout,
        )
        return response.choices[0].message.content or ""


def create_llm_client(config: LLMConfig = None) -> LLMClient:
    """工厂函数：根据配置创建对应的 LLM 客户端"""
    cfg = config or settings.llm
    if cfg.provider == "deepseek":
        return DeepSeekClient(cfg)
    # 其他 OpenAI 兼容提供商
    return DeepSeekClient(cfg)
