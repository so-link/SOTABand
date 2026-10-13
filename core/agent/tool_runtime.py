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
from pathlib import Path


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
    try:
        if not _LOADED:
            result = {"status": "failed", "message": f"工具加载失败: {_LOAD_ERR[:300]}"}
        else:
            out = execute(**task.get("params", {}))
            result = out if isinstance(out, dict) else {"status": "success", "data": out}
    except Exception as e:
        result = {"status": "failed", "message": f"执行异常: {e}\n{traceback.format_exc()[:800]}"}
    sys.stdout.write(json.dumps({"event": "result", "result": result}, ensure_ascii=False) + "\n")
    sys.stdout.flush()
"""


class ToolRuntime:
    """管理某 Agent 依赖工具的常驻进程。"""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        # {tool_id: {"proc": Process, "ready": bool, "load_error": str}}
        self._workers: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    # ── 进程探测 ──

    @staticmethod
    def _tool_space_dirs() -> list[Path]:
        """工具空间目录列表（内置 + 私有）。"""
        try:
            from core.resource import paths as _paths

            return [r / "tools" for r in _paths.resource_roots()]
        except Exception:
            from pathlib import Path as _P

            return [_P(__file__).resolve().parent.parent.parent / "resources" / "tools"]

    def _python_exe(self, tool_id: str) -> str:
        """常驻 Worker 与一次性执行、依赖检查使用相同 Python。"""
        from core.executor.tool_environment import resolve_tool_python
        return resolve_tool_python(tool_id)

    def _tool_dir(self, tool_id: str) -> Path:
        """在「内置 + 私有」资源根中定位工具实现目录。"""
        from core.executor.tool_environment import resolve_tool_dir
        return resolve_tool_dir(tool_id)

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
                    PYTHONIOENCODING="utf-8",
                    PYTHONPATH=str(self.project_root),
                )
                proc = await asyncio.create_subprocess_exec(
                    self._python_exe(tool_id),
                    "-c", _WORKER_SCRIPT,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**__import__("os").environ, **env},
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

    async def call(self, tool_id: str, params: dict, timeout: float | None = 300) -> dict | None:
        """通过常驻 Worker 调用工具；不可用时返回 None（调用方回退）。"""
        w = self._workers.get(tool_id)
        if not w or w["proc"].returncode is not None or not w.get("ready"):
            # 尝试按需启动
            st = await self._start_one(tool_id)
            w = self._workers.get(tool_id)
            if not w or not w.get("ready"):
                return None

        proc = w["proc"]
        try:
            task = json.dumps({"type": "call", "params": params}, ensure_ascii=False) + "\n"
            proc.stdin.write(task.encode("utf-8"))
            await proc.stdin.drain()
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            if not line:
                raise RuntimeError("Worker 无响应")
            evt = json.loads(line.decode("utf-8", "replace"))
            if evt.get("event") != "result":
                raise RuntimeError(f"Worker 返回异常事件: {evt.get('event')}")
            return evt.get("result")
        except Exception as e:
            # 调用失败 → 标记失效，交由调用方回退
            w["ready"] = False
            return {"status": "failed", "message": f"常驻调用失败: {e}", "_runtime_error": True}

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
        for tid, w in list(self._workers.items()):
            proc = w.get("proc")
            if proc and proc.returncode is None:
                # 1) 请求优雅退出
                try:
                    proc.stdin.write((json.dumps({"type": "shutdown"}) + "\n").encode())
                    await proc.stdin.drain()
                    proc.stdin.close()
                except Exception:
                    pass
                # 2) 等待退出（给模型释放留出时间）
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
            self._workers.pop(tid, None)
