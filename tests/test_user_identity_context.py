"""V1 身份实时化回归：执行身份用 contextvar 承载，不再依赖进程 env 快照。

锁定的不变量：
- ``set_exec_uid`` 只影响当前协程/任务，并发任务互不覆盖；
- ``current_uid`` / ``user_home`` / ``runtime_env`` 都跟随执行身份；
- 登录身份（Web 请求）优先于执行身份；
- ToolRuntime 的产物归属固定为服务所有者，不随调用方变化；
- Agent 模板的 ``out_dir()`` 每次现算，跨用户复用同一进程时各归各。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core import user_paths


@pytest.fixture()
def temp_home_root(tmp_path, monkeypatch):
    """隔离用户根，避免测试写入真实的 /root/SB-USER。"""
    root = tmp_path / "SB-USER"
    monkeypatch.setattr(user_paths, "USER_HOME_ROOT", root)
    for key in ("SOTABAND_USER_ID", "SOTABAND_USER_HOME", "SOTABAND_OWNER_UID"):
        monkeypatch.delenv(key, raising=False)
    return root


@pytest.mark.asyncio
async def test_exec_uid_is_isolated_between_concurrent_tasks(temp_home_root):
    """两个并发任务各自设置身份，互不覆盖（RPC 与 execute 并发的核心保障）。"""
    seen: dict[str, tuple[str, str]] = {}
    ready_a, ready_b = asyncio.Event(), asyncio.Event()
    release = asyncio.Event()

    async def worker(uid: str, ready: asyncio.Event):
        token = user_paths.set_exec_uid(uid)
        try:
            ready.set()
            await release.wait()  # 两个任务都已设置身份后同时放行
            seen[uid] = (user_paths.current_uid(), str(user_paths.user_home()))
        finally:
            user_paths.reset_exec_uid(token)

    task_a = asyncio.create_task(worker("1000", ready_a))
    task_b = asyncio.create_task(worker("1001", ready_b))
    await ready_a.wait()
    await ready_b.wait()
    release.set()
    await asyncio.gather(task_a, task_b)

    assert seen["1000"] == ("1000", str(temp_home_root / "1000"))
    assert seen["1001"] == ("1001", str(temp_home_root / "1001"))


def test_runtime_env_follows_exec_uid(temp_home_root):
    token = user_paths.set_exec_uid("1002")
    try:
        env = user_paths.runtime_env()
    finally:
        user_paths.reset_exec_uid(token)

    assert env["SOTABAND_USER_ID"] == "1002"
    assert env["SOTABAND_USER_HOME"] == str(temp_home_root / "1002")


def test_login_identity_wins_over_exec_uid(monkeypatch, temp_home_root):
    """Web 请求线程里登录身份优先级最高，执行身份不能越权覆盖它。"""
    import core.auth.session as session

    monkeypatch.setattr(session, "current_identity", lambda: "0")
    token = user_paths.set_exec_uid("1000")
    try:
        assert user_paths.current_uid() == "0"
        assert user_paths.user_home() == temp_home_root / "0"
    finally:
        user_paths.reset_exec_uid(token)


def test_tool_runtime_owner_is_stable(monkeypatch):
    """常驻 Worker 的产物归属 = 服务所有者，不随调用方 uid 变化。"""
    from core.agent.tool_runtime import ToolRuntime

    monkeypatch.delenv("SOTABAND_OWNER_UID", raising=False)
    assert ToolRuntime(Path(".")).owner_uid == "100"
    assert ToolRuntime(Path("."), owner_uid="1000").owner_uid == "1000"

    monkeypatch.setenv("SOTABAND_OWNER_UID", "1001")
    assert ToolRuntime(Path(".")).owner_uid == "1001"


def test_agent_header_out_dir_resolves_per_call(temp_home_root, monkeypatch):
    """Agent 模板的 out_dir() 必须每次现算，而不是导入期快照。"""
    from core.resource.builder.agent_builder import HEADER_TEMPLATE

    code = HEADER_TEMPLATE.format(agent_name="T", agent_id="t-identity")
    namespace: dict = {"__file__": str(temp_home_root / "agents" / "t-identity" / "agent.py")}
    exec(compile(code, "<agent_header>", "exec"), namespace)

    token = user_paths.set_exec_uid("1003")
    try:
        assert namespace["user_home"]() == temp_home_root / "1003"
        assert namespace["out_dir"]("logs") == temp_home_root / "1003" / "logs"
        assert namespace["current_uid"]() == "1003"
    finally:
        user_paths.reset_exec_uid(token)


def test_tool_executor_script_injects_exec_identity(temp_home_root):
    """一次性工具子进程的 env 必须取当前执行身份，而不是进程启动快照。"""
    from core.executor.tool_executor import ToolExecutor

    token = user_paths.set_exec_uid("1004")
    try:
        script = ToolExecutor._build_script(
            "def execute(**kwargs):\n    return {'status': 'success'}\n",
            {},
            str(temp_home_root / "1004" / "tools" / "implementations" / "demo"),
            str(user_paths.PROJECT_ROOT),
        )
    finally:
        user_paths.reset_exec_uid(token)

    # 脚本里是 JSON 序列化的 env 赋值：SOTABAND_USER_ID = "1004"
    assert '"1004"' in script
    assert "SOTABAND_USER_HOME" in script


def test_one_shot_tool_subprocess_writes_to_each_caller_home(temp_home_root):
    """端到端：同一工具在两个用户身份下各跑一次，产物必须各落各自目录。"""
    import subprocess
    import sys

    from core.executor.tool_executor import ToolExecutor

    code = (
        "def execute(**kwargs):\n"
        "    import os\n"
        "    from pathlib import Path\n"
        "    p = Path(os.environ['SOTABAND_USER_HOME']) / 'data' / 'probe.txt'\n"
        "    p.parent.mkdir(parents=True, exist_ok=True)\n"
        "    p.write_text('ok', encoding='utf-8')\n"
        "    return {'status': 'success', 'data': {'output_path': str(p)}}\n"
    )
    tool_dir = str(temp_home_root / "tools" / "implementations" / "probe")

    for uid in ("1005", "1006"):
        token = user_paths.set_exec_uid(uid)
        try:
            script = ToolExecutor._build_script(
                code, {}, tool_dir, str(user_paths.PROJECT_ROOT))
        finally:
            user_paths.reset_exec_uid(token)

        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert (temp_home_root / uid / "data" / "probe.txt").read_text(encoding="utf-8") == "ok"
