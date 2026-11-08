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

from core.scheduler.allocator import Allocation, AllocationError, allocate_in_thread, get_allocator
from core.scheduler.requirements import RequirementsError, resolve_tool_requirements
from core.proc_utils import no_window_kwargs
from config.settings import settings as _settings

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

    @staticmethod
    def _child_env(owner_id: str | None) -> dict:
        """构造工具子进程的环境变量。

        **身份显式注入**（管理员评审意见 1 的修复点）：

        - 有 ``owner_id`` → 覆盖为 ``SOTABAND_USER_ID``；
        - 无 ``owner_id`` → **删除**该变量，避免继承父进程残留的脏值
          （父进程可能是上一个用户/另一个 Agent 的上下文）。

        工具的 ``api_data.*`` 通过 ``current_owner_id()`` 读这个变量，
        从而在子进程里也能拿到发起方 UUID。
        """
        env = dict(os.environ)
        if owner_id:
            env["SOTABAND_USER_ID"] = str(owner_id)
        # 无显式 owner 时**保留父进程身份**：entrypoint 每个任务已设置正确值，
        # 主动 pop 会让 workflow / 生成的 Agent 等「未显式传 owner」的路径
        # 退化到机器身份（评审发现的回归点）。
        # Agent Web 服务的引擎令牌只属于 Agent 进程，不能泄漏给工具子进程。
        env.pop("SOTABAND_AGENT_HTTP_TOKEN", None)
        return env

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
    def _security_preamble(cls, tool_dir: str, project_root: str) -> str:
        """构造运行时安全审查前导代码（审计钩子 + 资源限制）。

        在 exec 工具代码之前注入子进程：
        - sys.addaudithook 拦截文件写入 / 系统调用 / 网络连接
          （audit hook 一旦注册无法被 Python 层移除，恶意代码也拆不掉）
        - resource.setrlimit 限制 CPU / 内存 / 文件大小 / 进程数
        策略来自 config.settings.SecurityConfig。
        """
        sec = _settings.security

        # 文件写入白名单 → 绝对路径
        # 注意：构建侧（本进程）与 hook 运行侧（子进程）同机同平台，
        # 统一 normpath+normcase 规范化，避免 Windows 反斜杠/正斜杠混用
        # 导致 startswith 失配（合法写入被误杀）
        roots: list[str] = []
        for p in sec.allowed_write_paths:
            p = str(p)
            if not os.path.isabs(p):
                p = os.path.join(project_root, p)
            roots.append(os.path.normcase(os.path.normpath(p)))
        roots.append(os.path.normcase(os.path.normpath(tool_dir)))  # 工具自身目录
        roots.append(os.path.normcase(os.path.normpath(tempfile.gettempdir())))  # 系统临时目录

        mode = sec.review_mode if sec.review_mode in ("enforce", "audit") else "off"

        return (
            "import sys as _sec_sys, os as _sec_os\n"
            "_sec_mode = " + json.dumps(mode) + "\n"
            "_sec_violations = []\n"
            "_sec_write_roots = " + json.dumps(roots) + "\n"
            "_sec_net_hosts = " + json.dumps(list(sec.allowed_network_hosts)) + "\n"
            "_sec_net_enforce = " + ("True" if sec.runtime_enforce_network else "False") + "\n"
            "\n"
            "# ── 资源限制 ──\n"
            "try:\n"
            "    import resource as _sec_resource\n"
            f"    _sec_resource.setrlimit(_sec_resource.RLIMIT_CPU, ({sec.max_cpu_seconds}, {sec.max_cpu_seconds}))\n"
            f"    _sec_resource.setrlimit(_sec_resource.RLIMIT_AS, ({sec.max_memory_mb} << 20, {sec.max_memory_mb} << 20))\n"
            f"    _sec_resource.setrlimit(_sec_resource.RLIMIT_FSIZE, ({sec.max_file_size_mb} << 20, {sec.max_file_size_mb} << 20))\n"
            f"    _sec_resource.setrlimit(_sec_resource.RLIMIT_NPROC, ({sec.max_processes}, {sec.max_processes}))\n"
            "except Exception:\n"
            "    pass\n"
            "\n"
            "# ── 审计钩子 ──\n"
            "def _sec_audit(event, args):\n"
            "    if _sec_mode == 'off':\n"
            "        return\n"
            "    msg = None\n"
            "    enforceable = False\n"
            "    if event == 'open':\n"
            "        try:\n"
            "            path = _sec_os.fspath(args[0])\n"
            "            mode = args[1] if len(args) > 1 else 'r'\n"
            "            if isinstance(mode, int):\n"
            "                mode = ''\n"
            "            mode = str(mode)\n"
            "        except Exception:\n"
            "            return\n"
            "        if any(c in mode for c in 'wa+'):\n"
            "            rp = _sec_os.path.normcase(_sec_os.path.realpath(path))\n"
            "            if not any(rp == r or rp.startswith(r + _sec_os.sep) for r in _sec_write_roots):\n"
            "                msg = 'FILE_WRITE_DENY: ' + rp\n"
            "                enforceable = True\n"
            "    elif event in ('os.system', 'os.posix_spawn', 'pty.spawn'):\n"
            "        msg = 'BANNED_SYSCALL: ' + event\n"
            "        enforceable = True\n"
            "    elif event == 'subprocess.Popen':\n"
            "        # 放行系统模板 _call_tool 发起的工具间调用\n"
            "        # （特征：python -c 'import json, sys; ...execute(...)'）\n"
            "        # CPython 3.12 事件签名: sys.audit('subprocess.Popen', executable, args, cwd, env)\n"
            "        #   args[0] = 可执行文件路径, args[1] = 命令列表 —— 特征匹配必须用 args[1]\n"
            "        try:\n"
            "            cmd = args[1] if len(args) > 1 else args[0]\n"
            "            joined = ' '.join(str(c) for c in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)\n"
            "        except Exception:\n"
            "            joined = ''\n"
            "        if 'import json, sys' in joined and 'execute(' in joined:\n"
            "            return\n"
            "        msg = 'BANNED_SYSCALL: subprocess.Popen ' + joined[:120]\n"
            "        enforceable = True\n"
            "    elif event == 'socket.connect':\n"
            "        try:\n"
            "            host = args[0][0]\n"
            "        except Exception:\n"
            "            host = '?'\n"
            "        if _sec_net_hosts and host not in _sec_net_hosts:\n"
            "            msg = 'NETWORK_DENY: ' + str(host)\n"
            "            enforceable = _sec_net_enforce\n"
            "        elif not _sec_net_hosts:\n"
            "            msg = 'NETWORK: ' + str(host)  # 无白名单时仅记录\n"
            "            enforceable = False\n"
            "    elif event == 'os.putenv':\n"
            "        msg = 'ENV_MODIFY: ' + str(args[0])[:60]\n"
            "        enforceable = False\n"
            "    if msg:\n"
            "        _sec_violations.append(msg)\n"
            "        if _sec_mode == 'enforce' and enforceable:\n"
            "            raise PermissionError('[安全策略] ' + msg)\n"
            "\n"
            "try:\n"
            "    _sec_sys.addaudithook(_sec_audit)\n"
            "except Exception:\n"
            "    pass\n"
            "\n"
            "def _sec_merge(result):\n"
            "    \"\"\"把运行时违规合并进结果 dict\"\"\"\n"
            "    if _sec_violations:\n"
            "        result['_security_violations'] = list(_sec_violations)\n"
            "    return result\n"
            "\n"
        )

    @classmethod
    def _build_script(cls, code: str, params: dict, tool_dir: str, project_root: str) -> str:
        """构造统一的临时执行脚本。

        与自动调试 sandbox_execute 使用完全相同的模板结构，
        但增加了 TOOL_DIR 环境变量设置与安全审查前导。
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
            + cls._security_preamble(tool_dir, project_root) +
            f"code = {_safe_dumps(code)}\n"
            f"try:\n"
            f"    exec(code)\n"
            f"    params = json.loads({_safe_dumps(json.dumps(params, ensure_ascii=False))})\n"
            f"    result = execute(**params)\n"
            f"    _sec_merge(result)\n"
            f"    print(json.dumps(result, default=str, ensure_ascii=False))\n"
            f"except ModuleNotFoundError as e:\n"
            f"    print(json.dumps(_sec_merge({{'status':'failed','message':f'Missing module: {{e.name}}','error':'ModuleNotFoundError','missing_module':e.name}})))\n"
            f"except PermissionError as e:\n"
            f"    print(json.dumps(_sec_merge({{'status':'failed','message':str(e),'error':'SecurityViolation'}})))\n"
            f"except Exception as e:\n"
            f"    import traceback\n"
            f"    print(json.dumps(_sec_merge({{'status':'failed','message':str(e),'error':type(e).__name__,'traceback':traceback.format_exc()[-2000:]}})))\n"
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

    # ── 设备调度（todo §12.2） ──

    @staticmethod
    def _allocate(tool_id: str, code: str, device: str | None) -> Allocation:
        """按资源需求匹配设备并预留；调用方须在执行结束后 release"""
        req = resolve_tool_requirements(tool_id, code)
        return get_allocator().allocate(f"tool:{tool_id}", req, device)

    @staticmethod
    def _unschedulable(e: Exception) -> dict:
        return {"status": "failed", "message": str(e), "error": type(e).__name__}

    # ── 异步执行（用于 FastAPI async 上下文） ──

    @classmethod
    async def execute(
        cls,
        tool_id: str,
        params: dict,
        timeout: float = None,
        code: str = None,
        owner_id: str | None = None,
        device: str | None = None,
    ) -> dict:
        """异步执行工具。

        Args:
            tool_id: 工具ID
            params: 执行参数字典
            timeout: 超时秒数，None 表示无超时，传入数字则限时
            code: 可选，直接传入代码字符串（用于自动调试，避免重复读文件）
            device: 可选，手动指定设备（cpu / gpu / gpu:N），默认由调度器自动匹配
            owner_id: 可选，资源归属标识（已登录用户 UUID）。注入子进程环境，
                供工具内 ``api_data.*`` 按人过滤数据集；None 表示不注入
                （未登录 / 无上下文场景，回退机器身份）

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

        try:
            req = await asyncio.to_thread(resolve_tool_requirements, tool_id, code)
            alloc = await allocate_in_thread(get_allocator(), f"tool:{tool_id}", req, device)
        except (RequirementsError, AllocationError) as e:
            return cls._unschedulable(e)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding='utf-8') as f:
            f.write(script)
            tmp_path = f.name

        try:
            proc = await asyncio.create_subprocess_exec(
                python_exe, tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=alloc.env(cls._child_env(owner_id)),
                **no_window_kwargs(),
            )
            alloc.attach_pid(proc.pid)
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
            get_allocator().release(alloc)
            os.unlink(tmp_path)

    # ── 同步执行（用于非 async 上下文） ──

    @classmethod
    def execute_sync(
        cls,
        tool_id: str,
        params: dict,
        timeout: float = None,
        code: str = None,
        owner_id: str | None = None,
        device: str | None = None,
    ) -> dict:
        """同步执行工具。内部实现与 execute() 完全一致，仅使用同步 subprocess。

        Args:
            tool_id: 工具ID
            params: 执行参数字典
            timeout: 超时秒数
            code: 可选，直接传入代码字符串
            device: 可选，手动指定设备（cpu / gpu / gpu:N）
            owner_id: 可选，资源归属标识（注入子进程环境，语义同 execute）

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

        try:
            alloc = cls._allocate(tool_id, code, device)
        except (RequirementsError, AllocationError) as e:
            return cls._unschedulable(e)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding='utf-8') as f:
            f.write(script)
            tmp_path = f.name

        try:
            proc = subprocess.run(
                [python_exe, tmp_path],
                capture_output=True, text=True, timeout=timeout,
                encoding='utf-8', errors='replace',
                env=alloc.env(cls._child_env(owner_id)),
                **no_window_kwargs(),
            )
            result = cls._parse_output(proc.stdout.strip())
            if proc.returncode != 0 and result.get("status") != "success":
                result.setdefault("stderr", proc.stderr[:500])
            return result
        finally:
            get_allocator().release(alloc)
            os.unlink(tmp_path)
