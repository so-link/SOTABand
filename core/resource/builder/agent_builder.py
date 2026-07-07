"""Agent 代码生成器 — 从 MD 规范文档生成 Agent Python 代码

架构：模板组装 + LLM 填充执行逻辑 + 后处理修复。
工具/API 辅助方法由模板机械生成（保证正确），execute() 体由 LLM 生成。
"""

import ast
import json
import re
from pathlib import Path

from core.resource.builder.builder_base import BaseBuilder
from core.llm.client import create_llm_client

# ═══════════════════════════════════════════════════════════════════
# 头文件模板 — 始终正确，非 LLM 生成
# ═══════════════════════════════════════════════════════════════════

HEADER_TEMPLATE = '''"""Agent: {agent_name} — {agent_id}"""

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
'''

# ═══════════════════════════════════════════════════════════════════
# 工具辅助方法模板 — 机械生成，不依赖 LLM
# ═══════════════════════════════════════════════════════════════════

TOOL_HELPER_TEMPLATE = '''
    def _call_tool_{tool_id_safe}(self, **params) -> dict:
        """调用工具: {tool_name} (tool_id: {tool_id})"""
        impl_path = PROJECT_ROOT / "resources" / "tools" / "implementations" / "{tool_id}" / "tool.py"
        if not impl_path.exists():
            return {{"status": "failed", "message": f"工具不存在: {tool_id}"}}

        code = impl_path.read_text()
        venv_python = PROJECT_ROOT / "resources" / "tools" / "implementations" / "{tool_id}" / ".venv" / "bin" / "python"
        python_exe = str(venv_python) if venv_python.exists() else sys.executable

        test_script = (
            f"import json, sys\\n"
            f"sys.path.insert(0, {{json.dumps(str(PROJECT_ROOT))}})\\n"
            f"code = {{json.dumps(code)}}\\n"
            f"exec(code)\\n"
            f"result = execute(**{{json.dumps(params)}})\\n"
            f"print(json.dumps(result, default=str))\\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(test_script)
            tmp_path = f.name
        try:
            proc = subprocess.run(
                [python_exe, tmp_path],
                capture_output=True, text=True,
                env={{**os.environ, "TOOL_DIR": str(impl_path.parent), "_PROJECT_ROOT": str(PROJECT_ROOT)}},
            )
            if proc.returncode == 0:
                return json.loads(proc.stdout.strip())
            else:
                return {{"status": "failed", "message": proc.stderr[:300]}}
        finally:
            os.unlink(tmp_path)
'''

# ═══════════════════════════════════════════════════════════════════
# API 辅助方法模板 — 机械生成
# ═══════════════════════════════════════════════════════════════════

API_HELPER_TEMPLATE = '''
    def _call_api_{api_id_safe}(self, **params) -> dict:
        """调用 API: {api_name} (api_id: {api_id})"""
        from core.api import get_api
        try:
            api = get_api("{api_id}")
            return api.call(**params)
        except Exception as e:
            return {{"status": "failed", "message": str(e)}}
'''

# ═══════════════════════════════════════════════════════════════════
# 类骨架模板 — 含 LLM 参数提取辅助方法 + tool/API 调用
# ═══════════════════════════════════════════════════════════════════

