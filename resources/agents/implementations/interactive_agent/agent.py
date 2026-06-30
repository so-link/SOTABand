"""交互 Agent — 系统默认入口，开机自启，处理用户对话"""

import json
import time
from typing import AsyncGenerator

from core.agent.base import BaseAgent, AgentContext, AgentSpec, AgentRole
from core.llm.client import create_llm_client, LLMClient


SYSTEM_PROMPT = """你是 MAIA Engine 的交互 Agent，一个多智能体多模态智能处理引擎的对话入口。

## 你的职责
1. 理解用户的自然语言意图
2. 分析用户的需求类型（简单数据处理 / 复杂多步任务编排）
3. 引导用户完成任务

## 当前系统能力
- 工具空间: 内置 EEG 信号处理、频谱分析、异常检测等工具
- Agent 空间: 数据加载、异常检测、编排等专业 Agent
- 数据空间: 支持 EEG (EDF)、图像 (PNG/TIFF)、表格 (CSV)、文本等多模态数据
- 模型空间: 支持 LLM、ViT、3D-CNN、时序模型
- 探索式能力增长: 当没有匹配工具时，可自动生成代码，经用户核验后注册为本地工具

## 响应格式
- 普通对话: 用自然语言回复
- 简单数据处理需求: 分析需求 → 匹配工具 → 建议执行方案
- 复杂任务: 分析步骤 → 建议使用编排模式
- 无匹配工具: 提示用户，建议自动生成工具

## 注意事项
- 回复简洁清晰，逐步引导用户
- 如用户提及具体数据文件，引用文件名
- 保持友好、专业的语气"""


class InteractiveAgent(BaseAgent):
    """交互 Agent — 开机自启，处理所有用户对话输入"""

    def __init__(self, llm_client: LLMClient = None):
        spec = AgentSpec(
            id="interactive-agent",
            name="交互Agent",
            version="1.0.0",
            role=AgentRole.INTERACTIVE,
            description="MAIA Engine 主交互入口，处理用户对话",
            inputs={
                "content": {"type": "string", "required": True},
                "attachments": {"type": "list", "required": False},
                "session_id": {"type": "string", "required": True},
                "user_id": {"type": "string", "required": True},
            },
            outputs={
                "content": "文本增量（流式）",
                "card": "内联卡片",
                "done": "响应结束",
                "error": "错误信息",
            },
            required_tools=["tool-llm-client"],
            optional_tools=[
                "tool-resource-discoverer",
                "tool-code-builder",
                "tool-orchestrator",
            ],
            config={
                "max_history": 20,
                "temperature": 0.7,
                "max_tokens": 100000,
            },
        )
        super().__init__(spec, config=spec.config)
        self.llm = llm_client or create_llm_client()
        self._sessions: dict[str, list[dict]] = {}

    async def execute(
        self, ctx: AgentContext, **kwargs
    ) -> AsyncGenerator[dict, None]:
        """处理用户输入，调用 DeepSeek v4，流式返回"""
        content = kwargs.get("content", "")
        attachments = kwargs.get("attachments", [])

        if not content.strip():
            yield {"event": "done", "data": {"messageId": ctx.session_id}}
            return

        # 构建消息历史
        session_key = ctx.session_id or "default"
        history = self._sessions.get(session_key, [])
        if not history:
            history = [{"role": "system", "content": SYSTEM_PROMPT}]

        # 构建用户消息
        user_msg = self._build_user_message(content, attachments)
        history.append({"role": "user", "content": user_msg})

        # 裁剪历史
        max_history = self.config.get("max_history", 20)
        if len(history) > max_history * 2 + 1:
            history = [history[0]] + history[-(max_history * 2):]

        try:
            full_response = ""
            async for token in self.llm.chat_stream(
                messages=history,
                temperature=kwargs.get("temperature", self.config.get("temperature", 0.7)),
                max_tokens=kwargs.get("max_tokens", self.config.get("max_tokens", 4096)),
            ):
                full_response += token
                yield {"event": "content", "data": {"text": token}}

            # 保存对话历史
            history.append({"role": "assistant", "content": full_response})
            self._sessions[session_key] = history

            # 结束
            yield {
                "event": "done",
                "data": {"messageId": f"msg-{int(time.time() * 1000)}"},
            }

        except Exception as e:
            yield {
                "event": "error",
                "data": {"code": "llm_error", "message": str(e)},
            }
            yield {
                "event": "done",
                "data": {"messageId": f"msg-{int(time.time() * 1000)}"},
            }

    def _build_user_message(self, content: str, attachments: list) -> str:
        """构建带附件上下文的用户消息"""
        if not attachments:
            return content

        parts = [content, "", "附加文件:"]
        for att in attachments:
            name = att.get("fileName", att.get("file_name", "unknown"))
            fmt = att.get("format", "unknown")
            size = att.get("fileSize", att.get("file_size", 0))
            if size > 1048576:
                size_str = f"{size / 1048576:.1f}MB"
            elif size > 1024:
                size_str = f"{size / 1024:.1f}KB"
            else:
                size_str = f"{size}B"
            parts.append(f"- {name} ({fmt.upper()}, {size_str})")

        return "\n".join(parts)


# 全局单例，开机时初始化
interactive_agent = InteractiveAgent()
