"""LLM 相关 API 实现"""
from core.llm.client import create_llm_client
from config.settings import get_llm_api_config


class ApiLlmChat:
    """api-llm-chat: LLM 非流式对话"""

    @staticmethod
    async def call(**kwargs) -> dict:
        client = create_llm_client()
        content = await client.chat(
            messages=kwargs.get("messages", []),
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 4096),
        )
        return {"content": content}


class ApiLlmChatStream:
    """api-llm-chat-stream: LLM 流式对话"""

    @staticmethod
    async def call(**kwargs) -> dict:
        # 流式需要特殊处理，这里返回生成器标记
        return {"stream": True, "messages": kwargs.get("messages", []),
                "temperature": kwargs.get("temperature", 0.7),
                "max_tokens": kwargs.get("max_tokens", 4096)}


class ApiLlmGetConfig:
    """api-llm-get-config: 获取 LLM 配置"""

    @staticmethod
    def call(**kwargs) -> dict:
        provider = kwargs.get("provider", "deepseek")
        return get_llm_api_config(provider)


class ApiDoubaoGetKey:
    """api-doubao-get-key: 获取豆包 API Key"""

    @staticmethod
    def call(**kwargs) -> dict:
        return get_llm_api_config("doubao")


class ApiDeepseekGetKey:
    """api-deepseek-get-key: 获取 DeepSeek API Key 和模型名称（从 .env 读取）"""

    @staticmethod
    def call(**kwargs) -> dict:
        return get_llm_api_config("deepseek")
