"""Tool 代码生成器 v2 — LLM 生成完整文件 + 沙箱执行 + 自动调试"""

import ast as _ast
import json
import re
import subprocess
import tempfile
import os as _os
import threading
from pathlib import Path
from datetime import datetime

from core.resource.builder.builder_base import BaseBuilder

# ── 全局调试状态：线程安全的停止标志 ──
# key=tool_id, value=True 表示正在运行
_debug_states: dict[str, bool] = {}
_debug_lock = threading.Lock()

def set_debug_running(tool_id: str):
    """设置指定工具的调试为运行状态"""
    with _debug_lock:
        _debug_states[tool_id] = True

def set_debug_stopped(tool_id: str):
    """设置指定工具的调试为停止状态"""
    with _debug_lock:
        _debug_states.pop(tool_id, None)

def is_debug_running(tool_id: str) -> bool:
    """检查指定工具的调试是否在运行"""
    with _debug_lock:
        return _debug_states.get(tool_id, False)

def stop_debug(tool_id: str):
    """请求停止指定工具的调试"""
    with _debug_lock:
        if tool_id in _debug_states:
            _debug_states[tool_id] = False
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
11. NEVER use pip install, subprocess.run for package installation, or any runtime dependency installation — dependencies are managed by the system automatically
12. Output ONLY Python code, no markdown, no explanation"""

        response = await self.llm.chat(
            messages=[{"role":"user","content":prompt}],
            temperature=0.3, max_tokens=100000, timeout=300,
        )
        code = response.strip()
        # 清理开头的 markdown 代码块标记
        if code.startswith("```python"):
            code = code[len("```python"):]
        elif code.startswith("```"):
            code = code[3:]
        # 清理末尾的 markdown 代码块标记
        if code.endswith("```"):
            code = code[:-3]
        # 移除首尾空白行
        code = code.strip()
        # 如果清理后以 ``` 开头或结尾（说明中间也有），再尝试整体提取
        # 但只在确认是纯代码块包裹时才做 split
        if code.startswith("```") and code.count("```") == 2:
            parts = code.split("```")
            code = parts[1].strip() if len(parts) >= 2 else code
        return code

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

    async def sandbox_execute(self, code: str, test_input: dict, tool_id: str = None, exec_timeout: float = 180.0) -> dict:
        """沙箱执行：通过 ToolExecutor 统一执行。
        注意：依赖安装由 _auto_debug_loop 统一管理（带日志），此处不再重复安装。
        """
        import asyncio

        tid = tool_id or "sandbox"
        
        from core.executor.tool_executor import ToolExecutor

        try:
            result = await asyncio.wait_for(
                ToolExecutor.execute(
                    tool_id=tid,
                    params=test_input,
                    code=code,
                    timeout=exec_timeout,
                ),
                timeout=exec_timeout + 10,
            )
        except asyncio.TimeoutError:
            result = {"status": "failed", "message": f"工具执行超时 ({exec_timeout}秒)", "error": "TimeoutError"}

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

    def _get_exec_python(self, tool_id: str) -> str:
        """获取工具执行时实际使用的 Python（与 ToolExecutor._get_python_exe 一致）"""
        from core.executor.tool_executor import ToolExecutor
        return ToolExecutor._get_python_exe(tool_id)

    def _get_exec_pip(self, tool_id: str) -> str:
        """获取 pip 安装命令。
        
        工具独立 .venv → 用 .venv/bin/pip（如果可用）
        项目 venv → 用 python -m pip（避免 shebang 空格路径问题）
        """
        python_exe = self._get_exec_python(tool_id)
        py_dir = Path(python_exe).parent
        is_tool_venv = "resources/tools/implementations" in str(py_dir)

        if is_tool_venv:
            pip = str(py_dir / "pip")
            if Path(pip).exists():
                return pip
            pip3 = str(py_dir / "pip3")
            if Path(pip3).exists():
                return pip3

        # 项目 venv 或回退：统一用 python -m pip（安全可靠）
        return f"{python_exe} -m pip"

    def _get_install_target(self) -> str:
        """获取 pip install --target 的目标目录：项目 venv 的 site-packages"""
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        site_packages = project_root / "venv" / "lib" / "python3.12" / "site-packages"
        if site_packages.exists():
            return str(site_packages)
        # 尝试其他 Python 版本
        venv_lib = project_root / "venv" / "lib"
        if venv_lib.exists():
            for d in sorted(venv_lib.iterdir(), reverse=True):
                sp = d / "site-packages"
                if sp.exists():
                    return str(sp)
        return ""

    async def install_deps_stream(self, tool_id: str, dependencies: list[str], stop_event=None):
        """流式安装依赖：实时输出 pip 日志，失败时用 LLM 分析原因并调整命令重试。
        
        使用 asyncio.to_thread + subprocess.Popen + Queue 实现：
        - 安装在线程中执行，不阻塞事件循环
        - pip 输出逐行通过 queue 推回异步世界
        - 失败时自动调用 LLM 分析并重试
        - 支持 stop_event 和全局调试标志双重停止
        
        安装目标：
        - 工具独立 .venv → 安装到工具自己的 .venv
        - 否则 → 安装到项目 venv 的 site-packages
        """
        import asyncio, threading

        exec_python = self._get_exec_python(tool_id)
        exec_pip = self._get_exec_pip(tool_id)
        is_local = ".venv" in exec_python
        env_label = "local" if is_local else "project_venv"

        # 如果不是工具独立 venv，安装到项目 venv 的 site-packages
        install_target = "" if is_local else self._get_install_target()

        yield {"event": "deps_start", "deps": list(dependencies), "env": env_label}

        def _is_installed(python_exe: str, module_name: str) -> bool:
            proc = subprocess.run(
                [python_exe, "-c", f"import {module_name}"],
                capture_output=True, text=True, timeout=10,
            )
            return proc.returncode == 0

        def _pip_worker(pip_cmd: str, dep: str, target: str, output_queue: asyncio.Queue):
            """在线程中执行 pip install，逐行推入 queue"""
            parts = pip_cmd.split() + ["install", dep]
            if target:
                parts += ["--target", target]
            try:
                proc = subprocess.Popen(
                    parts,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        output_queue.put_nowait(("line", line))
                proc.wait()
                output_queue.put_nowait(("done", proc.returncode))
            except Exception as e:
                output_queue.put_nowait(("error", str(e)))

        results = []
        for dep in dependencies:
            dep = dep.strip()
            if not dep: continue

            module_name = dep.split("==")[0].split(">=")[0].split("<=")[0].strip()

            if _is_installed(exec_python, module_name):
                yield {"event": "dep_already", "dep": dep, "env": env_label}
                results.append({"dep": dep, "success": True, "env": env_label, "already": True})
                continue

            yield {"event": "dep_installing", "dep": dep, "env": env_label}

            # 启动安装线程
            current_dep = dep
            max_retries = 2
            for attempt in range(max_retries + 1):
                q = asyncio.Queue()
                all_output = ""
                thread = threading.Thread(target=_pip_worker, args=(exec_pip, current_dep, install_target, q), daemon=True)
                thread.start()

                # 从 queue 读取输出，短超时轮询 + 停止检查
                retcode = None
                pip_start = asyncio.get_event_loop().time()
                PIP_TOTAL_TIMEOUT = 300  # pip 总超时 5 分钟
                while True:
                    # 检查停止信号
                    if not is_debug_running(tool_id):
                        all_output += "\n[已停止] 调试已停止\n"
                        retcode = -1
                        break
                    if stop_event and stop_event.is_set():
                        all_output += "\n[已停止] 用户手动停止\n"
                        retcode = -1
                        break
                    # 总超时
                    if asyncio.get_event_loop().time() - pip_start > PIP_TOTAL_TIMEOUT:
                        all_output += f"\n[超时] 安装超过 {PIP_TOTAL_TIMEOUT} 秒\n"
                        retcode = -1
                        break

                    try:
                        kind, data = await asyncio.wait_for(q.get(), timeout=0.5)
                        if kind == "line":
                            all_output += data + "\n"
                            yield {"event": "pip_output", "dep": current_dep, "line": data}
                        elif kind == "done":
                            retcode = data
                            break
                        elif kind == "error":
                            all_output += f"\n[错误] {data}\n"
                            retcode = -1
                            break
                    except asyncio.TimeoutError:
                        # 0.5 秒超时是正常的，继续循环检查停止信号
                        continue

                thread.join(timeout=5)
                if retcode == 0:
                    yield {"event": "pip_result", "ok": True, "output": all_output[-1000:]}
                    break

                if attempt >= max_retries:
                    yield {"event": "pip_result", "ok": False, "output": all_output[-1000:]}
                    break

                # 安装失败，LLM 分析
                yield {"event": "pip_analyzing", "dep": current_dep, "attempt": attempt + 1}
                analysis = await self._llm_analyze_pip_error(current_dep, all_output[-2000:])
                yield {"event": "pip_analysis", "dep": current_dep, "analysis": analysis}
                if analysis.get("adjusted_command"):
                    current_dep = analysis["adjusted_command"]
                else:
                    yield {"event": "pip_result", "ok": False, "output": all_output[-1000:]}
                    break

            # 汇总结果
            if retcode == 0:
                results.append({"dep": dep, "success": True, "env": env_label})
                yield {"event": "dep_installed", "dep": dep, "env": env_label}
            else:
                results.append({"dep": dep, "success": False, "env": env_label, "reason": all_output[-300:]})
                yield {"event": "dep_failed", "dep": dep, "env": env_label, "reason": all_output[-300:]}

        data = {"python": exec_python, "results": results}
        yield {"event": "deps_done", "data": data}

    async def _llm_analyze_pip_error(self, dep: str, output: str) -> dict:
        """用 LLM 分析 pip 安装失败原因，返回调整建议"""
        prompt = f"""pip install {dep} 安装失败，以下是完整输出：

