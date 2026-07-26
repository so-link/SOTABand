"""Tool 代码生成器 v2 — LLM 生成完整文件 + 沙箱执行 + 自动调试"""

import ast as _ast
import json
import re
import subprocess
import tempfile
import os as _os
from pathlib import Path

from core.resource.builder.builder_base import BaseBuilder
from core.llm.client import create_llm_client


# ================================================================
#   工具代码模板 — LLM 以此为基础生成完整文件
# ================================================================

TOOL_TEMPLATE = '''# === SOTABand 工具标准模板 ===
import os, sys, json, time
from pathlib import Path
from typing import Any
import requests

# ── 项目根路径 ──
_tool_dir = os.environ.get("TOOL_DIR", "")
if _tool_dir:
    _PROJECT_ROOT = Path(_tool_dir).resolve().parent.parent.parent.parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 数据目录 ──
_DATA_DIR = _PROJECT_ROOT / "data"
_DOWNLOADS_DIR = _DATA_DIR / "downloads"

# ── API 调用辅助 ──
def _call_api(api_name: str, **params) -> dict:
    """调用系统 API"""
    from core.api import get_api
    api = get_api(api_name)
    return api.call(**params)

# ── 工具调用辅助 ──
def _call_tool(tool_name: str, **params) -> dict:
    """调用已注册的工具"""
    import subprocess as _sp
    tool_dir = _PROJECT_ROOT / "resources" / "tools" / "implementations" / tool_name
    tool_file = tool_dir / "tool.py"
    if not tool_file.exists():
        return {"status": "failed", "message": f"Tool '{tool_name}' not found"}
    venv_py = tool_dir / ".venv" / "bin" / "python"
    py_exe = str(venv_py) if venv_py.exists() else sys.executable
    script = f"import json, sys; sys.path.insert(0, {str(_PROJECT_ROOT)!r}); exec(open({str(tool_file)!r}).read()); print(json.dumps(execute(**{params!r}), default=str, ensure_ascii=False))"
    proc = _sp.run([py_exe, "-c", script], capture_output=True, text=True, timeout=30)
    try:
        return json.loads(proc.stdout.strip())
    except:
        return {"status": "failed", "message": proc.stderr[:500]}

# ── 文件路径辅助 ──
def _resolve_path(path: str) -> str:
    """将相对/绝对路径转为绝对路径（基于 _PROJECT_ROOT）"""
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(_PROJECT_ROOT / p)

# === 头部结束，以下由 LLM 生成 ===
'''


