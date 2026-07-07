"""Agent: 天气查询助手 — weather-query-agent"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.base import BaseAgent, AgentContext


class WeatherQueryAgent(BaseAgent):
    """天气查询助手 — weather-query-agent"""

    def __init__(self, spec=None):
        super().__init__(spec)

    def _call_tool_get_weather(self, **params) -> dict:
        """调用工具: 获取当前天气 (tool_id: get-weather)"""
        impl_path = PROJECT_ROOT / "resources" / "tools" / "implementations" / "get-weather" / "tool.py"
        if not impl_path.exists():
            return {"status": "failed", "message": f"工具不存在: get-weather"}

        code = impl_path.read_text()
        venv_python = PROJECT_ROOT / "resources" / "tools" / "implementations" / "get-weather" / ".venv" / "bin" / "python"
        python_exe = str(venv_python) if venv_python.exists() else sys.executable

        test_script = (
            f"import json, sys\n"
            f"sys.path.insert(0, {json.dumps(str(PROJECT_ROOT))})\n"
            f"code = {json.dumps(code)}\n"
            f"exec(code)\n"
            f"result = execute(**{json.dumps(params)})\n"
            f"print(json.dumps(result, default=str))\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(test_script)
            tmp_path = f.name
        try:
            proc = subprocess.run([python_exe, tmp_path], capture_output=True, text=True)
            if proc.returncode == 0:
                return json.loads(proc.stdout.strip())
            else:
                return {"status": "failed", "message": proc.stderr[:300]}
        finally:
            os.unlink(tmp_path)

    async def execute(self, ctx: AgentContext, **kwargs) -> AsyncGenerator[dict, None]:
        """多轮天气查询：每轮接收城市名 → 返回天气 → 等待下一轮"""
        content = kwargs.get("content", "").strip()

        if not content:
            yield {"event": "content", "data": {"text": "请输入一个城市名称，例如：北京"}}
            yield {"event": "done", "data": {"messageId": ctx.session_id}}
            return

        weather_result = self._call_tool_get_weather(city=content)

        if weather_result.get("status") != "success":
            yield {"event": "error", "data": {"code": "WEATHER_QUERY_FAILED", "message": weather_result.get("message", "查询失败")}}
        else:
            data = weather_result.get("data", {})
            temp = data.get("temperature", "未知")
            cond = data.get("condition", data.get("weather", "未知"))
            hum = data.get("humidity", "未知")
            desc = f"{content}当前天气：{cond}，温度{temp}，湿度{hum}"
            yield {"event": "content", "data": {"text": desc}}

        yield {"event": "done", "data": {"messageId": ctx.session_id}}