{output}

请分析失败原因，给出修复方案。返回 JSON 格式：
{{
  "reason": "失败原因（简短）",
  "suggestion": "修复建议",
  "adjusted_command": "调整后的安装命令（如 pip install xxx --no-deps 或其他参数），如果无需调整则为空字符串"
}}

只返回 JSON，不要其他内容。"""
        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=500, timeout=30,
            )
            # 提取 JSON
            text = response if isinstance(response, str) else response.get("content", "")
            import re as _re
            match = _re.search(r'\{[^{}]*\}', text, _re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception:
            pass
        return {"reason": "未知错误", "suggestion": "", "adjusted_command": ""}

    # ── 调试日志写入 ──

    async def _write_debug_log(self, tool_id: str, timestamp: str, entries: list[dict], success: bool, final_round: int):
        """将调试日志写入 logs/ 目录下的时间戳文件"""
        import asyncio

        def _write():
            try:
                project_root = Path(__file__).resolve().parent.parent.parent.parent
                log_dir = project_root / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_file = log_dir / f"{tool_id}_{timestamp}.md"

                lines = []
                lines.append(f"# 自动调试日志\n")
                lines.append(f"- **工具**: {tool_id}")
                lines.append(f"- **时间**: {timestamp}")
                lines.append(f"- **结果**: {'成功' if success else '失败'}（共 {final_round} 轮）")
                lines.append(f"- **日志条目**: {len(entries)} 轮\n")
                lines.append("---\n")

                for i, entry in enumerate(entries):
                    r = entry["round"]
                    lines.append(f"## 第 {r} 轮\n")
                    lines.append(f"### 执行结果\n")
                    lines.append(f"```\n{entry['exec_result']}\n```\n")
                    if entry.get("dep_feedback"):
                        lines.append(f"### 依赖反馈\n")
                        lines.append(f"{entry['dep_feedback']}\n")
                    lines.append(f"### 发送给 LLM 的 Prompt\n")
                    lines.append(f"```\n{entry['prompt']}\n```\n")
                    lines.append(f"### LLM 返回\n")
                    lines.append(f"```\n{entry['response']}\n```\n")
                    if i < len(entries) - 1:
                        lines.append("======================\n")

                log_file.write_text("\n".join(lines), encoding="utf-8")
            except Exception:
                pass  # 日志写入失败不阻塞调试流程

        await asyncio.to_thread(_write)

    # ── 自动调试 ──

    async def auto_debug_stream(self, spec_md: str, code: str, test_input: dict, tool_id: str, max_rounds: int = 50, stop_event=None):
        """自动调试生成器：执行→LLM分析→改代码→再执行。
        stop_event: asyncio.Event，设置时中断调试。
        CancelledError: producer_task.cancel() 时抛出，立即退出。
        """
        import asyncio

        current_code = code
        set_debug_running(tool_id)

        try:
            try:
                async for event in self._auto_debug_loop(
                    spec_md, current_code, test_input, tool_id, max_rounds, stop_event
                ):
                    yield event
            except asyncio.CancelledError:
                if stop_event:
                    stop_event.set()
                yield {"event": "stopped", "round": 0, "message": "调试已停止"}
        finally:
            set_debug_stopped(tool_id)

    async def _auto_debug_loop(self, spec_md: str, code: str, test_input: dict, tool_id: str, max_rounds: int, stop_event):
        """自动调试主循环
        
        流程: 执行 → 系统自动处理依赖安装 → LLM 根据结果修复代码
        
        依赖处理由系统自动完成，LLM 不需要关心安装指令：
        - 循环开始前: AST 提取 import → 安装
        - 执行报 ModuleNotFoundError: 自动安装缺失模块
        - LLM 只负责: 根据执行结果 + 安装反馈 → 决定修复代码还是换方案
        """
        import asyncio

        current_code = code
        # 记录安装失败的依赖（用于告诉 LLM 哪些依赖不可用）
        failed_deps: set = set()
        # 调试日志记录
        debug_log_entries: list[dict] = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 调试开始：立即通知前端
        yield {"event": "debug_start", "message": "自动调试启动", "max_rounds": max_rounds}

        # 初始依赖安装（系统自动，LLM 不参与）
        deps = self._extract_imports(current_code)
        if deps:
            async for dep_event in self.install_deps_stream(tool_id, deps, stop_event=stop_event):
                if stop_event and stop_event.is_set():
                    yield {"event": "stopped", "round": 0, "message": "用户手动停止调试"}
                    return
                yield dep_event

        for round_num in range(1, max_rounds + 1):
            # ── 每轮开始前检查停止标志（全局标志 + stop_event 双重保障）──
            if not is_debug_running(tool_id):
                yield {"event": "stopped", "round": round_num, "message": "调试已停止"}
                return
            if stop_event and stop_event.is_set():
                yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                return

            yield {"event": "round_start", "round": round_num, "max": max_rounds}

            # ── ① 执行 ──
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

            if stop_event and stop_event.is_set():
                yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                return

            # ── ② 解析输出 ──
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

            yield {"event": "exec_result", "round": round_num, "stdout": exec_result["stdout"], "stderr": exec_result["stderr"], "success": success}

            if success:
                yield {"event": "done", "round": round_num, "success": True, "code": current_code, "message": f"调试成功 (第{round_num}轮)"}
                await self._write_debug_log(tool_id, timestamp, debug_log_entries, success=True, final_round=round_num)
                return

            # ── ③ 系统自动处理依赖（LLM 不参与）──
            dep_feedback = ""
            if output_data and output_data.get("error") == "ModuleNotFoundError":
                missing = output_data.get("missing_module", "")
                if missing:
                    install_ok = False
                    install_reason = ""
                    async for dep_event in self.install_deps_stream(tool_id, [missing], stop_event=stop_event):
                        if stop_event and stop_event.is_set():
                            yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                            return
                        yield dep_event
                        if dep_event.get("event") == "dep_installed":
                            install_ok = True
                        elif dep_event.get("event") == "dep_failed":
                            install_ok = False
                            install_reason = dep_event.get("reason", "")
                        elif dep_event.get("event") == "pip_analysis":
                            a = dep_event.get("analysis", {})
                            if a:
                                install_reason += f" [分析: {a.get('reason', '')}]"

                    if install_ok:
                        dep_feedback = f"\n[系统已自动安装 {missing}，请保留现有 import，修复其他代码问题]"
                    else:
                        failed_deps.add(missing)
                        dep_feedback = f"\n[依赖 {missing} 安装失败: {install_reason}。该依赖不可用，请换替代方案]"

            # ── ④ LLM 分析并修复代码 ──
            if round_num >= max_rounds:
                break

            if not is_debug_running(tool_id):
                yield {"event": "stopped", "round": round_num, "message": "调试已停止"}
                return
            if stop_event and stop_event.is_set():
                yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                return

            yield {"event": "thinking", "round": round_num, "message": "LLM 分析错误..."}

            # ── 构建本轮执行摘要（用于日志）──
            exec_summary = f"stdout:\n{exec_result['stdout'][:3000]}\n\nstderr:\n{exec_result['stderr'][:2000]}"

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
{dep_feedback}

Fix the code based on the error and the dependency feedback above.
- If a dependency was installed successfully: keep the import, fix other code logic issues.
- If a dependency failed to install: replace it with an alternative library or stdlib approach.
- If the error is a code logic bug (not dependency-related): fix the bug.

Output the COMPLETE fixed Python file (including template header).
INTERFACE RULES: execute(**kwargs)->dict, kwargs.get, {{status,output_format,message,data}}, try/except.
Output ONLY Python code. NO pip install, NO subprocess, NO install directives, NO markdown."""

            # 记录本轮日志条目
            log_entry = {
                "round": round_num,
                "exec_result": exec_summary,
                "dep_feedback": dep_feedback,
                "prompt": fix_prompt,
                "response": "",
            }

            full_response = ""
            llm_task = None
            LLM_TIMEOUT = 120  # LLM 调用总超时（秒）
            try:
                token_queue: list = []

                async def _collect_tokens():
                    async for token in self.llm.chat_stream(
                        messages=[{"role":"user","content":fix_prompt}],
                        temperature=0.2, max_tokens=100000,
                    ):
                        token_queue.append(token)

                llm_task = asyncio.create_task(_collect_tokens())

                start_time = asyncio.get_event_loop().time()
                while not llm_task.done():
                    if stop_event and stop_event.is_set():
                        llm_task.cancel()
                        try: await llm_task
                        except asyncio.CancelledError: pass
                        yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试（LLM对话已中断）"}
                        return
                    # 整体超时检查
                    if asyncio.get_event_loop().time() - start_time > LLM_TIMEOUT:
                        llm_task.cancel()
                        try: await llm_task
                        except asyncio.CancelledError: pass
                        yield {"event": "llm_error", "round": round_num, "message": f"LLM调用超时（{LLM_TIMEOUT}秒），跳过本轮"}
                        break
                    while token_queue:
                        token = token_queue.pop(0)
                        full_response += token
                        yield {"event": "thinking_stream", "round": round_num, "token": token}
                        # 每消费 50 个 token 检查一次停止信号
                        if len(full_response) % 50 == 0:
                            if not is_debug_running(tool_id):
                                llm_task.cancel()
                                try: await llm_task
                                except asyncio.CancelledError: pass
                                yield {"event": "stopped", "round": round_num, "message": "调试已停止"}
                                return
                    await asyncio.sleep(0.05)

                if llm_task.cancelled():
                    continue

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

            # ── ⑤ 解析 LLM 响应：只接受代码 ──
            log_entry["response"] = full_response
            debug_log_entries.append(log_entry)

            new_code = full_response.strip()
            # 清理 markdown 代码块标记（只处理开头和结尾，避免注释中的 ``` 截断代码）
            if new_code.startswith("```python"):
                new_code = new_code[len("```python"):]
            elif new_code.startswith("```"):
                new_code = new_code[3:]
            if new_code.endswith("```"):
                new_code = new_code[:-3]
            new_code = new_code.strip()
            # 仅当整个响应被一对 ``` 包裹时才做 split 提取
            if new_code.startswith("```") and new_code.count("```") == 2:
                parts = new_code.split("```")
                new_code = parts[1].strip() if len(parts) >= 2 else new_code

            # 如果 LLM 返回的不是代码（太短、只有安装指令等），跳过本轮
            if len(new_code) < 100:
                yield {"event": "thinking", "round": round_num, "message": "LLM 返回内容过短，跳过本轮"}
                continue

            current_code = new_code
            yield {"event": "code_updated", "round": round_num, "code": current_code}

            # ── ⑥ 系统自动安装新代码的依赖 ──
            new_deps = self._extract_imports(current_code)
            if new_deps:
                async for dep_event in self.install_deps_stream(tool_id, new_deps, stop_event=stop_event):
                    if stop_event and stop_event.is_set():
                        yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                        return
                    yield dep_event

        await self._write_debug_log(tool_id, timestamp, debug_log_entries, success=False, final_round=max_rounds)
        yield {"event": "done", "round": max_rounds, "success": False, "code": current_code, "message": f"达到最大轮数 {max_rounds}，调试未完成"}