class ToolCodeBuilder(BaseBuilder):
    """v2: LLM 生成完整文件 + 仅沙箱执行 + 自动调试"""

    def __init__(self, llm_client=None):
        self.llm = llm_client or create_llm_client()

    async def dry_run(self, code: str) -> dict:
        """兼容 BaseBuilder 抽象方法"""
        return await self.sandbox_execute(code, {})

    # ── 参数解析 ──

    @staticmethod
    def _parse_spec_inputs(spec_md: str) -> list[dict]:
        """从 MD 的输入规范表格解析参数"""
        inputs = []
        in_section = False
        for line in spec_md.split("\n"):
            if "输入规范" in line:
                in_section = True
                continue
            if in_section and line.startswith("##") and "输入" not in line:
                break
            if in_section and line.startswith("|") and "参数名" not in line and "---" not in line:
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) >= 2 and parts[0]:
                    inputs.append({
                        "name": parts[0], "type": parts[1] if len(parts) > 1 else "string",
                        "required": "是" in parts[2] if len(parts) > 2 else True,
                        "default": parts[3] if len(parts) > 3 and parts[3] not in ("-", "—", "") else None,
                        "desc": parts[4] if len(parts) > 4 else "",
                    })
        return inputs

    async def extract_param_metadata(self, spec_md: str) -> list[dict]:
        """LLM 提取参数元数据"""
        prompt = f"""从以下工具 MD 规范文档中提取输入参数列表，返回 JSON 数组。
每个参数: name, type, required(true/false), default(null或值), desc, hints(示例输入数组)

MD 文档:
{spec_md[:3000]}

仅返回 JSON 数组:"""
        response = await self.llm.chat(messages=[{"role":"user","content":prompt}], temperature=0.1, max_tokens=1000)
        try:
            clean = response.strip()
            if clean.startswith("```"): clean = clean.split("\n",1)[1].rsplit("\n",1)[0]
            return json.loads(clean)
        except:
            return []

    async def validate_spec(self, spec: dict) -> bool:
        md = spec.get("raw_md", "")
        return all(s in md for s in ["功能概述","输入规范","输出规范","依赖环境","运行机制"])

    # ── 代码生成 — LLM 生成完整文件 ──

    async def build(self, spec: dict) -> str:
        """LLM 根据模板 + MD 规范生成完整 Python 文件，不做任何后处理"""
        raw_md = spec.get("raw_md", "")
        return await self._llm_generate(raw_md)

    async def _llm_generate(self, spec_md: str) -> str:
        """LLM 生成完整工具代码"""
        # 解析标记
        api_calls = re.findall(r'(?<!【)【(?!【)(.+?)(?<!】)】(?!】)', spec_md)
        tool_calls = re.findall(r'【【(.+?)】】', spec_md)

        api_info = ""
        tool_info = ""

        api_lines = []

        if api_calls:
            from core.api.registry import ApiRegistry
            reg = ApiRegistry()
            all_apis = reg._read()

            # API MD 定义文件所在目录
            api_def_dir = Path(__file__).resolve().parent.parent.parent / "core" / "api" / "definitions"

            for name in api_calls:
                for api in all_apis:
                    if api.get("name") == name:
                        api_id = api['id']
                        input_schema = api.get("input_schema", {})
                        output_schema = api.get("output_schema", {})

                        # 1. 读取 API 的 MD 定义文件，提取输入/输出参数表
                        spec_path = api.get("spec_path", f"definitions/{api_id}.md")
                        md_file = api_def_dir / (spec_path.split("/")[-1] if "/" in spec_path else spec_path)
                        param_descriptions = {}
                        output_descriptions = {}
                        if md_file.exists():
                            md_content = md_file.read_text()
                            # 解析输入参数
                            in_input = False
                            in_output = False
                            for line in md_content.split("\n"):
                                if "输入规范" in line:
                                    in_input = True; in_output = False
                                    continue
                                if "输出规范" in line:
                                    in_output = True; in_input = False
                                    continue
                                if (in_input or in_output) and line.startswith("##"):
                                    in_input = False; in_output = False
                                    continue
                                if line.startswith("|") and "参数名" not in line and "字段" not in line and "---" not in line:
                                    parts = [p.strip() for p in line.split("|")[1:-1]]
                                    if len(parts) >= 2 and parts[0]:
                                        name = parts[0]
                                        typ = parts[1] if len(parts) > 1 else "string"
                                        desc = parts[3] if len(parts) > 3 else (parts[2] if len(parts) > 2 else "")
                                        if in_input:
                                            required = parts[2] if len(parts) > 2 else ""
                                            param_descriptions[name] = {"type": typ, "required": "是" in required, "description": desc}
                                        elif in_output:
                                            output_descriptions[name] = {"type": typ, "description": desc}

                        # 2. 构造详细的输入参数信息
                        api_lines.append(f"  API: {name} (ID: {api_id})")

                        if input_schema:
                            params_detail = []
                            for k, v in input_schema.items():
                                md_info = param_descriptions.get(k, {})
                                desc = md_info.get("description", v)
                                req = "必填" if md_info.get("required", True) else "可选"
                                params_detail.append(f"      {k} ({v}, {req}): {desc}")
                            params_example = ", ".join(f'{k}=<{k}>' for k in input_schema.keys())
                            api_lines.append(f"    调用: _call_api(\"{api_id}\", {params_example})")
                            api_lines.append(f"    输入参数:")
                            api_lines.extend(params_detail)
                        else:
                            api_lines.append(f"    调用: _call_api(\"{api_id}\")")
                            api_lines.append(f"    输入参数: 无")

                        # 3. 构造输出格式说明
                        if output_schema:
                            output_detail = []
                            for k, v in output_schema.items():
                                md_info = output_descriptions.get(k, {})
                                desc = md_info.get("description", v)
                                output_detail.append(f"      {k} ({v}): {desc}")
                            api_lines.append(f"    返回值 (dict):")
                            api_lines.extend(output_detail)
                        else:
                            api_lines.append(f"    返回值: 无特定格式")

                        api_lines.append(f"    使用方式: result = _call_api(\"{api_id}\", ...); # 然后从 result 中按字段名取值")

            if api_lines:
                api_info = "\n=== SYSTEM API CALLS ===\n\n" + "\n".join(api_lines) + "\n\n=== END API CALLS ===\n"

        if tool_calls:
            tool_lines = [f"  {name} → _call_tool(\"{name}\", ...)" for name in tool_calls]
            tool_info = "\n=== TOOL CALLS ===\n" + "\n".join(tool_lines) + "\n=== END TOOL CALLS ===\n"

        prompt = f"""You are a Python code generator for SOTABand tools. Generate a COMPLETE, RUNNABLE Python file.

=== TEMPLATE (use as starting point) ===
{TOOL_TEMPLATE}
=== END TEMPLATE ===

=== TOOL SPECIFICATION ===
{spec_md}
=== END SPECIFICATION ===

{api_info}
{tool_info}

CRITICAL RULES:
1. Output the COMPLETE file including template header — all imports and helpers
2. Function: def execute(**kwargs) -> dict[str, Any]
3. Access params: kwargs.get("param_name", default) — NEVER kwargs["param_name"]
4. Return: {{"status":"success"|"failed","output_format":"text"|"image"|"table"|"file","message":"...","data":{{}}}}
5. All errors: try/except, return {{"status":"failed","message":str(e)}}
6. File paths: _PROJECT_ROOT / "data" / ... or _resolve_path()
7. API calls: use the EXACT api_id and param names from the SYSTEM API CALLS section above
8. Map tool input parameters (from kwargs) to API parameters with the CORRECT names
9. Tool calls: _call_tool("tool-name", param=value)
10. NEVER async/await
11. Output ONLY Python code, no markdown, no explanation"""

        response = await self.llm.chat(
            messages=[{"role":"user","content":prompt}],
            temperature=0.3, max_tokens=100000, timeout=300,
        )
        code = response
        if "```python" in code: code = code.split("```python")[1].split("```")[0]
        elif "```" in code: code = code.split("```")[1].split("```")[0]
        return code.strip()

    # ── 沙箱执行 — 统一走 ToolExecutor，与对话界面完全一致 ──

    @staticmethod
    def _extract_imports(code: str) -> list[str]:
        """从代码中提取第三方依赖包名"""
        try:
            tree = _ast.parse(code)
        except SyntaxError:
            return []
        deps = set()
        # 已知标准库前缀
        stdlib_prefixes = {
            'abc', 'argparse', 'array', 'ast', 'asyncio', 'base64', 'bisect',
            'builtins', 'bz2', 'calendar', 'cgi', 'cmath', 'codecs', 'collections',
            'concurrent', 'configparser', 'contextlib', 'copy', 'csv', 'ctypes',
            'curses', 'dataclasses', 'datetime', 'dbm', 'decimal', 'difflib',
            'dis', 'distutils', 'email', 'encodings', 'enum', 'errno', 'faulthandler',
            'fnmatch', 'fractions', 'ftplib', 'functools', 'gc', 'getopt', 'gettext',
            'glob', 'graphlib', 'grp', 'gzip', 'hashlib', 'heapq', 'hmac', 'html',
            'http', 'idlelib', 'imaplib', 'importlib', 'inspect', 'io', 'ipaddress',
            'itertools', 'json', 'keyword', 'linecache', 'locale', 'logging', 'lzma',
            'mailbox', 'marshal', 'math', 'mimetypes', 'mmap', 'multiprocessing',
            'netrc', 'numbers', 'operator', 'optparse', 'os', 'pathlib', 'pdb',
            'pickle', 'pipes', 'pkgutil', 'platform', 'plistlib', 'pprint', 'profile',
            'pstats', 'pty', 'pwd', 'py_compile', 'pydoc', 'queue', 'quopri',
            'random', 're', 'readline', 'reprlib', 'resource', 'rlcompleter',
            'runpy', 'sched', 'secrets', 'select', 'selectors', 'shelve', 'shlex',
            'shutil', 'signal', 'site', 'socket', 'socketserver', 'sqlite3', 'ssl',
            'stat', 'statistics', 'string', 'struct', 'subprocess', 'sunau', 'sys',
            'sysconfig', 'tabnanny', 'tarfile', 'telnetlib', 'tempfile', 'termios',
            'textwrap', 'threading', 'time', 'timeit', 'tkinter', 'token', 'tokenize',
            'tomllib', 'trace', 'traceback', 'tracemalloc', 'tty', 'turtle',
            'types', 'typing', 'unicodedata', 'unittest', 'urllib', 'uu', 'uuid',
            'venv', 'warnings', 'wave', 'weakref', 'webbrowser', 'xml', 'xmlrpc',
            'zipapp', 'zipfile', 'zipimport', 'zlib', '_thread', '_io',
            '__future__',
        }
        project_prefixes = {'app', 'core', 'resources', 'storage', 'config'}
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    top = alias.name.split('.')[0]
                    if top not in stdlib_prefixes and top not in project_prefixes:
                        deps.add(top)
            elif isinstance(node, _ast.ImportFrom):
                if node.module:
                    top = node.module.split('.')[0]
                    if top not in stdlib_prefixes and top not in project_prefixes:
                        deps.add(top)
        return sorted(deps)

    async def _ensure_deps(self, code: str, tool_id: str) -> dict:
        """自动检测并安装代码中的第三方依赖"""
        deps = self._extract_imports(code)
        if not deps:
            return {"installed": [], "failed": [], "message": "无第三方依赖"}
        return await self.install_deps(tool_id, deps)

    async def sandbox_execute(self, code: str, test_input: dict, tool_id: str = None) -> dict:
        """沙箱执行：自动检测依赖 → 安装 → 通过 ToolExecutor 统一执行。
        返回格式兼容 auto_debug_stream 的旧调用方。
        """
        tid = tool_id or "sandbox"
        
        # 执行前自动检测并安装缺失依赖
        await self._ensure_deps(code, tid)
        
        from core.executor.tool_executor import ToolExecutor

        result = await ToolExecutor.execute(
            tool_id=tid,
            params=test_input,
            code=code,
            timeout=None,  # 自动调试无超时限制
        )
        return {
            "exit_code": 0 if result.get("status") == "success" else 1,
            "stdout": json.dumps(result, ensure_ascii=False),
            "stderr": result.get("stderr", ""),
            "success": result.get("status") == "success",
        }

    # ── 依赖安装 ──

    async def install_deps(self, tool_id: str, dependencies: list[str]) -> dict:
        """安装依赖（非流式，供 sandbox_execute 等调用）"""
        result = {"venv_path": "", "python": "", "results": []}
        async for event in self.install_deps_stream(tool_id, dependencies):
            if event["event"] == "deps_done":
                result = event["data"]
        return result

    async def install_deps_stream(self, tool_id: str, dependencies: list[str]):
        """流式安装依赖：实时 yield 安装进度事件。
        
        策略：
        1. 先尝试安装到全局 venv（当前 Python 环境）
        2. 如果全局失败（版本冲突等），后续依赖全部装到工具本地 .venv
        
        事件类型：
        - deps_start: 开始安装，{deps: [...], env: "global"|"local"}
        - dep_installing: 正在安装某个包，{dep: str, env: str}
        - dep_installed: 安装成功，{dep: str, env: str}
        - dep_failed: 安装失败，{dep: str, env: str, reason: str}
        - env_switch: 从全局切换到本地 venv
        - deps_done: 全部完成，{data: {...}}
        """
        import sys as _sys

        global_pip = str(Path(_sys.executable).parent / "pip")
        results = []
        use_local = False

        yield {"event": "deps_start", "deps": list(dependencies), "env": "global"}

        for dep in dependencies:
            dep = dep.strip()
            if not dep: continue

            if not use_local:
                yield {"event": "dep_installing", "dep": dep, "env": "global"}
                proc = subprocess.run(
                    [global_pip, "install", dep],
                    capture_output=True, text=True, timeout=300,
                )
                if proc.returncode == 0:
                    results.append({"dep": dep, "success": True, "env": "global"})
                    yield {"event": "dep_installed", "dep": dep, "env": "global"}
                    continue
                # 全局安装失败
                use_local = True
                yield {"event": "env_switch", "dep": dep, "reason": proc.stderr[-200:]}

            # 回退到工具本地 .venv
            tools_dir = Path(__file__).resolve().parent.parent.parent.parent / "resources" / "tools" / "implementations"
            tool_dir = tools_dir / tool_id
            tool_dir.mkdir(parents=True, exist_ok=True)
            venv_dir = tool_dir / ".venv"
            venv_python = venv_dir / "bin" / "python"

            if not venv_python.exists():
                subprocess.run([_sys.executable, "-m", "venv", str(venv_dir)], capture_output=True, timeout=60)

            local_pip = str(venv_dir / "bin" / "pip")
            yield {"event": "dep_installing", "dep": dep, "env": "local"}
            proc = subprocess.run(
                [local_pip, "install", dep],
                capture_output=True, text=True, timeout=300,
            )
            if proc.returncode == 0:
                results.append({"dep": dep, "success": True, "env": "local"})
                yield {"event": "dep_installed", "dep": dep, "env": "local"}
            else:
                results.append({"dep": dep, "success": False, "env": "local", "reason": proc.stderr[-200:]})
                yield {"event": "dep_failed", "dep": dep, "env": "local", "reason": proc.stderr[-200:]}

        data = {"venv_path": str(Path(_sys.executable).parent.parent), "python": _sys.executable, "results": results}
        yield {"event": "deps_done", "data": data}

    # ── 自动调试 ──

    async def auto_debug_stream(self, spec_md: str, code: str, test_input: dict, tool_id: str, max_rounds: int = 50, stop_event=None):
        """自动调试生成器：执行→LLM分析→改代码→再执行。
        stop_event: asyncio.Event，设置时中断调试。
        CancelledError: producer_task.cancel() 时抛出，立即退出。
        """
        import asyncio

        current_code = code

        try:
            async for event in self._auto_debug_loop(
                spec_md, current_code, test_input, tool_id, max_rounds, stop_event
            ):
                yield event
        except asyncio.CancelledError:
            if stop_event:
                stop_event.set()
            yield {"event": "stopped", "round": 0, "message": "调试已停止"}
            # 不 raise，让 _producer 正常结束

    async def _auto_debug_loop(self, spec_md: str, code: str, test_input: dict, tool_id: str, max_rounds: int, stop_event):
        """自动调试主循环"""
        import asyncio

        current_code = code

        # 调试开始：先检测并安装依赖
        deps = self._extract_imports(current_code)
        if deps:
            async for dep_event in self.install_deps_stream(tool_id, deps):
                if stop_event and stop_event.is_set():
                    yield {"event": "stopped", "round": 0, "message": "用户手动停止调试"}
                    return
                yield dep_event

        for round_num in range(1, max_rounds + 1):
            # 检查停止信号
            if stop_event and stop_event.is_set():
                yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                return

            # 1. 执行（带取消支持：stop_event 设置时立即终止子进程）
            exec_task = asyncio.create_task(
                self.sandbox_execute(current_code, test_input, tool_id)
            )
            exec_done = False
            exec_result = None
            while not exec_done:
                if stop_event and stop_event.is_set():
                    exec_task.cancel()
                    try:
                        await asyncio.wait_for(exec_task, timeout=2.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
                    yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试（执行已中断）"}
                    return
                try:
                    exec_result = await asyncio.wait_for(asyncio.shield(exec_task), timeout=0.1)
                    exec_done = True
                except asyncio.TimeoutError:
                    continue

            # 检查停止信号
            if stop_event and stop_event.is_set():
                yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                return

            # 2. 解析输出判断成功/失败
            success = False
            output_data = None
            stdout = exec_result.get("stdout", "")
            if stdout:
                lines = stdout.strip().split('\n')
                for line in reversed(lines):
                    line = line.strip()
                    if line.startswith('{') and line.endswith('}'):
                        try:
                            output_data = json.loads(line)
                            break
                        except:
                            continue
                if output_data is None:
                    try:
                        output_data = json.loads(stdout)
                    except:
                        pass
                if isinstance(output_data, dict) and output_data.get("status") == "success":
                    success = True

            yield {"event": "round_start", "round": round_num, "max": max_rounds}
            yield {"event": "exec_result", "round": round_num, "stdout": exec_result["stdout"], "stderr": exec_result["stderr"], "success": success}

            if success:
                yield {"event": "done", "round": round_num, "success": True, "code": current_code, "message": f"调试成功 (第{round_num}轮)"}
                return

            # 3. 检查缺失依赖
            if output_data and output_data.get("error") == "ModuleNotFoundError":
                missing = output_data.get("missing_module", "")
                yield {"event": "missing_dep", "round": round_num, "module": missing}
                if missing:
                    await self.install_deps(tool_id, [missing])
                    yield {"event": "dep_installed", "round": round_num, "module": missing}
                    continue

            # 4. LLM 分析并修复
            if round_num >= max_rounds:
                break

            # 检查停止信号
            if stop_event and stop_event.is_set():
                yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                return

            yield {"event": "thinking", "round": round_num, "message": "LLM 分析错误..."}

            fix_prompt = f"""Debug this tool code. It failed execution.

=== CURRENT CODE ===
{current_code}
=== END CODE ===

=== TEST INPUT ===
{json.dumps(test_input, ensure_ascii=False, indent=2)}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {exec_result['stdout'][:2000]}
stderr: {exec_result['stderr'][:1000]}
=== END RESULT ===

Fix the code. Output the COMPLETE fixed Python file (including template header).
INTERFACE RULES: execute(**kwargs)->dict, kwargs.get, {{status,output_format,message,data}}, try/except.
Output ONLY Python code."""

            full_response = ""
            llm_task = None
            try:
                token_queue: list = []

                async def _collect_tokens():
                    async for token in self.llm.chat_stream(
                        messages=[{"role":"user","content":fix_prompt}],
                        temperature=0.2, max_tokens=100000,
                    ):
                        token_queue.append(token)

                llm_task = asyncio.create_task(_collect_tokens())

                while not llm_task.done():
                    if stop_event and stop_event.is_set():
                        llm_task.cancel()
                        try: await llm_task
                        except asyncio.CancelledError: pass
                        yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试（LLM对话已中断）"}
                        return
                    while token_queue:
                        token = token_queue.pop(0)
                        full_response += token
                        yield {"event": "thinking_stream", "round": round_num, "token": token}
                    await asyncio.sleep(0.05)

                # LLM 完成，消费剩余
                while token_queue:
                    token = token_queue.pop(0)
                    full_response += token
                    yield {"event": "thinking_stream", "round": round_num, "token": token}

            except asyncio.CancelledError:
                # producer_task.cancel() 传播的 CancelledError — 立即退出
                if llm_task and not llm_task.done():
                    llm_task.cancel()
                    try: await llm_task
                    except asyncio.CancelledError: pass
                raise  # 继续向上传播
            except Exception as e:
                yield {"event": "llm_error", "round": round_num, "message": f"LLM调用异常: {str(e)[:200]}"}
                continue

            new_code = full_response.strip()
            if "```python" in new_code: new_code = new_code.split("```python")[1].split("```")[0]
            elif "```" in new_code: new_code = new_code.split("```")[1].split("```")[0]
            else: new_code = new_code

            current_code = new_code
            yield {"event": "code_updated", "round": round_num, "code": current_code}

            # 4. LLM 修改代码后，自动检测新增依赖并流式安装
            new_deps = self._extract_imports(current_code)
            if new_deps:
                async for dep_event in self.install_deps_stream(tool_id, new_deps):
                    if stop_event and stop_event.is_set():
                        yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                        return
                    yield dep_event

        yield {"event": "done", "round": max_rounds, "success": False, "code": current_code, "message": f"达到最大轮数 {max_rounds}，调试未完成"}
