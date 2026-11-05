"""ToolRuntime — Agent 级工具常驻运行时

设计原则（见 docs/agent-types-design.md）：
- **工具保持纯粹**：工具代码不被修改，仍是无状态、可独立运行的功能单元。
- **常驻由 Agent 负责**：Web 服务式 Agent 将其依赖的工具进程常驻化，
  进程启动时一次性完成重型库导入（如 torch/mmyolo）与模型加载，
  之后反复接收任务，避免每次调用都重复付出数秒的启动开销。

工作方式：
    主进程(Agent) ──(JSON 行任务)──> 常驻 Worker 进程（已预热）
                 <──(JSON 行结果)──

Worker 本质上是运行工具的长期进程，通过 stdin 接收 {tool_id, params}，
通过 stdout 返回 {status, message, data}。首次启动时预热（import + 加载），
后续调用仅付出「推理时间」。

若常驻不可用（启动失败 / 进程崩溃），调用方回退到 ToolExecutor 的一次性执行，
保证功能始终可用。
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

from core.proc_utils import no_window_kwargs


# 常驻 Worker 的宿主脚本：先预热常用重型库，再循环处理任务。
_WORKER_SCRIPT = r"""
import sys, os, json, io, traceback

# 项目根（由父进程通过环境变量注入）
PROJECT_ROOT = os.environ.get("SOTABAND_ROOT", "")
if PROJECT_ROOT and PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 工具目录（用于定位 tool.py 与其独立 venv 依赖）
TOOL_DIR = os.environ.get("TOOL_DIR", "")
if TOOL_DIR and TOOL_DIR not in sys.path:
    sys.path.insert(0, TOOL_DIR)

# ── 预热：一次性导入重型库（这些在每次新进程执行中都是主要开销）──
_WARMUP_ERRS = []
for _mod in ("torch", "numpy"):
    try:
        __import__(_mod)
    except Exception as e:
        _WARMUP_ERRS.append(f"{_mod}: {e}")
try:
    from mmyolo.utils import register_all_modules
    register_all_modules()
except Exception as e:
    _WARMUP_ERRS.append(f"mmyolo: {e}")

# ── 加载工具代码 ──
_TOOL_CODE = ""
try:
    with open(os.path.join(TOOL_DIR, "tool.py"), encoding="utf-8") as f:
        _TOOL_CODE = f.read()
    os.environ.setdefault("TOOL_DIR", TOOL_DIR)
    exec(compile(_TOOL_CODE, os.path.join(TOOL_DIR, "tool.py"), "exec"), globals())
    _LOADED = True
except Exception as e:
    _LOADED = False
    _LOAD_ERR = f"{e}\n{traceback.format_exc()}"

# 通知父进程：Worker 已就绪（含预热信息）
sys.stdout.write(json.dumps({
    "event": "ready",
    "warmup_errors": _WARMUP_ERRS,
    "loaded": _LOADED,
}) + "\n")
sys.stdout.flush()

# ── 任务循环 ──
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        task = json.loads(line)
    except Exception:
        continue
    if task.get("type") == "shutdown":
        break
    if task.get("type") != "call":
        continue
    # ── 身份：每次调用按 RPC 载荷里的 owner_id 重设环境变量 ──
    # 常驻 Worker 会连续服务多个用户，若沿用启动时的环境变量就会串号
    # （A 的数据被写成 B 的 owner）。这里每次调用都显式覆盖/清除。
    _owner = str(task.get("owner_id") or "")
    if _owner:
        os.environ["SOTABAND_USER_ID"] = _owner
    else:
        os.environ.pop("SOTABAND_USER_ID", None)
    try:
        from core.user_context import reset_cache
        reset_cache()
    except Exception:
        pass
    try:
        if not _LOADED:
            result = {"status": "failed", "message": f"工具加载失败: {_LOAD_ERR[:300]}"}
        else:
            out = execute(**task.get("params", {}))
            result = out if isinstance(out, dict) else {"status": "success", "data": out}
    except Exception as e:
        result = {"status": "failed", "message": f"执行异常: {e}\n{traceback.format_exc()[:800]}"}
    sys.stdout.write(json.dumps({
        "event": "result",
        # 回显 call_id：父进程据此确认「这条结果属于本轮调用」，
        # 防止超时/取消后滞留的旧结果被下一次调用读走。
        "call_id": str(task.get("call_id") or ""),
        "result": result,
    }, ensure_ascii=False) + "\n")
    sys.stdout.flush()
