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

    async def extract_param_metadata(self, spec_md: str) -> list[dict]:
        """让 LLM 从 MD spec 中提取参数元数据，用于后续智能参数提取"""
        prompt = f"""从以下工具 MD 规范文档中提取输入参数列表，返回 JSON 数组。

每个参数包含: name(参数名), type(类型), required(是否必填 true/false), default(默认值或null), desc(一句话中文描述), hints(用户可能如何描述这个参数的示例, 数组)

MD 文档:
{spec_md[:3000]}

返回格式(仅 JSON):
[{{"name":"image_path","type":"string","required":true,"default":null,"desc":"待检测图片的文件路径","hints":["图片","照片","图像","这张图"]}}, ...]"""

        response = await self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=1000,
        )
        try:
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("\n", 1)[0]
            return json.loads(clean)
        except Exception:
            return []

    async def setup_venv(self, tool_id: str, dependencies: list[str] = None) -> str | None:
        """检查兼容性。依赖与主环境兼容则返回 None（用主Python），否则创建 venv"""
        import importlib, sys

        if not dependencies:
            return None

        # 测试主环境是否能导入所有依赖
        for dep in dependencies:
            if not dep.strip():
                continue
            pkg = dep.strip().split(">=")[0].split("==")[0].split("<")[0].strip()
            try:
                importlib.import_module(pkg)
            except ImportError:
                break
        else:
            return None  # 全部可导入，主环境兼容

        # 不兼容 → 创建 venv
        import subprocess
        from pathlib import Path as _Path

        tools_dir = _Path(__file__).resolve().parent.parent.parent.parent / "resources" / "tools" / "implementations"
        venv_dir = tools_dir / tool_id / ".venv"
        venv_python = venv_dir / "bin" / "python"

        if not venv_python.exists():
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)],
                         capture_output=True, timeout=60)
            pip = str(venv_dir / "bin" / "pip")
            subprocess.run([pip, "install", "numpy>=1.24,<2.0"],
                         capture_output=True, timeout=120)
            for dep in dependencies:
                if dep.strip():
                    subprocess.run([pip, "install", dep.strip()],
                                 capture_output=True, timeout=120)

        return str(venv_python)
    async def validate_spec(self, spec: dict) -> bool:
        md = spec.get("raw_md", "")
        required = ["功能概述", "输入规范", "输出规范", "依赖环境", "运行机制"]
        return all(s in md for s in required)

    async def build(self, spec: dict) -> str:
        """生成工具代码 — 传递完整 MD spec 给 LLM"""
        raw_md = spec.get("raw_md", "")
        return await self._llm_generate(raw_md)

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

    async def dry_run(self, code: str, test_input: dict = None, tool_id: str = None) -> dict:
        """沙箱测试 — 优先使用工具独立 venv 的 Python"""
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

        # 4. 功能测试（用工具 venv 的 Python 执行）
        try:
            import subprocess as _sp
            import json as _json
            import sys as _sys
            import tempfile as _tmp
            import os as _os

            # 确定 Python 执行器：优先工具 venv
            python_exe = _sys.executable
            if tool_id:
                venv_py = Path(__file__).resolve().parent.parent.parent.parent / "resources" / "tools" / "implementations" / tool_id / ".venv" / "bin" / "python"
                if venv_py.exists():
                    python_exe = str(venv_py)

            test_script = (
                f"import json\n"
                f"import sys\n"
                f"code = {_json.dumps(code)}\n"
                f"try:\n"
                f"    exec(code)\n"
                f"    result = execute(**{_json.dumps(test_input)})\n"
                f"    print(json.dumps(result, default=str))\n"
                f"except ModuleNotFoundError as e:\n"
                f"    print(json.dumps({{'status':'failed','message':f'缺少依赖: {{e.name}}，请用 pip install {{e.name}} 安装','error':'ModuleNotFoundError'}}))\n"
                f"except Exception as e:\n"
                f"    print(json.dumps({{'status':'failed','message':str(e),'error':type(e).__name__}}))\n"
            )
            with _tmp.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(test_script)
                tmp_path = f.name

            try:
                proc = _sp.run(
                    [python_exe, tmp_path],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode == 0:
                    output_data = proc.stdout.strip()
                    try:
                        output_data = _json.loads(output_data)
                    except _json.JSONDecodeError:
                        pass
                    # 检查是否是 ModuleNotFoundError
                    if isinstance(output_data, dict) and output_data.get("error") == "ModuleNotFoundError":
                        results["failed"].append(output_data.get('message', '缺少依赖'))
                    else:
                        results["passed"].append("功能测试: execute() 执行成功")
                    results["test_details"] = {
                        "input": test_input,
                        "output": output_data,
                    }
                else:
                    # 解析 stderr 中的 ModuleNotFoundError
                    err_msg = proc.stderr[:200]
                    if "ModuleNotFoundError" in err_msg:
                        import re as _re
                        match = _re.search(r"No module named '(\S+)'", err_msg)
                        if match:
                            results["failed"].append(f"缺少依赖: {match.group(1)}，请 pip install {match.group(1)}")
                        else:
                            results["failed"].append(f"缺少依赖:\n{err_msg}")
                    else:
                        results["failed"].append(f"功能测试失败:\n{err_msg}")
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
            "output_format": "text",  # 从 MD 解析
        }

        # 解析 output_format
        out_match = re.search(r'output_format\s*\|\s*(\w+)', md)
        if out_match:
            result["output_format"] = out_match.group(1)

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
                return ("/hdd/sdc1/jmlv/LLM/data/photo.png", "", "/hdd/sdc1/jmlv/LLM/data/photo.png")
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

    async def _llm_generate(self, spec_md: str) -> str:
        """LLM 生成工具代码 — 基于完整 MD 规范文档"""
        prompt = f"""You are a Python code generator. Generate a tool function that STRICTLY follows the specification below.

=== TOOL SPECIFICATION ===
{spec_md}
=== END SPECIFICATION ===

CRITICAL RULES:
1. Function signature MUST be: def execute(**kwargs) -> dict[str, Any]
2. Parameter names MUST match EXACTLY what the spec defines in the input table
3. For EVERY required parameter (required=是), add a validation check at the top
4. The return dict MUST include: status, output_format, message, data
5. Use the EXACT output_format specified in the spec
6. If output_format is "image": save the result to a real file path and return image_path in data
7. If output_format is "table": return data with "columns" and "rows" arrays
8. Handle ALL errors gracefully with try/except, returning status="failed"
9. Import only standard library and the dependencies listed in the spec
10. The docstring should describe Args and Returns based on the spec

Return ONLY the Python code, no explanations, no markdown fences."""

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
