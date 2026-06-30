"""Tool 代码生成器 — 从 MD 规范文档生成工具代码 + 测试数据"""

import ast
import json
import re
from pathlib import Path

from core.resource.builder.builder_base import BaseBuilder
from core.llm.client import create_llm_client


class ToolCodeBuilder(BaseBuilder):
    """从 Tool MD 规范文档生成可执行的 Python 工具代码"""

    def __init__(self, llm_client=None):
        self.llm = llm_client or create_llm_client()

    async def validate_spec(self, spec: dict) -> bool:
        md = spec.get("raw_md", "")
        required = ["功能概述", "输入规范", "输出规范", "依赖环境", "运行机制"]
        return all(s in md for s in required)

    async def build(self, spec: dict) -> str:
        """生成工具代码"""
        # 从 MD 解析结构化信息
        parsed = self._parse_spec(spec)

        # 使用 LLM 生成代码（工具逻辑差异大，不适合模板）
        return await self._llm_generate(parsed)

    async def generate_test_data(self, spec: dict) -> dict:
        """根据输入规范自动构造测试数据"""
        parsed = self._parse_spec(spec)
        inputs = parsed.get("inputs", [])

        test_normal = {}
        test_boundary = {}
        test_error = {}

        for param in inputs:
            name = param.get("name", "unknown")
            ptype = param.get("type", "string")
            default = param.get("default")

            normal, boundary, error = self._gen_test_values(name, ptype, default)
            test_normal[name] = normal
            if boundary is not None:
                test_boundary[name] = boundary
            test_error[name] = error

        return {
            "normal": {"input": test_normal, "description": "正常输入"},
            "boundary": {"input": test_boundary, "description": "边界值"} if test_boundary else None,
            "error": {"input": test_error, "description": "异常输入"},
        }

    async def dry_run(self, code: str, test_input: dict = None) -> dict:
        """沙箱测试"""
        results = {"passed": [], "failed": [], "errors": []}

        # 1. 语法检查
        try:
            ast.parse(code)
            results["passed"].append("语法检查通过")
        except SyntaxError as e:
            results["failed"].append(f"语法错误: {e}")
            return results

        # 2. 接口检查
        if "def execute" in code:
            results["passed"].append("实现 execute() 函数")
        else:
            results["failed"].append("未找到 execute() 函数")

        # 3. 依赖检查
        imports = re.findall(r'^(?:import\s+(\S+)|from\s+(\S+))', code, re.MULTILINE)
        deps = [i[0] or i[1] for i in imports if i[0] or i[1]]
        stdlib = {"sys", "os", "json", "pathlib", "typing", "re", "math", "datetime", "subprocess"}
        external = [d for d in deps if d.split(".")[0] not in stdlib and not d.startswith("core.")]
        if external:
            results["passed"].append(f"外部依赖: {', '.join(external)}")
        else:
            results["passed"].append("无外部依赖")

        # 4. 功能测试（在子进程中执行，防止死循环挂起）
        try:
            import subprocess as _sp
            import json as _json
            import sys as _sys
            import tempfile as _tmp
            import os as _os

            test_script = (
                f"import json\n"
                f"code = {_json.dumps(code)}\n"
                f"exec(code)\n"
                f"result = execute(**{_json.dumps(test_input)})\n"
                f"print(json.dumps(result, default=str))\n"
            )
            with _tmp.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(test_script)
                tmp_path = f.name

            try:
                proc = _sp.run(
                    [_sys.executable, tmp_path],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode == 0:
                    output_data = proc.stdout.strip()
                    try:
                        output_data = _json.loads(output_data)
                    except _json.JSONDecodeError:
                        pass
                    results["passed"].append("功能测试: execute() 执行成功")
                    results["test_details"] = {
                        "input": test_input,
                        "output": output_data,
                    }
                else:
                    results["failed"].append(f"功能测试失败:\n{proc.stderr[:200]}")
                    results["test_details"] = {
                        "input": test_input,
                        "output": None,
                        "stderr": proc.stderr[:500],
                    }
            except _sp.TimeoutExpired:
                results["failed"].append("功能测试超时 (10s)，代码可能存在死循环")
                results["test_details"] = {"input": test_input, "output": None, "error": "timeout"}
            finally:
                _os.unlink(tmp_path)
        except Exception as e:
            results["failed"].append(f"功能测试异常: {str(e)[:200]}")

        return results

    # ── 内部方法 ──

    def _parse_spec(self, spec: dict) -> dict:
        """从 raw_md 解析结构化信息"""
        md = spec.get("raw_md", "")
        result = {
            "id": spec.get("id", "custom-tool"),
            "name": spec.get("name", "Custom Tool"),
            "description": "",
            "inputs": [],
            "outputs": [],
        }

        # 解析 frontmatter
        fm_match = re.search(r'^---\n(.*?)\n---', md, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    result[k.strip()] = v.strip()

        # 解析输入规范表格
        in_section = False
        for line in md.split("\n"):
            if "输入规范" in line:
                in_section = True
                continue
            if in_section and line.startswith("##"):
                break
            if in_section and line.startswith("|") and "参数名" not in line and "---" not in line:
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) >= 4:
                    result["inputs"].append({
                        "name": parts[0],
                        "type": parts[1],
                        "required": parts[2] == "是",
                        "default": parts[3] if parts[3] != "—" and parts[3] != "-" else None,
                        "description": parts[4] if len(parts) > 4 else "",
                    })

        return result

    def _gen_test_values(self, name: str, ptype: str, default: str = None):
        """为单个参数生成测试值"""
        ptype = ptype.lower()

        if "string" in ptype:
            if "path" in name.lower():
                return ("/data/test_sample.edf", "", "/nonexistent/file.edf")
            return ("hello", "", None)

        if "int" in ptype:
            return (3, 0, -1)

        if "float" in ptype:
            return (1.0, 0.0, "not_a_number")

        if "list" in ptype or "[" in ptype:
            return ([0, 1, 2], [], "not_a_list")

        if "bool" in ptype:
            return (True, False, "not_bool")

        if "dict" in ptype:
            return ({"key": "value"}, {}, "not_dict")

        return ("test", "", None)

    async def _llm_generate(self, parsed: dict) -> str:
        """LLM 生成工具代码"""
        prompt = f"""Generate a Python tool function based on this specification:

Tool ID: {parsed.get('id', 'custom-tool')}
Tool Name: {parsed.get('name', 'Custom Tool')}
Language: {parsed.get('language', 'python')}

Input parameters:
{json.dumps(parsed.get('inputs', []), indent=2, ensure_ascii=False)}

Output format:
{json.dumps(parsed.get('outputs', []), indent=2, ensure_ascii=False)}

Requirements:
1. Create a function: def execute(**kwargs) -> dict[str, Any]:
2. Validate all required input parameters
3. Return a dict with at least: {{"status": "success"|"failed", "message": "", "data": {{}}}}
4. Handle errors gracefully with try/except
5. Add docstring with Args and Returns
6. If it's a data processing tool, handle file I/O properly
7. Import only standard library + specified dependencies

Return ONLY the Python code, no explanations. """

        response = await self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=100000, timeout=30,
        )
        code = response
        if "```python" in code:
            code = code.split("```python")[1].split("```")[0]
        elif "```" in code:
            code = code.split("```")[1].split("```")[0]
        return code.strip()