"""


class ToolRuntime:
    """管理某 Agent 依赖工具的常驻进程。"""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        # {tool_id: {"proc": Process, "ready": bool, "load_error": str}}
        self._workers: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        # 每个 tool 一把调用锁：常驻 Worker 是单通道 RPC（写一条 → 读一条），
        # 并发调用会互相抢读，导致「第二个调用拿到前一个的结果」或超时后
        # 回退到 ToolExecutor 把有副作用的工具执行两遍。
        self._call_locks: dict[str, asyncio.Lock] = {}

    def _call_lock(self, tool_id: str) -> asyncio.Lock:
        """返回某工具的调用锁（惰性创建；兼容 ``__new__`` 构造的测试替身）。"""
        locks = getattr(self, "_call_locks", None)
        if locks is None:
            locks = self._call_locks = {}
        lock = locks.get(tool_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[tool_id] = lock
        return lock

    # ── 进程探测 ──

    @staticmethod
    def _tool_space_dirs() -> list[Path]:
        """工具空间目录列表（私有在前、内置在后）。

        与 ToolExecutor._tool_space_dirs 保持一致：写入落私有根，故定位也
        私有优先，否则常驻工具进程加载的还是内置旧代码。
        """
        try:
            from core.resource import paths as _paths

            return [r / "tools" for r in _paths.read_roots()]
        except Exception:
            from pathlib import Path as _P

            return [_P(__file__).resolve().parent.parent.parent / "resources" / "tools"]

    def _python_exe(self, tool_id: str) -> str:
        """选择执行工具的 Python：优先工具独立 venv，否则当前解释器。"""
        for space in self._tool_space_dirs():
            venv_py = space / "implementations" / tool_id / ".venv" / "bin" / "python"
            if venv_py.exists():
                return str(venv_py)
        return sys.executable

    def _tool_dir(self, tool_id: str) -> Path:
        """在「内置 + 私有」资源根中定位工具实现目录。"""
        for space in self._tool_space_dirs():
            d = space / "implementations" / tool_id
            if (d / "tool.py").exists():
                return d
        return self._tool_space_dirs()[0] / "implementations" / tool_id

    def _has_worker_script(self, tool_id: str) -> bool:
        return (self._tool_dir(tool_id) / "tool.py").exists()

    # ── 启动 / 预热 ──

    async def ensure_started(self, tool_ids: list[str]) -> dict:
        """为给定的工具集启动常驻 Worker（幂等）。返回每项启动状态。"""
        status = {}
        for tid in tool_ids:
            status[tid] = await self._start_one(tid)
        return status

    async def _start_one(self, tool_id: str) -> dict:
        async with self._lock:
            w = self._workers.get(tool_id)
            if w and w["proc"].returncode is None and w.get("ready"):
                return {"tool_id": tool_id, "status": "already_running"}

            if not self._has_worker_script(tool_id):
                return {"tool_id": tool_id, "status": "skipped", "reason": "工具实现不存在"}

            try:
                tool_dir = str(self._tool_dir(tool_id))
                env = dict(
                    SOTABAND_ROOT=str(self.project_root),
                    TOOL_DIR=tool_dir,
                    PYTHONUNBUFFERED="1",
                    PYTHONPATH=str(self.project_root),
                )
                # 清掉父进程可能残留的身份变量：Worker 的默认身份应为「未指定」，
                # 具体身份由每次 call 的 owner_id 覆盖（避免启动时把某个用户的
                # UUID 固化进长期进程）。
                _base_env = {
                    k: v for k, v in __import__("os").environ.items()
                    if k != "SOTABAND_USER_ID"
                }
                proc = await asyncio.create_subprocess_exec(
                    self._python_exe(tool_id),
                    "-c", _WORKER_SCRIPT,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**_base_env, **env},
                    **no_window_kwargs(),
                )
                # 等待 ready 事件（预热可能耗时数秒，给足超时）
                ready = False
                load_error = ""
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=180)
                    if line:
                        evt = json.loads(line.decode("utf-8", "replace"))
                        ready = bool(evt.get("loaded"))
                        if not ready:
                            load_error = "工具加载失败（详见 Worker 预热）"
                except asyncio.TimeoutError:
                    load_error = "预热超时"

                self._workers[tool_id] = {"proc": proc, "ready": ready, "load_error": load_error}
                return {
                    "tool_id": tool_id,
                    "status": "started" if ready else "failed",
                    "pid": proc.pid,
                    "error": load_error or None,
                }
            except Exception as e:
                return {"tool_id": tool_id, "status": "failed", "error": str(e)}

    # ── 调用 ──

    async def call(
        self,
        tool_id: str,
        params: dict,
        timeout: float | None = 300,
        owner_id: str | None = None,
    ) -> dict | None:
        """通过常驻 Worker 调用工具；不可用时返回 None（调用方回退）。

        ``owner_id`` 会随 RPC 载荷下发，Worker 在每次执行前重设
        ``SOTABAND_USER_ID`` —— 这是多用户共用同一常驻进程时不串号的关键。

        **并发语义**：同一 tool 的调用用 ``_call_lock`` 串行化。Worker 是
        单通道 RPC（写一条任务 → 读一条结果），并发调用会让第二个调用与前
        一个抢读同一条 stdout：轻则读到别人的结果，重则超时后拿到
        ``_runtime_error`` 而回退到 ToolExecutor **把工具再执行一遍**。
        协议错误后旧 Worker 不可复用，必须停掉（否则残留结果会漏给下一次调用）。
        """
        async with self._call_lock(tool_id):
            # Worker 状态必须在锁内重新判断：可能刚被另一个调用标记失效并停止。
            w = self._workers.get(tool_id)
            if not w or w["proc"].returncode is not None or not w.get("ready"):
                await self._start_one(tool_id)
                w = self._workers.get(tool_id)
                if not w or not w.get("ready"):
                    return None

            proc = w["proc"]
            call_id = uuid.uuid4().hex
            try:
                task = json.dumps(
                    {
                        "type": "call",
                        "params": params,
                        "owner_id": owner_id or "",
                        "call_id": call_id,
                    },
                    ensure_ascii=False,
                ) + "\n"
                proc.stdin.write(task.encode("utf-8"))
                await proc.stdin.drain()
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                if not line:
                    raise RuntimeError("Worker 无响应")
                evt = json.loads(line.decode("utf-8", "replace"))
                if evt.get("event") != "result":
                    raise RuntimeError(f"Worker 返回异常事件: {evt.get('event')}")
                echoed = evt.get("call_id")
                if echoed and echoed != call_id:
                    raise RuntimeError("Worker 响应与本轮调用不匹配（协议失步）")
                return evt.get("result")
            except asyncio.CancelledError:
                # 调用方被取消后 Worker 仍会把结果写回管道；继续复用会让下一个
                # 调用读到它。标记失效并停掉，下次调用重新预热。
                w["ready"] = False
                try:
                    await self.stop(tool_id)
                except Exception:
                    self._workers.pop(tool_id, None)
                raise
            except Exception as e:
                # 调用失败 → 标记失效并**停掉旧 Worker**（避免残留/半截响应
                # 被下一次调用读走），交由调用方回退。
                w["ready"] = False
                try:
                    await self.stop(tool_id)
                except Exception:
                    self._workers.pop(tool_id, None)
                return {"status": "failed", "message": f"常驻调用失败: {e}", "_runtime_error": True}

    async def stop(self, tool_id: str) -> dict:
        """停止并回收单个常驻 Worker（幂等）。"""
        async with self._lock:
            w = self._workers.pop(tool_id, None)
        if w is None:
            return {"tool_id": tool_id, "status": "not_running"}
        proc = w.get("proc")
        if proc is not None and proc.returncode is None:
            # 1) 请求优雅退出（让 Worker 有机会释放模型/显存）
            try:
                proc.stdin.write((json.dumps({"type": "shutdown"}) + "\n").encode())
                await proc.stdin.drain()
                proc.stdin.close()
            except Exception:
                pass
            # 2) 等待退出
            try:
                await asyncio.wait_for(proc.wait(), timeout=8)
            except asyncio.TimeoutError:
                # 3) 超时兜底：强制终止
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                except Exception:
                    pass
            except Exception:
                pass
        return {"tool_id": tool_id, "status": "stopped"}

    # ── 状态 / 停止 ──

    def status(self) -> dict:
        return {
            tid: {
                "running": w["proc"].returncode is None,
                "ready": bool(w.get("ready")),
                "pid": w["proc"].pid,
            }
            for tid, w in self._workers.items()
        }

    async def stop_all(self):
        """停止并回收所有常驻 Worker 进程。

        先发送 shutdown 命令请求优雅退出；若超时未退出则强制终止。
        Worker 进程退出后，其加载的模型与占用的显存/内存由操作系统自动释放。
        """
        for tid in list(self._workers):
            try:
                await self.stop(tid)
            except Exception:
                self._workers.pop(tid, None)
