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

from core.proc_utils import no_window_kwargs
from core.resource import param_guard


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
    def _resolve_impl_dir(cls, tool_id: str) -> Path:
        """在「内置 + 私有」资源根中定位工具实现目录。

        返回第一个含 tool.py 的实现目录；均不存在时返回内置默认路径
        （便于上层给出明确的“工具不存在”错误）。
        """
        for space in cls._tool_space_dirs():
            d = space / "implementations" / tool_id
            if (d / "tool.py").exists():
                return d
        # 兜底：返回内置默认路径
        return cls._project_root() / "resources" / "tools" / "implementations" / tool_id

    @staticmethod
    def _tool_space_dirs() -> list[Path]:
        """返回工具空间目录列表（私有在前、内置在后）。

        顺序决定「同 id 同时存在于两个根」时跑哪一份代码：写入一律落私有根，
        注册表也是私有覆盖内置，故执行必须同样私有优先 —— 否则用户改完代码
        会看到详情页/测试是新的，实际执行却仍是内置旧代码。
        """
        try:
            from core.resource import paths as _paths

            return [r / "tools" for r in _paths.read_roots()]
        except Exception:
            return [Path(__file__).resolve().parent.parent.parent / "resources" / "tools"]

    @classmethod
    def _get_python_exe(cls, tool_id: str) -> str:
        """获取工具执行所用的 Python 解释器（同时也是 pip 的安装目标）。

        优先级：
        1. 工具独立 ``.venv`` 的解释器（在内置/私有目录中查找）
        2. 项目 ``venv`` 的解释器（避免污染系统全局 Python）
        3. 当前进程的 ``sys.executable``

        注意：venv 的目录布局跨平台不同 —— Windows 是 ``Scripts/python.exe``，
        Linux/macOS 是 ``bin/python``。历史实现只查 ``bin/python``，在 Windows
        上**永远探测不到**，于是一律回退到 ``sys.executable``：若后端由全局
        Python 启动，pip 就装进全局环境（服务器上常因无写权限直接失败），
        表现为「依赖装了还缺 / 每轮都报缺依赖」。
        """
        candidates_rel = (
            ("Scripts", "python.exe"),   # Windows
            ("bin", "python"),           # Linux / macOS
        )
        for space in cls._tool_space_dirs():
            impl_dir = space / "implementations" / tool_id
            for rel in candidates_rel:
                venv_py = impl_dir.joinpath(".venv", *rel)
                if venv_py.exists():
                    return str(venv_py)

        for rel in candidates_rel:
            project_venv_py = cls._project_root().joinpath("venv", *rel)
            if project_venv_py.exists():
                return str(project_venv_py)

        return sys.executable

    @classmethod
    def _build_script(cls, code: str, params: dict, tool_dir: str, project_root: str) -> str:
        """构造统一的临时执行脚本。

        与自动调试 sandbox_execute 使用完全相同的模板结构，
        但增加了 TOOL_DIR 环境变量设置。
        """
        # 清理 surrogate 字符（如 LLM 生成的数学符号），避免 json.dumps 报错
        def _safe_dumps(obj):
            return json.dumps(obj, ensure_ascii=False).encode('utf-8', errors='replace').decode('utf-8')

        return (
            f"import json, sys, os\n"
            f"import io\n"
            f"# 强制 UTF-8 输出，避免 Windows GBK 编码问题\n"
            f"sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')\n"
            f"sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')\n"
            f"os.environ['TOOL_DIR'] = {_safe_dumps(tool_dir)}\n"
            f"sys.path.insert(0, {_safe_dumps(project_root)})\n"
            f"# 屏蔽 sys.argv 防止代码中误用 argparse 等 CLI 参数\n"
            f"sys.argv = [sys.argv[0]]\n"
            f"code = {_safe_dumps(code)}\n"
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
        timeout: float = None,
        code: str = None,
    ) -> dict:
        """异步执行工具。

        Args:
            tool_id: 工具ID
            params: 执行参数字典
            timeout: 超时秒数，None 表示无超时，传入数字则限时
            code: 可选，直接传入代码字符串（用于自动调试，避免重复读文件）

        Returns:
            {"status": "success"|"failed", "message": ..., "data": ...}
        """
        # 必填参数兜底：按 registry 的 param_meta 统一拦截，直接返回失败，
        # 不启动子进程（工具代码里已不再自带缺参校验）。
        guard = param_guard.check_params(tool_id, params)
        if guard is not None:
            return guard

        project_root = str(cls._project_root())
        impl_dir = cls._resolve_impl_dir(tool_id)
        python_exe = cls._get_python_exe(tool_id)

        if code is None:
            impl_file = impl_dir / "tool.py"
            if not impl_file.exists():
                return {"status": "failed", "message": f"工具代码不存在: {impl_file}"}
            code = impl_file.read_text(encoding='utf-8-sig')

        tool_dir = str(impl_dir)
        script = cls._build_script(code, params, tool_dir, project_root)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding='utf-8') as f:
            f.write(script)
            tmp_path = f.name

        try:
            proc = await asyncio.create_subprocess_exec(
                python_exe, tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **no_window_kwargs(),
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

            result = cls._parse_output(stdout.decode('utf-8', errors='replace'))
            if proc.returncode != 0 and result.get("status") != "success":
                result.setdefault("stderr", stderr.decode('utf-8', errors='replace')[:500])
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
        timeout: float = None,
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
        guard = param_guard.check_params(tool_id, params)
        if guard is not None:
            return guard

        project_root = str(cls._project_root())
        impl_dir = cls._resolve_impl_dir(tool_id)
        python_exe = cls._get_python_exe(tool_id)

        if code is None:
            impl_file = impl_dir / "tool.py"
            if not impl_file.exists():
                return {"status": "failed", "message": f"工具代码不存在: {impl_file}"}
            code = impl_file.read_text(encoding='utf-8-sig')

        tool_dir = str(impl_dir)
        script = cls._build_script(code, params, tool_dir, project_root)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding='utf-8') as f:
            f.write(script)
            tmp_path = f.name

        try:
            proc = subprocess.run(
                [python_exe, tmp_path],
                capture_output=True, text=True, timeout=timeout,
                encoding='utf-8', errors='replace',
                **no_window_kwargs(),
            )
            result = cls._parse_output(proc.stdout.strip())
            if proc.returncode != 0 and result.get("status") != "success":
                result.setdefault("stderr", proc.stderr[:500])
            return result
        finally:
            os.unlink(tmp_path)
