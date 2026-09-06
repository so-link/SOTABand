"""Agent: Test — test-agent"""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import time as _time
from pathlib import Path
from typing import AsyncGenerator

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.base import BaseAgent, AgentContext

class TestAgent(BaseAgent):
    """Test — test-agent"""

    def __init__(self, spec=None):
        super().__init__(spec)
        self._llm = None

    def _get_llm(self):
        """懒加载 LLM 客户端"""
        if self._llm is None:
            from config.settings import settings
            from core.llm.client import DeepSeekClient
            self._llm = DeepSeekClient(settings.llm)
        return self._llm

    def _log_step(self, step_name: str, action: str, input_data: dict = None, output_data: dict = None, error: str = None):
        """记录执行步骤日志到 resources/agents/logs/{agent_id}-{timestamp}.md"""
        if not hasattr(self, '_log_file'):
            log_dir = PROJECT_ROOT / "resources" / "agents" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            agent_id = self.spec.id if self.spec else "unknown"
            self._log_file = log_dir / f"{agent_id}-{ts}.md"
            # 写入文件头
            self._log_file.write_text(
                f"# Agent 执行日志: {agent_id}\n\n"
                f"**启动时间**: {ts}\n\n"
                f"| 步骤 | 时间 | 操作 | 输入 | 输出 | 状态 |\n"
                f"|------|------|------|------|------|------|\n",
                encoding="utf-8"
            )
        now = datetime.datetime.now().strftime("%H:%M:%S")
        inp_str = json.dumps(input_data, ensure_ascii=False)[:200] if input_data else "-"
        out_str = json.dumps(output_data, ensure_ascii=False)[:200] if output_data else "-"
        status = f"❌ {error}" if error else "✅ 成功"
        row = f"| {step_name} | {now} | {action} | {inp_str} | {out_str} | {status} |\n"
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(row)

    async def _extract_params_with_llm(self, tool_name: str, user_input: str, param_defs: list) -> dict:
        """使用 LLM 从用户输入中智能提取工具/API 所需参数"""
        if not param_defs:
            return {}
        prompt = (
            f"从用户输入中提取工具参数。\n\n"
            f"工具: {tool_name}\n"
            f"参数定义: {json.dumps(param_defs, ensure_ascii=False)}\n"
            f"用户输入: \"{user_input}\"\n\n"
            f"返回 JSON 格式:"
        )
        try:
            response = await self._get_llm().chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=300
            )
            text = response.strip()
            if text.startswith("```"): text = text.split("\n", 1)[1].rsplit("\n", 1)[0]
            return json.loads(text)
        except Exception:
            return {}


    async def execute(self, ctx: AgentContext, **kwargs) -> AsyncGenerator[dict, None]:
        """Agent 主执行逻辑 — 按 MD 执行流程逐步: LLM解析参数→调用工具→LLM合成结果"""
        content = kwargs.get("content", "").strip()
        if not content:
            yield {"event": "error", "data": {"text": "输入内容为空，请提供任务描述。"}}
            yield {"event": "done", "data": {}}
            return

        try:
            self._log_step("步骤1", "获取用户输入", {"content": content[:100]})

            llm = self._get_llm()
            prompt_text = f"根据任务输入生成自然语言回复: {content}"
            response = await llm.chat(messages=[{"role": "user", "content": prompt_text}])

            self._log_step("步骤2", "LLM生成回复", {"response": response[:100]})
            yield {"event": "content", "data": {"text": response}}
            yield {"event": "done", "data": {}}
        except Exception as exc:
            yield {"event": "error", "data": {"text": f"执行失败: {exc}"}}
            yield {"event": "done", "data": {}}
