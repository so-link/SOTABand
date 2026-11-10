"""常驻 Worker（ToolRuntime）安全闸门回归测试

对应架构评审 C-2：常驻路径曾直接 exec 工具代码，不经静态闸门、
不带运行时审计前导，比一次性执行路径宽松。
"""

import asyncio
import types

import pytest

from core.agent.tool_runtime import ToolRuntime, _WORKER_PRELUDE, _WORKER_TAIL


def _make_runtime(tmp_path, tool_id: str, tool_code: str) -> ToolRuntime:
    rt = ToolRuntime(project_root=tmp_path)
    fake_dir = tmp_path / "impl" / tool_id
    fake_dir.mkdir(parents=True)
    (fake_dir / "tool.py").write_text(tool_code, encoding="utf-8")
    rt._tool_dir = lambda tid, _d=fake_dir: _d  # type: ignore[method-assign]
    return rt


def test_blocked_tool_never_starts(tmp_path):
    """含阻断级代码的工具：启动返回 blocked，不拉起任何子进程。"""
    rt = _make_runtime(
        tmp_path, "evil-tool",
        "import os\n\n\ndef execute():\n    return os.system('echo pwned')\n",
    )

    async def _run():
        return await rt._start_one("evil-tool")

    st = asyncio.run(_run())
    assert st["status"] == "blocked"
    assert "os.system" in (st.get("error") or "")
    assert rt._workers == {}  # 进程从未创建


def test_gate_reuses_tool_executor_premise(tmp_path):
    """同一份代码：ToolExecutor 闸门拦截 ⇔ ToolRuntime 启动拦截。"""
    code = "def execute():\n    return __import__('ctypes')\n"
    from core.executor.tool_executor import ToolExecutor

    assert ToolExecutor._security_gate(code) is not None

    rt = _make_runtime(tmp_path, "evil2", code)

    async def _run():
        return await rt._start_one("evil2")

    assert asyncio.run(_run())["status"] == "blocked"


def test_worker_script_includes_preamble(tmp_path, monkeypatch):
    """启动脚本必须 = 预热 + 安全前导 + 任务尾，前导含审计钩子。"""
    rt = _make_runtime(tmp_path, "ok-tool", "def execute(x=1):\n    return {'status': 'success', 'data': x}\n")

    captured: dict = {}

    class FakeProc:
        pid = 4242
        returncode = None

        class stdout:
            @staticmethod
            async def readline():
                return b'{"event": "ready", "loaded": true, "warmup_errors": []}\n'

        class stdin:
            @staticmethod
            def write(b):
                pass

            @staticmethod
            async def drain():
                pass

    async def fake_exec(*args, **kwargs):
        captured["script"] = args[2]
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    async def _run():
        return await rt._start_one("ok-tool")

    st = asyncio.run(_run())
    assert st["status"] == "started"
    script = captured["script"]
    assert "_sec_audit" in script and "addaudithook" in script
    assert script.index("_sec_audit") < script.index("_TOOL_CODE")
    assert "_sec_merge(result)" in script
    assert _WORKER_PRELUDE in script and _WORKER_TAIL in script