CLASS_SKELETON = '''
class {cls_name}(BaseAgent):
    """{description}"""

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
        """记录执行步骤日志到 resources/agents/logs/{{agent_id}}-{{timestamp}}.md"""
        if not hasattr(self, '_log_file'):
            log_dir = PROJECT_ROOT / "resources" / "agents" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            agent_id = self.spec.id if self.spec else "unknown"
            self._log_file = log_dir / f"{{agent_id}}-{{ts}}.md"
            # 写入文件头
            self._log_file.write_text(
                f"# Agent 执行日志: {{agent_id}}\\n\\n"
                f"**启动时间**: {{ts}}\\n\\n"
                f"| 步骤 | 时间 | 操作 | 输入 | 输出 | 状态 |\\n"
                f"|------|------|------|------|------|------|\\n",
                encoding="utf-8"
            )
        now = datetime.datetime.now().strftime("%H:%M:%S")
        inp_str = json.dumps(input_data, ensure_ascii=False)[:200] if input_data else "-"
        out_str = json.dumps(output_data, ensure_ascii=False)[:200] if output_data else "-"
        status = f"❌ {{error}}" if error else "✅ 成功"
        row = f"| {{step_name}} | {{now}} | {{action}} | {{inp_str}} | {{out_str}} | {{status}} |\\n"
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(row)

    async def _extract_params_with_llm(self, tool_name: str, user_input: str, param_defs: list) -> dict:
        """使用 LLM 从用户输入中智能提取工具/API 所需参数"""
        if not param_defs:
            return {{}}
        prompt = (
            f"从用户输入中提取工具参数。\\n\\n"
            f"工具: {{tool_name}}\\n"
            f"参数定义: {{json.dumps(param_defs, ensure_ascii=False)}}\\n"
            f"用户输入: \\"{{user_input}}\\"\\n\\n"
            f"返回 JSON 格式:"
        )
        try:
            response = await self._get_llm().chat(
                messages=[{{"role": "user", "content": prompt}}],
                temperature=0.0, max_tokens=300
            )
            text = response.strip()
            if text.startswith("```"): text = text.split("\\n", 1)[1].rsplit("\\n", 1)[0]
            return json.loads(text)
        except Exception:
            return {{}}
{helper_methods}

    async def execute(self, ctx: AgentContext, **kwargs) -> AsyncGenerator[dict, None]:
        """Agent 主执行逻辑 — 按 MD 执行流程逐步: LLM解析参数→调用工具→LLM合成结果"""
{execute_body}
'''


