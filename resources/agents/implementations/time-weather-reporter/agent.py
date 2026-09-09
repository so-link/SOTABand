"""Agent: 时间天气汇报员 — time-weather-reporter"""

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

class TimeWeatherReporter(BaseAgent):
    """时间天气汇报员 — time-weather-reporter"""

    def __init__(self, spec=None):
        super().__init__(spec)
        self._llm = None

    def _get_llm(self):
        """懒加载 LLM 客户端"""
        if self._llm is None:
            from config.settings import settings
            from core.llm.client import create_llm_client
            self._llm = create_llm_client(settings.llm)
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

        # 步骤1: 获取当前系统时间
        self._log_step("步骤1", "获取当前系统时间", {})
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_step("步骤1", "当前时间", {"time": current_time})

        # 步骤2: 调用天气查询工具获取广州市天气，带重试逻辑
        city = self.config.get("city", "广州市") if hasattr(self, 'config') else "广州市"
        max_retries = 2
        weather_result = None
        last_error = None

        for attempt in range(max_retries + 1):
                self._log_step("步骤2", f"调用天气查询工具，第{attempt+1}次", {"city": city})
                try:
                        weather_result = self._call_tool_get_weather(city=city)
                        if isinstance(weather_result, dict) and weather_result.get("status") == "failed":
                                last_error = weather_result.get("message", "天气查询失败")
                                self._log_step("步骤2", "工具返回失败", {"error": last_error})
                                if attempt < max_retries:
                                        continue
                                else:
                                        break
                        self._log_step("步骤2", "天气查询成功", {"result": str(weather_result)[:200]})
                        break
                except Exception as e:
                        last_error = str(e)
                        self._log_step("步骤2", f"异常: {last_error}", {})
                        if attempt < max_retries:
                                continue
                        else:
                                weather_result = None
                                break

        if weather_result is None or (isinstance(weather_result, dict) and weather_result.get("status") == "failed"):
                error_msg = last_error or "天气查询失败"
                yield {"event": "error", "data": {"message": f"获取天气信息失败: {error_msg}"}}
                yield {"event": "done", "data": {"messageId": f"error-{datetime.datetime.now().timestamp()}"}}
                return

        # 步骤3: 使用LLM生成汇报文本
        self._log_step("步骤3", "生成汇报文本", {"time": current_time, "weather_raw": str(weather_result)[:200]})
        llm = self._get_llm()
        prompt_text = (
                f"当前时间：{current_time}\n"
                f"天气查询原始结果：{json.dumps(weather_result, ensure_ascii=False)}\n"
                "请根据以上信息生成一句简洁的时间天气汇报文本，格式为：当前时间：YYYY-MM-DD HH:MM:SS，广州市天气：<天气状况>，<温度>℃。只输出这一句，不要额外内容。"
        )
        response = await llm.chat(messages=[{"role": "user", "content": prompt_text}])
        self._log_step("步骤3", "LLM回复", {"response": response})

        # 步骤4: 输出 content 和 done 事件
        yield {"event": "content", "data": {"text": response}}
        yield {"event": "done", "data": {"messageId": f"done-{datetime.datetime.now().timestamp()}"}}

