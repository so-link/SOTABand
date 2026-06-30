"""Agent 代码生成器 — 从 MD 规范文档生成 Agent Python 代码"""

import ast
import re
from pathlib import Path

from core.resource.builder.builder_base import BaseBuilder
from core.llm.client import create_llm_client

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "agent" / "templates"  # templates stay under core/agent/

ROLE_TEMPLATE_MAP = {
    "task": "task.py.tmpl",
    "interactive": "interactive.py.tmpl",
    "orchestrator": "orchestrator.py.tmpl",
    "observer": "observer.py.tmpl",
}


class AgentCodeBuilder(BaseBuilder):
    """从 Agent MD 规范文档生成 Agent Python 代码"""

    def __init__(self, llm_client=None):
        self.llm = llm_client or create_llm_client()

    async def validate_spec(self, spec: dict) -> bool:
        """校验 MD 规范文档是否包含必需的 9 个段落"""
        md = spec.get("raw_md", "")
        required_sections = [
            "功能概述", "角色定位", "输入规范", "输出规范",
            "运行机制", "工具使用", "通信协议", "配置参数", "版本历史",
        ]
        return all(s in md for s in required_sections)

    async def build(self, spec: dict) -> str:
        """生成 Agent 代码"""
        role = spec.get("role", "task")

        # 标准角色 → 模板生成
        if role in ROLE_TEMPLATE_MAP:
            return self._template_generate(spec, role)

        # 自定义角色 → LLM 生成
        return await self._llm_generate(spec)

    async def dry_run(self, code: str) -> dict:
        """沙箱预跑：语法检查 + 基础接口校验"""
        results = {"passed": [], "failed": [], "errors": []}

        # 1. 语法检查
        try:
            ast.parse(code)
            results["passed"].append("语法检查通过")
        except SyntaxError as e:
            results["failed"].append(f"语法错误: {e}")
            return results

        # 2. 检查 BaseAgent 继承
        if "BaseAgent" in code:
            results["passed"].append("继承 BaseAgent")
        else:
            results["failed"].append("未继承 BaseAgent")

        # 3. 检查 execute 方法
        if "async def execute" in code:
            results["passed"].append("实现 execute() 方法")
        else:
            results["failed"].append("未实现 execute() 方法")

        # 4. 依赖检查（抽取 import 语句）
        imports = re.findall(r'^import\s+(\S+)|^from\s+(\S+)', code, re.MULTILINE)
        if imports:
            results["passed"].append(f"检测到依赖: {len(imports)} 个")

        return results

    def _template_generate(self, spec: dict, role: str) -> str:
        """生成可独立运行的 Agent 代码（基于默认模板 + spec 参数）"""
        return self._default_template(spec)

    async def _llm_generate(self, spec: dict) -> str:
        """LLM 生成自定义 Agent 代码"""
        prompt = f"""Generate a Python class that inherits from BaseAgent.

Agent spec:
- id: {spec.get('id', 'custom-agent')}
- name: {spec.get('name', 'Custom Agent')}
- role: {spec.get('role', 'task')}
- description: {spec.get('description', '')}
- inputs: {spec.get('inputs', {})}
- outputs: {spec.get('outputs', {})}

The class must:
1. Inherit from BaseAgent
2. Implement async execute(self, ctx, **kwargs) -> AsyncGenerator[dict, None]
3. Yield events: {{"event": "content", "data": {{"text": "..."}}}}, {{"event": "done", "data": {{"messageId": "..."}}}}

Return ONLY the Python code, no explanations."""

        response = await self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=100000,
        )
        # 提取代码块
        code = response
        if "```python" in code:
            code = code.split("```python")[1].split("```")[0]
        elif "```" in code:
            code = code.split("```")[1].split("```")[0]
        return code.strip()

    @staticmethod
    def _to_class_name(agent_id: str) -> str:
        """将 agent-id 转为 PascalCase 类名"""
        parts = agent_id.replace("-", " ").replace("_", " ").split()
        return "".join(p.capitalize() for p in parts)

    @staticmethod
    def _default_template(spec: dict) -> str:
        """默认模板 — 生成可独立运行的 Agent 代码（内置 LLM 调用）"""
        cls_name = AgentCodeBuilder._to_class_name(spec.get("id", "CustomAgent"))
        return f'''"""Agent: {spec.get("name", "Custom")} — {spec.get("description", "")}"""

import sys
from pathlib import Path
from typing import AsyncGenerator

# 确保项目根目录可导入
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.base import BaseAgent, AgentContext


class {cls_name}(BaseAgent):
    """{spec.get("description", "Task agent")}"""

    def __init__(self, spec=None):
        super().__init__(spec)
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from config.settings import settings
            from core.llm.client import DeepSeekClient
            self._llm = DeepSeekClient(settings.llm)
        return self._llm

    def _build_prompt(self) -> str:
        """从 MD 规范文档构建 system prompt"""
        if self.spec and self.spec.raw_md:
            return self.spec.raw_md
        return "You are a helpful AI assistant."

    async def execute(self, ctx: AgentContext, **kwargs) -> AsyncGenerator[dict, None]:
        content = kwargs.get("content", "")
        attachments = kwargs.get("attachments", [])

        system_prompt = self._build_prompt()
        messages = [
            {{"role": "system", "content": system_prompt}},
        ]
        if attachments:
            att_info = ", ".join(
                a.get("fileName", a.get("file_name", "unknown"))
                for a in attachments
            )
            messages.append(
                {{"role": "user", "content": f"[附加文件: {{att_info}}]\\n\\n{{content}}"}}
            )
        else:
            messages.append({{"role": "user", "content": content}})

        llm = self._get_llm()
        full = ""
        async for token in llm.chat_stream(messages=messages):
            full += token
            yield {{"event": "content", "data": {{"text": token}}}}

        yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}
'''