class AgentCodeBuilder(BaseBuilder):
    """从 Agent MD 规范文档生成 Agent Python 代码"""

    def __init__(self, llm_client=None):
        self.llm = llm_client or create_llm_client()

    # ══════════════════════════════════════════════════════════
    # MD 解析 — 提取工具/API 引用
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def _extract_tool_refs(spec_md: str) -> list[dict]:
        """从 MD 中提取【【工具名称】】引用"""
        names = re.findall(r'【【(.+?)】】', spec_md)
        refs = []
        seen = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            info = AgentCodeBuilder._lookup_tool(name)
            refs.append({"name": name, "id": info["id"], "params": info["params"]})
        return refs

    @staticmethod
    def _extract_api_refs(spec_md: str) -> list[dict]:
        """从 MD 中提取【API名称】引用（排除工具引用【【...】】）"""
        tool_spans = [(m.start(), m.end()) for m in re.finditer(r'【【.+?】】', spec_md)]
        refs = []
        seen = set()
        for m in re.finditer(r'【(.+?)】', spec_md):
            if any(m.start() >= s and m.end() <= e for s, e in tool_spans):
                continue
            name = m.group(1)
            if name in seen:
                continue
            seen.add(name)
            info = AgentCodeBuilder._lookup_api(name)
            refs.append({"name": name, "id": info["id"], "params": info.get("params", {})})
        return refs

    @staticmethod
    def _extract_role(spec_md: str) -> str:
        """从 MD frontmatter 中提取 role"""
        m = re.search(r'^role:\s*(\w+)', spec_md, re.MULTILINE)
        return m.group(1) if m else "task"

    @staticmethod
    def _extract_frontmatter_field(spec_md: str, field: str) -> str:
        """从 MD frontmatter 中提取指定字段"""
        m = re.search(rf'^{field}:\s*(.+)', spec_md, re.MULTILINE)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _lookup_tool(name: str) -> dict:
        tools_dir = Path(__file__).resolve().parent.parent.parent.parent / "resources" / "tools"
        registry_file = tools_dir / "registry.json"
        try:
            if registry_file.exists():
                for t in json.loads(registry_file.read_text()):
                    if t.get("name") == name:
                        return {"id": t["id"], "params": t.get("param_meta", [])}
        except Exception:
            pass
        return {"id": name, "params": []}

    @staticmethod
    def _lookup_api(name: str) -> dict:
        api_dir = Path(__file__).resolve().parent.parent.parent / "api"
        registry_file = api_dir / "registry.json"
        try:
            if registry_file.exists():
                for a in json.loads(registry_file.read_text()):
                    if a.get("name") == name:
                        return {"id": a["id"], "params": a.get("input_schema", {})}
        except Exception:
            pass
        return {"id": name, "params": {}}

    @staticmethod
    def _to_class_name(agent_id: str) -> str:
        parts = agent_id.replace("-", " ").replace("_", " ").split()
        return "".join(p.capitalize() for p in parts)

    @staticmethod
    def _safe_ident(s: str) -> str:
        return s.replace("-", "_").replace(".", "_")

    # ══════════════════════════════════════════════════════════
    # 校验
    # ══════════════════════════════════════════════════════════

    async def validate_spec(self, spec: dict) -> bool:
        """校验 MD 规范文档是否包含必需的段落（兼容新旧模板）"""
        md = spec.get("raw_md", "")
        required = ["功能概述", "角色定位", "输入规范", "输出规范"]
        flow_ok = "执行流程" in md or "运行机制" in md
        version_ok = "版本历史" in md
        return all(s in md for s in required) and flow_ok and version_ok

    # ══════════════════════════════════════════════════════════
    # 代码组装（核心）
    # ══════════════════════════════════════════════════════════

    async def build(self, spec: dict) -> str:
        """组装完整 Agent 代码：模板头部 + 机械生成的辅助方法 + LLM 生成的 execute 体"""
        raw_md = spec.get("raw_md", "")
        agent_id = spec.get("id", self._extract_frontmatter_field(raw_md, "id") or "custom-agent")
        agent_name = spec.get("name", self._extract_frontmatter_field(raw_md, "name") or "Custom Agent")
        role = spec.get("role", self._extract_role(raw_md))

        tool_refs = self._extract_tool_refs(raw_md)
        api_refs = self._extract_api_refs(raw_md)
        cls_name = self._to_class_name(agent_id)

        # 1. 头部 — 始终正确
        header = HEADER_TEMPLATE.format(agent_name=agent_name, agent_id=agent_id)

        # 2. 工具/API 辅助方法 — 机械生成，保证正确
        helpers = ""
        for tr in tool_refs:
            helpers += TOOL_HELPER_TEMPLATE.format(
                tool_id_safe=self._safe_ident(tr["id"]),
                tool_name=tr["name"],
                tool_id=tr["id"],
            )
        for ar in api_refs:
            helpers += API_HELPER_TEMPLATE.format(
                api_id_safe=self._safe_ident(ar["id"]),
                api_name=ar["name"],
                api_id=ar["id"],
            )

        # 3. execute() 体 — LLM 生成
        execute_body = await self._generate_execute_body(
            raw_md=raw_md,
            agent_name=agent_name,
            role=role,
            tool_refs=tool_refs,
            api_refs=api_refs,
        )

        # 4. 组装
        code = header + CLASS_SKELETON.format(
            cls_name=cls_name,
            description=f"{agent_name} — {agent_id}",
            helper_methods=helpers,
            execute_body=execute_body,
        )

        # 5. 后处理修复
        code = self._fix_common_errors(code)

        return code

    # ══════════════════════════════════════════════════════════
    # LLM 生成 execute() 体
    # ══════════════════════════════════════════════════════════

    async def _generate_execute_body(
        self,
        raw_md: str,
        agent_name: str,
        role: str,
        tool_refs: list[dict],
        api_refs: list[dict],
    ) -> str:
        """让 LLM 生成 execute() 方法体（仅函数体内部代码）"""

        # 构建可用方法清单
        tool_calls_desc = ""
        for tr in tool_refs:
            safe_id = self._safe_ident(tr["id"])
            tool_calls_desc += f"""
- **{tr['name']}** (`【【{tr['name']}】】`): 调用 `self._call_tool_{safe_id}(**params)` → 返回 `{{"status":"success"|"failed", "message":"...", "data":{{...}}}}`
  参数定义: {json.dumps(tr['params'], ensure_ascii=False)}
"""

        api_calls_desc = ""
        for ar in api_refs:
            safe_id = self._safe_ident(ar["id"])
            api_calls_desc += f"""
- **{ar['name']}** (`【{ar['name']}】`): 调用 `self._call_api_{safe_id}(**params)` → 返回 dict
"""

        prompt = f"""你是一个 Python 代码生成器。生成 Agent 类的 `execute()` 方法体（仅方法内部的代码，不包含方法签名和类定义）。

## Agent 信息
- 名称: {agent_name}
- 角色: {role}

## 完整的 MD 规范文档
{raw_md}

## 可用的工具调用（已实现的辅助方法，直接调用即可）
{tool_calls_desc if tool_calls_desc else "（无）"}

## 可用的 API 调用（已实现的辅助方法）
{api_calls_desc if api_calls_desc else "（无）"}

## 代码生成模式（必须遵循）

对于每个工具/API 调用步骤，按以下模式生成代码：

### 模式 A: 首次从用户输入提取参数 → 保存变量 → 调用工具
```python
# Step 1: 从用户输入中一次提取所有变量（只做一次！）
self._log_step("步骤1", "从用户输入提取参数", {{"input": content[:100]}})
extracted = await self._extract_params_with_llm(
    tool_name="提取参数",
    user_input=content,
    param_defs=[
        {{"name": "需求", "type": "string", "required": true, "desc": "图片描述"}},
        {{"name": "数量", "type": "int", "required": true, "desc": "生成数量"}},
        {{"name": "效果", "type": "string", "required": true, "desc": "编辑效果"}},
    ]
)
self._log_step("步骤1", "参数提取完成", {{"extracted": extracted}})

# Step 2: 用提取的变量直接构造工具参数（不要重新从 content 提取！）
prompt = extracted.get("需求")  # ← 直接用变量
num = extracted.get("数量")
self._log_step("步骤2", "调用工具", {{"prompt": prompt, "num": num}})
result = self._call_tool_xxx(prompt=prompt, num_images=int(num))
self._log_step("步骤2", "工具返回", {{"status": result.get("status")}})
```

### 模式 B: 上一步输出 → LLM 提取数据 → 调用下一个工具
```python
# Step N: 用 LLM 从上一步输出中提取下一工具所需参数（仅此场景用 LLM 提取）
prev_output = json.dumps(prev_result, ensure_ascii=False)
self._log_step("步骤N", "从上步输出提取参数", {{"output": prev_output[:200]}})
next_params = await self._extract_params_with_llm(
    tool_name="下一个工具名",
    user_input=prev_output,
    param_defs=[{{"name": "image_path", "type": "string", "required": true, "desc": "从上一步输出中提取图片路径"}}]
)
self._log_step("步骤N", "调用工具", {{"params": next_params}})
next_result = self._call_tool_next_tool(**next_params)
self._log_step("步骤N", "工具返回", {{"status": next_result.get("status")}})
```
【重要】绝对不要用硬编码字段！禁止 `prev_result["data"]["field"]`！
【重要】_extract_params_with_llm 只在两种场景调用: (1)首次从 content 提取 (2)从上一步工具输出提取。其他场景都用已提取的变量。

### 模式 C: 用 LLM 生成最终回复（最后一步）
```python
llm = self._get_llm()
prompt_text = f"根据结果生成自然语言回复: {{json.dumps(result, ensure_ascii=False)}}"
response = await llm.chat(messages=[{{"role": "user", "content": prompt_text}}])
yield {{"event": "content", "data": {{"text": response}}}}
```

## 代码要求

1. `content = kwargs.get("content", "").strip()` 获取用户输入
2. 按 MD 规范的"执行流程"逐步执行，每步对应一个代码段落
3. **每步执行前/后记录日志**: `self._log_step("步骤名", "操作描述", input_data={{...}}, output_data={{...}})`
4. 第一个工具：用 `await self._extract_params_with_llm()` 从用户输入 content 中提取参数，保存到局部变量
5. 后续工具：【重要】用已提取的变量直接构造参数，绝对不要再次对 content 调用 _extract_params_with_llm
6. 仅当需要从上一步工具输出中提取数据时，才再次调用 _extract_params_with_llm，输入为 json.dumps(prev_result)
6. 【禁止】绝对不要硬编码字段: 禁止 result["data"]["xxx"] 或 result.get("data",{{}}).get("xxx") — 始终用 LLM 提取
7. 调用 `self._call_tool_xxx(...)` 或 `self._call_api_xxx(...)`
8. 最后一步用 LLM 将结果转化为自然语言回复
9. 错误处理: yield error 后 yield done

## 生成规则

- 代码缩进为 8 个空格
- 输入为空时提示用户
- 工具返回 status=="failed" 时返回友好错误
- 【重要】工具和 API 辅助方法是同步的，直接调用即可: `result = self._call_tool_xxx(...)`，不要加 await
- 不要使用 ctx.session、ctx.metadata_id
- 直接返回 Python 代码，不要 markdown 代码块"""

        response = await self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=100000,
        )
        body = response.strip()

        # 去除可能的 markdown 代码块
        if "```python" in body:
            body = body.split("```python")[1].split("```")[0]
        elif "```" in body:
            body = body.split("```")[1].split("```")[0]

        # 确保有缩进（8 空格）
        lines = body.split("\n")
        indented = []
        for line in lines:
            stripped = line.rstrip()
            if stripped == "":
                indented.append("")
            elif stripped.startswith("        "):
                indented.append(stripped)
            else:
                indented.append("        " + stripped.lstrip())
        return "\n".join(indented)

    # ══════════════════════════════════════════════════════════
    # 后处理 — 修复常见 LLM 错误
    # ══════════════════════════════════════════════════════════

    def _fix_common_errors(self, code: str) -> str:
        """修复 LLM 生成代码中的常见错误"""
        fixes = [
            # 1. 错误的 import
            (r'^from base_agent import', 'from core.agent.base import'),
            (r'^from agent_base import', 'from core.agent.base import'),
            (r'^import base_agent', 'from core.agent.base import BaseAgent, AgentContext'),
            # 2. __import__ hack
            (r"__import__\('sys'\)\.executable", "sys.executable"),
            (r'__import__\("sys"\)\.executable', "sys.executable"),
            # 3. ctx.get_input 不存在
            (r'ctx\.get_input\(', 'kwargs.get('),
            # 3b. ctx.session 不存在
            (r'ctx\.session\[', 'self._state['),
            (r'ctx\.session\.', 'self._state.'),
            # 3c. ctx.metadata_id 不存在 — 应为 ctx.session_id
            (r'ctx\.metadata_id', 'ctx.session_id'),
            # 3d. await self._call_tool_ — 工具/API 辅助方法是同步的
            (r'await self\._call_tool_', 'self._call_tool_'),
            (r'await self\._call_api_', 'self._call_api_'),
            # 3e. kwargs.get("messageId" — 应为 ctx.session_id
            (r'kwargs\.get\("messageId",?\s*""?\)?', 'ctx.session_id'),
            # 3f. 确保 llm.chat(messages 前有 await (异步方法); 修复重复 await await
            (r'(?<!\bawait )llm\.chat\(messages', 'await llm.chat(messages'),
            (r'await +await +llm\.chat\(', 'await llm.chat('),
            # 4. 删除 LLM 在方法体内重复的 import（仅匹配缩进的，保护头部导入）
            (r'\n        import json\n', '\n'),
            (r'\n        import sys\n', '\n'),
            (r'\n        from pathlib import Path\n', '\n'),
            (r'\n        from typing import AsyncGenerator\n', '\n'),
            (r'\n        from core.agent.base import.*\n', '\n'),
        ]

        for pattern, replacement in fixes:
            code = re.sub(pattern, replacement, code)

        return code

    # ══════════════════════════════════════════════════════════
    # 沙箱测试
    # ══════════════════════════════════════════════════════════

    async def dry_run(self, code: str) -> dict:
        """沙箱预跑：语法检查 + 接口校验 + 导入检查"""
        results = {"passed": [], "failed": [], "errors": []}

        # 1. 语法检查
        try:
            ast.parse(code)
            results["passed"].append("语法检查通过")
        except SyntaxError as e:
            results["failed"].append(f"语法错误: {e}")
            return results

        # 2. 检查正确导入
        bad_imports = []
        for line in code.split("\n"):
            if "from base_agent" in line or "import base_agent" in line:
                bad_imports.append(line.strip())
        if bad_imports:
            results["failed"].append(f"错误的导入: {bad_imports}")
        else:
            results["passed"].append("导入路径正确")

        # 3. 检查错误方法调用
        if "ctx.get_input" in code:
            results["failed"].append('使用了不存在的 ctx.get_input()，应为 kwargs.get()')
        else:
            results["passed"].append("无错误方法调用")

        # 4. 检查 BaseAgent 继承
        if "BaseAgent" in code:
            results["passed"].append("继承 BaseAgent")
        else:
            results["failed"].append("未继承 BaseAgent")

        # 5. 检查 execute 方法
        if "async def execute" in code:
            results["passed"].append("实现 execute() 方法")
        else:
            results["failed"].append("未实现 execute() 方法")

        # 6. 检查 yield done 是否存在
        if 'yield {"event": "done"' in code or "yield {'event': 'done'" in code:
            results["passed"].append("包含 done 事件")
        else:
            results["failed"].append("缺少 done 事件")

        # 7. 检查 __import__ hack
        if "__import__" in code:
            results["failed"].append("包含 __import__ hack，应使用 import sys; sys.executable")
        else:
            results["passed"].append("无 __import__ hack")

        return results
