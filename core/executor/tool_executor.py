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
    def _security_gate(code: str) -> dict | None:
        """执行前静态安全闸门：命中阻断项返回失败结果，放行返回 None。

        这里是所有执行路径（/test、/{id}/execute、Agent 调用、自动调试）
        共享的最后闸门——注册/保存/导入在落盘前已拦截，但存量工具与
        直接传码执行仍需执行前复查。
        """
        from core.security.code_scan import scan_code

        scan = scan_code(code)
        if scan.ok:
            return None
        return {
            "status": "failed",
            "message": "静态安全审查未通过：\n" + "\n".join(scan.errors),
            "security_scan": scan.to_dict(),
        }

    @staticmethod
    def _project_root() -> Path:
        """项目根目录"""
        return Path(__file__).resolve().parent.parent.parent

    @staticmethod
    def _bump_usage(tool_id: str) -> None:
        """工具被真实执行一次 → usage_count 自增（称不上计数失败即忽略）。"""
        try:
            from core.resource.registry.tool_registry import ToolRegistry
            ToolRegistry().bump_usage_count(tool_id)
        except Exception:
            pass  # 计数失败不影响执行结果

    @classmethod
    def _resolve_file_params(cls, params: dict) -> None:
        """参数值中的 file_id → 当前用户目录内真实路径（平台入口层转换）。

        界面只持有 file_id，而工具参数语义就是路径；转换收在这里，工具
        （含 AI 生成的）不需要知道 id 体系。失败静默——无效 id 与无效
        路径对工具而言失败面相同。
        """
        try:
            from core import user_files
            user_files.resolve_params(params)
        except Exception:
            pass

    @classmethod
    def _register_artifacts(cls, result: dict) -> None:
        """把结果里指向用户目录的 ``*_path`` 登记为 file_id（就地注入）。

        收在执行器出口而不是每个工具里：工具（含 AI 生成的）只需按约定把
        产物写到 SOTABAND_USER_HOME 并返回路径，平台负责签发前端可用的 id。
        登记失败绝不影响工具结果本身——最坏情况是前端显示产物不可用。
        """
        try:
            from core import user_files
            recs = user_files.register_result_paths(result.get("data"), kind="tool")
            if recs:
                result["fileIds"] = [r["file_id"] for r in recs]
        except Exception:
            pass  # 登记失败不影响执行结果

    @classmethod
    def _resolve_impl_dir(cls, tool_id: str) -> Path:
        """在「内置 + 私有」资源根中定位工具实现目录。

        返回第一个含 tool.py 的实现目录；均不存在时返回私有根默认路径
        （便于上层给出明确的“工具不存在”错误，同时保证兜底路径可写）。
        """
        for space in cls._tool_space_dirs():
            d = space / "implementations" / tool_id
            if (d / "tool.py").exists():
                return d
        # 兜底：未注册工具（如自动调试阶段）落到私有根，避免写入内置只读资产
        from core.resource import paths as _paths

        return _paths.private_space_dir("tools") / "implementations" / tool_id

    @staticmethod
    def _tool_space_dirs() -> list[Path]:
        """返回工具空间目录列表（内置 + 私有）。"""
        try:
            from core.resource import paths as _paths

            return [r / "tools" for r in _paths.resource_roots()]
        except Exception:
            return [Path(__file__).resolve().parent.parent.parent / "resources" / "tools"]

    @classmethod
    def _venv_python(cls, venv_root: Path) -> Path | None:
        """venv 根目录 → 解释器路径（兼容 Unix bin/ 与 Windows Scripts/）。"""
        for rel in (("bin", "python"), ("Scripts", "python.exe")):
            p = venv_root.joinpath(*rel)
            if p.exists():
                return p
        return None

    @classmethod
    def _get_python_exe(cls, tool_id: str) -> str:
        """获取工具执行所用的 Python 解释器。

        优先级：
        1. 工具独立 .venv（在内置/私有目录中查找）
        2. 项目 venv（避免污染系统全局 Python）
        3. 当前进程的 sys.executable
        """
        for space in cls._tool_space_dirs():
            venv_py = cls._venv_python(space / "implementations" / tool_id / ".venv")
            if venv_py:
                return str(venv_py)

        project_venv_py = cls._venv_python(cls._project_root() / "venv")
        if project_venv_py:
            return str(project_venv_py)

        return sys.executable

    @staticmethod
    def _params_literal(params: dict) -> str:
        """把参数字典渲染成合法的 Python 源码字面量。

        JSON 的 true/false/null 不是 Python 字面量，直接 json.dumps 拼进源码
        会触发 ``NameError: name 'true' is not defined``；repr() 产出的
        True/False/None 才是合法表达式。
        """
        text = repr(params)
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            text = text.encode("utf-8", errors="replace").decode("utf-8")
        return text

    @classmethod
    def _build_script(cls, code: str, params: dict, tool_dir: str, project_root: str) -> str:
        """构造统一的临时执行脚本。

        与自动调试 sandbox_execute 使用完全相同的模板结构，
        但增加了 TOOL_DIR 环境变量设置。
        """
        # 清理 surrogate 字符（如 LLM 生成的数学符号），避免 json.dumps 报错
        def _safe_dumps(obj):
            return json.dumps(obj, ensure_ascii=False).encode('utf-8', errors='replace').decode('utf-8')

        from core.user_paths import runtime_env
        _rt = runtime_env()

        return (
            f"import json, sys, os\n"
            f"import io\n"
            f"# 强制 UTF-8 输出，避免 Windows GBK 编码问题\n"
            f"sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')\n"
            f"sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')\n"
            f"os.environ['TOOL_DIR'] = {_safe_dumps(tool_dir)}\n"
            # 根目录/用户目录一律由父进程注入：工具代码不得靠 __file__ 层级反推
            f"os.environ['SOTABAND_ROOT'] = {_safe_dumps(_rt['SOTABAND_ROOT'])}\n"
            f"os.environ['SOTABAND_USER_HOME'] = {_safe_dumps(_rt['SOTABAND_USER_HOME'])}\n"
            f"os.environ['SOTABAND_USER_ID'] = {_safe_dumps(_rt['SOTABAND_USER_ID'])}\n"
            f"sys.path.insert(0, {_safe_dumps(project_root)})\n"
            f"# 屏蔽 sys.argv 防止代码中误用 argparse 等 CLI 参数\n"
            f"sys.argv = [sys.argv[0]]\n"
            f"code = {_safe_dumps(code)}\n"
            f"try:\n"
            f"    exec(code)\n"
            f"    result = execute(**{cls._params_literal(params)})\n"
            f"    print(json.dumps(result, default=str, ensure_ascii=False))\n"
            f"except ModuleNotFoundError as e:\n"
            f"    print(json.dumps({{'status':'failed','message':f'Missing module: {{e.name}}','error':'ModuleNotFoundError','missing_module':e.name}}))\n"
            f"except Exception as e:\n"
            f"    import traceback\n"
            f"    print(json.dumps({{'status':'failed','message':str(e),'error':type(e).__name__,'traceback':traceback.format_exc()[-2000:]}}))\n"
        )

    @classmethod
    def _launch_env_cwd(cls) -> tuple[dict, str]:
        """工具子进程环境 + cwd：注入系统根与用户目录，cwd 定为 USER_HOME。

        子进程内任何相对路径都必须有确定落点，否则工具代码写 ``open("x.png","w")``
        会落进 FastAPI 进程 cwd（可能是 git 工作区）。
        """
        import os as _os
        from core.user_paths import runtime_env, user_home
        home = user_home()
        home.mkdir(parents=True, exist_ok=True)
        return {**_os.environ, **runtime_env(), "PYTHONUNBUFFERED": "1"}, str(home)

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
        cls._resolve_file_params(params)
        project_root = str(cls._project_root())
        impl_dir = cls._resolve_impl_dir(tool_id)
        python_exe = cls._get_python_exe(tool_id)

        # 是否按 tool_id 从注册目录加载代码（vs 调用方直传 code 的调试路径）。
        # 计数判定用；不能事后判 code is None——下面加载会把 code 覆盖为文件内容。
        from_registry = code is None
        if code is None:
            impl_file = impl_dir / "tool.py"
            if not impl_file.exists():
                return {"status": "failed", "message": f"工具代码不存在: {impl_file}"}
            code = impl_file.read_text(encoding='utf-8')

        blocked = cls._security_gate(code)
        if blocked is not None:
            return blocked

        tool_dir = str(impl_dir)
        script = cls._build_script(code, params, tool_dir, project_root)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding='utf-8') as f:
            f.write(script)
            tmp_path = f.name

        try:
            _env, _cwd = cls._launch_env_cwd()
            proc = await asyncio.create_subprocess_exec(
                python_exe, tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=_cwd,
                env=_env,
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
            # 按 tool_id 真实执行成功 → 使用次数 +1（沙箱测试/自动调试传
            # code 直接执行不走计数，避免把调试点击数算进真实使用）
            if from_registry and result.get("status") == "success":
                cls._bump_usage(tool_id)
            # 产物文件登记：工具返回的 *_path 换成前端可用的 file_id
            if result.get("status") == "success":
                cls._register_artifacts(result)
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
        project_root = str(cls._project_root())
        impl_dir = cls._resolve_impl_dir(tool_id)
        python_exe = cls._get_python_exe(tool_id)

        # 计数判定见 execute() 同名变量说明
        from_registry = code is None
        if code is None:
            impl_file = impl_dir / "tool.py"
            if not impl_file.exists():
                return {"status": "failed", "message": f"工具代码不存在: {impl_file}"}
            code = impl_file.read_text(encoding='utf-8')

        blocked = cls._security_gate(code)
        if blocked is not None:
            return blocked

        tool_dir = str(impl_dir)
        script = cls._build_script(code, params, tool_dir, project_root)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding='utf-8') as f:
            f.write(script)
            tmp_path = f.name

        try:
            _env, _cwd = cls._launch_env_cwd()
            proc = subprocess.run(
                [python_exe, tmp_path],
                capture_output=True, text=True, timeout=timeout,
                encoding='utf-8', errors='replace',
                cwd=_cwd, env=_env,
            )
            result = cls._parse_output(proc.stdout.strip())
            if proc.returncode != 0 and result.get("status") != "success":
                result.setdefault("stderr", proc.stderr[:500])
            # 按 tool_id 真实执行成功 → 使用次数 +1（与 execute() 一致）
            if from_registry and result.get("status") == "success":
                cls._bump_usage(tool_id)
            return result
        finally:
            os.unlink(tmp_path)
