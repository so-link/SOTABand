"""V3 运行时路径守卫：写入白名单收敛为「用户目录 + 临时目录」。

锁定的不变量：
- 工具自身目录、项目根 data/logs/output 不再默认可写；
- 越界写入的错误消息带修复指引（产物写 _out_dir / SOTABAND_USER_HOME）；
- audit 模式只记录（工具仍成功），enforce 模式拦截并返回 failed；
- 写用户目录不误杀。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from core import user_paths
from core.executor.tool_executor import ToolExecutor

WRITE_TOOL_DIR = '''
import os

def execute(**kwargs):
    p = os.path.join(os.environ["TOOL_DIR"], "bad.txt")
    with open(p, "w") as f:
        f.write("x")
    return {"status": "success", "data": {"path": p}}
'''

WRITE_USER_HOME = '''
import os

def execute(**kwargs):
    base = os.path.join(os.environ["SOTABAND_USER_HOME"], "data")
    os.makedirs(base, exist_ok=True)
    p = os.path.join(base, "ok.txt")
    with open(p, "w") as f:
        f.write("x")
    return {"status": "success", "data": {"path": p}}
'''


@pytest.fixture()
def guard_env(tmp_path, monkeypatch):
    """隔离用户根与系统临时目录，避免 pytest 的 tmp_path 落在 Temp 下被放行。"""
    sys_temp = tmp_path / "sys-temp"
    sys_temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(sys_temp))
    monkeypatch.setattr(user_paths, "USER_HOME_ROOT", tmp_path / "SB-USER")
    monkeypatch.delenv("SOTABAND_USER_ID", raising=False)
    monkeypatch.setenv("REVIEW_ENFORCE_TOOLS", "")
    return tmp_path


def _write_roots(preamble: str) -> list[str]:
    m = re.search(r"_sec_write_roots = (\[.*\])", preamble)
    assert m, "未找到 _sec_write_roots"
    return json.loads(m.group(1))


def _run_tool(code: str, tool_dir: Path, project_root: Path) -> subprocess.CompletedProcess:
    script = ToolExecutor._build_script(
        code, {}, str(tool_dir), str(project_root), "demo-tool")
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


def test_write_roots_only_home_and_temp(guard_env):
    tool_dir = guard_env / "tools" / "impl"
    project_root = guard_env / "project"
    preamble = ToolExecutor._security_preamble(
        str(tool_dir), str(project_root), "demo-tool")
    roots = _write_roots(preamble)

    home = os.path.normcase(os.path.normpath(str(guard_env / "SB-USER" / "0")))
    assert home in roots
    assert not any(os.path.normcase(str(tool_dir)) == r for r in roots)
    assert not any(os.path.normcase(str(project_root)) == r for r in roots)


def test_deny_message_contains_guidance(guard_env):
    preamble = ToolExecutor._security_preamble(
        str(guard_env / "tools" / "impl"), str(guard_env / "project"), "demo-tool")
    assert "FILE_WRITE_DENY" in preamble
    assert "产物请写当前用户目录" in preamble
    assert "_out_dir" in preamble
    assert "只读资产" in preamble


def test_audit_records_tool_dir_write(guard_env, monkeypatch):
    monkeypatch.setenv("REVIEW_MODE", "audit")
    tool_dir = guard_env / "tools" / "impl"
    tool_dir.mkdir(parents=True)

    proc = _run_tool(WRITE_TOOL_DIR, tool_dir, guard_env / "project")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["status"] == "success"  # audit 不阻断
    assert (tool_dir / "bad.txt").is_file()
    violations = result.get("_security_violations") or []
    assert any("FILE_WRITE_DENY" in v for v in violations)


def test_enforce_blocks_tool_dir_write(guard_env, monkeypatch):
    monkeypatch.setenv("REVIEW_MODE", "enforce")
    tool_dir = guard_env / "tools" / "impl"
    tool_dir.mkdir(parents=True)

    proc = _run_tool(WRITE_TOOL_DIR, tool_dir, guard_env / "project")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["status"] == "failed"
    assert "FILE_WRITE_DENY" in result.get("message", "")
    assert not (tool_dir / "bad.txt").exists()


def test_user_home_write_is_allowed(guard_env, monkeypatch):
    monkeypatch.setenv("REVIEW_MODE", "enforce")
    tool_dir = guard_env / "tools" / "impl"
    tool_dir.mkdir(parents=True)

    proc = _run_tool(WRITE_USER_HOME, tool_dir, guard_env / "project")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["status"] == "success", result
    assert (guard_env / "SB-USER" / "0" / "data" / "ok.txt").is_file()
