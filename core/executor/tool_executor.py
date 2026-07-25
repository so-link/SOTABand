"""工具执行器 — 统一入口

保证自动调试和对话界面使用完全相同的执行环境：
- 始终使用独立子进程（永不与 FastAPI 同进程执行）
- 统一设置 TOOL_DIR 环境变量
- 统一的 Python 解释器选择逻辑
- 统一的临时脚本模板
- 统一的输出解析和错误处理
"""

import json
import os
import sys
import tempfile
import asyncio
import subprocess
from pathlib import Path
from typing import Any


class ToolExecutor:
    """工具执行器单例。

    所有场景（自动调试、对话界面、独立 API 调用）都通过此类执行工具，
    保证自动调试能过的工具在对话界面中一定能过。
    """

    @staticmethod
    def _project_root() -> Path:
        """项目根目录"""
        return Path(__file__).resolve().parent.parent.parent

    @classmethod
    def _get_python_exe(cls, tool_id: str) -> str:
        """获取工具执行所用的 Python 解释器。

        优先级：
        1. 工具独立 .venv/bin/python（如果存在）
        2. 当前进程的 sys.executable
        """
        venv_py = (
            cls._project_root()
            / "resources" / "tools" / "implementations" / tool_id
            / ".venv" / "bin" / "python"
        )
        if venv_py.exists():
            return str(venv_py)
        return sys.executable

    @classmethod
    def _build_script(cls, code: str, params: dict, tool_dir: str, project_root: str) -> str:
        """构造统一的临时执行脚本。

        与自动调试 sandbox_execute 使用完全相同的模板结构，
        但增加了 TOOL_DIR 环境变量设置。
        """
        return (
            f"import json, sys, os\n"
            f"os.environ['TOOL_DIR'] = {json.dumps(tool_dir)}\n"
            f"sys.path.insert(0, {json.dumps(project_root)})\n"
            f"code = {json.dumps(code)}\n"
            f"try:\n"
            f"    exec(code)\n"
            f"    result = execute(**{json.dumps(params)})\n"
            f"    print(json.dumps(result, default=str, ensure_ascii=False))\n"
            f"except ModuleNotFoundError as e:\n"
            f"    print(json.dumps({{'status':'failed','message':f'Missing module: {{e.name}}','error':'ModuleNotFoundError','missing_module':e.name}}))\n"
            f"except Exception as e:\n"
            f"    import traceback\n"
            f"    print(json.dumps({{'status':'failed','message':str(e),'error':type(e).__name__,'traceback':traceback.format_exc()[-2000:]}}))\n"
        )

    @classmethod
    def _parse_output(cls, stdout: str) -> dict:
        """解析子进程 stdout 输出为结构化结果"""
        if not stdout:
            return {"status": "failed", "message": "无输出"}
        lines = stdout.strip().split("\n")
        # 从最后一行往前找 JSON
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        # 尝试整个输出作为 JSON
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"status": "failed", "message": stdout[:500]}

    # ── 异步执行（用于 FastAPI async 上下文） ──

    @classmethod
    async def execute(
        cls,
        tool_id: str,
        params: dict,
        timeout: float = 120.0,
        code: str = None,
    ) -> dict:
        """异步执行工具。

        Args:
            tool_id: 工具ID
            params: 执行参数字典
            timeout: 超时秒数，默认 120 秒，None 表示无超时
            code: 可选，直接传入代码字符串（用于自动调试，避免重复读文件）

        Returns:
            {"status": "success"|"failed", "message": ..., "data": ...}
        """
        project_root = str(cls._project_root())
        impl_dir = cls._project_root() / "resources" / "tools" / "implementations" / tool_id
        python_exe = cls._get_python_exe(tool_id)

        if code is None:
            impl_file = impl_dir / "tool.py"
            if not impl_file.exists():
                return {"status": "failed", "message": f"工具代码不存在: {impl_file}"}
            code = impl_file.read_text()

        tool_dir = str(impl_dir)
        script = cls._build_script(code, params, tool_dir, project_root)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(script)
            tmp_path = f.name

        try:
            proc = await asyncio.create_subprocess_exec(
                python_exe, tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                if timeout is None:
                    stdout, stderr = await proc.communicate()
                else:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=timeout
                    )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {"status": "failed", "message": f"工具执行超时 ({timeout}秒)"}
            except asyncio.CancelledError:
                proc.kill()
                await proc.wait()
                raise

            result = cls._parse_output(stdout.decode())
            if proc.returncode != 0 and result.get("status") != "success":
                result.setdefault("stderr", stderr.decode()[:500])
            return result

        except asyncio.CancelledError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            raise
        finally:
            os.unlink(tmp_path)

    # ── 同步执行（用于非 async 上下文） ──

    @classmethod
    def execute_sync(
        cls,
        tool_id: str,
        params: dict,
        timeout: float = 120.0,
        code: str = None,
    ) -> dict:
        """同步执行工具。内部实现与 execute() 完全一致，仅使用同步 subprocess。

        Args:
            tool_id: 工具ID
            params: 执行参数字典
            timeout: 超时秒数
            code: 可选，直接传入代码字符串

        Returns:
            {"status": "success"|"failed", "message": ..., "data": ...}
        """
        project_root = str(cls._project_root())
        impl_dir = cls._project_root() / "resources" / "tools" / "implementations" / tool_id
        python_exe = cls._get_python_exe(tool_id)

        if code is None:
            impl_file = impl_dir / "tool.py"
            if not impl_file.exists():
                return {"status": "failed", "message": f"工具代码不存在: {impl_file}"}
            code = impl_file.read_text()

        tool_dir = str(impl_dir)
        script = cls._build_script(code, params, tool_dir, project_root)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(script)
            tmp_path = f.name

        try:
            proc = subprocess.run(
                [python_exe, tmp_path],
                capture_output=True, text=True, timeout=timeout,
            )
            result = cls._parse_output(proc.stdout.strip())
            if proc.returncode != 0 and result.get("status") != "success":
                result.setdefault("stderr", proc.stderr[:500])
            return result
        finally:
            os.unlink(tmp_path)
