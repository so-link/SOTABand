"""LLM 客户端 — 默认 DeepSeek v4，兼容任意 OpenAI 协议服务商"""

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


class OpenAICompatibleClient(LLMClient):
    """OpenAI 兼容协议客户端

    支持所有遵循 OpenAI /v1/chat/completions 协议的服务商：
    DeepSeek / OpenAI / Kimi(Moonshot) / 智谱 GLM / 通义千问 /
    硅基流动 / MiniMax / MiMo Coding Plan / 豆包(火山方舟) 等，
    通过 base_url + api_key + model 即可接入。
    """

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
        """流式对话"""
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            temperature=kwargs.get("temperature", self.config.temperature),
            timeout=self.config.timeout,
            stream_options={"include_usage": True},
        )
        finish_reason = None
        async for chunk in stream:
            # include_usage=True 时，流末尾会下发只含 usage 统计的 chunk，
            # 其 choices 为空列表；部分服务商还会下发 delta 为 None 的空 chunk。
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is not None and delta.content:
                yield delta.content
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason
        # 如果因为 token 限制被截断，yield 警告标记
        if finish_reason == "length":
            yield "\n\n# [WARNING] Response truncated due to max_tokens limit"

    async def aclose(self) -> None:
        """关闭底层异步客户端（httpx 连接池），避免事件循环关闭时残留任务报错"""
        try:
            await self.client.close()
        except Exception:
            pass

    async def chat(self, messages: list[dict], **kwargs) -> str:
        """非流式对话，返回完整响应"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=False,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            temperature=kwargs.get("temperature", self.config.temperature),
            timeout=self.config.timeout,
        )
        content = response.choices[0].message.content or ""
        if response.choices[0].finish_reason == "length":
            content += "\n\n# [WARNING] Response truncated due to max_tokens limit"
        return content


# 向后兼容别名：历史生成的 Agent 代码可能直接引用 DeepSeekClient
DeepSeekClient = OpenAICompatibleClient


def create_llm_client(config: LLMConfig = None) -> LLMClient:
    """工厂函数：根据配置创建对应的 LLM 客户端

    - 所有 OpenAI 兼容协议的服务商统一走 OpenAICompatibleClient
      （provider 由 LLMConfig 携带，配置在 config/settings.py 的 PROVIDER_PRESETS）
    - 未来接入 Anthropic Claude / Google Gemini 等非兼容协议时，
      在此新增对应 Client 子类分支即可，调用方无需改动。
    """
    cfg = config or settings.llm
    # if cfg.provider == "claude": return ClaudeClient(cfg)
    # if cfg.provider == "gemini": return GeminiClient(cfg)
    return OpenAICompatibleClient(cfg)
