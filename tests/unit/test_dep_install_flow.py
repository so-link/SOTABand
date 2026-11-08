"""沙箱自动调试的依赖安装闭环测试。

覆盖线上问题：工具带新的外部依赖时，点「自动调试」会反复报缺失依赖、
永远无法通过，工具注册不了。根因有四个，这里逐条锁死：

1. 缺依赖识别不到：生成的工具代码强制带 ``try/except Exception``，import 写在
   ``execute()`` 里时异常被吞，只剩 message 里一句 ``No module named 'x'``，
   而旧实现只认 ``error == "ModuleNotFoundError"`` → 既不安装也不终止。
2. 安装名用错：自动调试传进去的是 import 名（cv2 / PIL / sklearn），旧实现直接
   ``pip install cv2`` → PyPI 没这个名字 → 必然失败。
3. LLM 给出的 ``adjusted_command`` 是整条命令，被当成依赖名拼成
   ``pip install "pip install xxx --no-deps"`` → 重试必然失败。
4. venv 探测只查 ``bin/python``，Windows 上是 ``Scripts/python.exe`` → 永远
   回退到 sys.executable（服务器上常因无写权限装不上）。
"""

import sys

import pytest

from core.deps import (
    build_pip_argv,
    dist_name_for,
    dist_name_from_spec,
    extract_declared_deps,
    extract_missing_module,
    import_name_for,
    pip_name_for,
)
from core.executor.tool_executor import ToolExecutor
from core.resource import paths
from core.resource.builder import tool_builder as tb

# ── ① 缺依赖识别 ─────────────────────────────────────────

def test_extract_missing_module_from_wrapper_error():
    """模块级 import：包装器直接给出 error / missing_module。"""
    assert extract_missing_module({"error": "ModuleNotFoundError", "missing_module": "cv2"}) == "cv2"


def test_extract_missing_module_from_swallowed_exception():
    """函数内 import 被工具自身 try/except 吞掉：只能从 message 里捞。"""
    payload = {"status": "failed", "message": "工具执行失败: No module named 'fitz'"}
    assert extract_missing_module(payload) == "fitz"


def test_extract_missing_module_from_stderr_and_submodule():
    stderr = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'pymysql.cursors'"
    assert extract_missing_module(None, stderr) == "pymysql"


def test_extract_missing_module_none_when_no_dep_issue():
    assert extract_missing_module({"status": "failed", "message": "值错误：n 必须为正"}) == ""
    assert extract_missing_module(None, "") == ""


# ── ② 安装名 / 探测名 ────────────────────────────────────

def test_pip_and_import_name_mapping():
    assert pip_name_for("cv2") == "opencv-python"
    assert pip_name_for("PIL") == "pillow"
    assert pip_name_for("Crypto") == "pycryptodome"
    assert pip_name_for("fitz") == "pymupdf"
    # 已经是 pip 包名 → 原样返回
    assert pip_name_for("opencv-python") == "opencv-python"
    # 探测用 import 名（反查）
    assert import_name_for("opencv-python") == "cv2"
    assert import_name_for("opencv-python>=4.8") == "cv2"


# ── ③ pip 命令拼装 ───────────────────────────────────────

def test_build_pip_argv_plain_dep():
    # --prefer-binary：优先用 wheel，避免在精简系统/容器里为 Pillow / greenlet
    # 这类 C 扩展现场编译（缺 gcc、libjpeg 头文件时必然失败）
    assert build_pip_argv(["py", "-m", "pip"], "opencv-python") == [
        "py", "-m", "pip", "install", "--prefer-binary", "opencv-python",
    ]


def test_build_pip_argv_accepts_full_command_from_llm():
    argv = build_pip_argv(["py", "-m", "pip"], "cv2", "pip install opencv-python --no-deps")
    assert argv == [
        "py", "-m", "pip", "install", "--prefer-binary", "opencv-python", "--no-deps",
    ]


def test_build_pip_argv_respects_explicit_source_build():
    """LLM 明确要求从源码构建时，不能再强加 --prefer-binary。"""
    for flag in ("--no-binary :all:", "--only-binary :none:"):
        argv = build_pip_argv(["py", "-m", "pip"], "x", f"pip install {flag} x")
        assert "--prefer-binary" not in argv, argv


def test_build_pip_argv_with_options_only():
    argv = build_pip_argv(["py", "-m", "pip"], "cv2", "--no-deps opencv-python")
    assert argv[:4] == ["py", "-m", "pip", "install"]
    assert "--no-deps" in argv and "opencv-python" in argv


# ── ④ venv 探测跨平台 ────────────────────────────────────

@pytest.fixture()
def fake_root(tmp_path, monkeypatch):
    root = tmp_path / "resources"
    (root / "tools" / "implementations").mkdir(parents=True)
    monkeypatch.setattr(paths, "resource_roots", lambda: [root])
    return root


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def test_python_exe_finds_windows_tool_venv(fake_root):
    exe = fake_root / "tools" / "implementations" / "t1" / ".venv" / "Scripts" / "python.exe"
    _touch(exe)
    assert ToolExecutor._get_python_exe("t1") == str(exe)


def test_python_exe_finds_posix_tool_venv(fake_root):
    exe = fake_root / "tools" / "implementations" / "t2" / ".venv" / "bin" / "python"
    _touch(exe)
    assert ToolExecutor._get_python_exe("t2") == str(exe)


# ── ⑤ 自动调试：装失败即终止，不再让 LLM 兜圈 ─────────────

@pytest.mark.asyncio
async def test_auto_debug_blocks_when_dep_install_fails(monkeypatch):
    monkeypatch.setattr(tb, "is_debug_running", lambda _tid: True)

    async def _write_log(*args, **kwargs):
        return None

    async def _fake_install(self, tool_id, deps, stop_event=None, as_pip_names=False):
        dep = list(deps)[0]
        yield {"event": "deps_start", "deps": list(deps), "env": "project_venv"}
        yield {"event": "dep_installing", "dep": dep, "package": pip_name_for(dep)}
        yield {"event": "dep_failed", "dep": dep, "env": "project_venv",
               "reason": "ERROR: No matching distribution found for opencv-python"}

    async def _never_execute(self, *args, **kwargs):   # 不应被执行到
        raise AssertionError("依赖安装失败后不应继续执行工具")

    monkeypatch.setattr(tb.ToolCodeBuilder, "_write_debug_log", _write_log, raising=False)
    monkeypatch.setattr(tb.ToolCodeBuilder, "install_deps_stream", _fake_install)
    monkeypatch.setattr(tb.ToolCodeBuilder, "sandbox_execute", _never_execute, raising=False)

    builder = tb.ToolCodeBuilder(llm_client=object())
    code = "import cv2\n\ndef execute(**kwargs):\n    return {'status':'success'}\n"

    events = []
    async for ev in builder._auto_debug_loop("md", code, {}, "t1", 3, None):
        events.append(ev)

    kinds = [e.get("event") for e in events]
    assert "debug_blocked" in kinds
    assert "thinking" not in kinds          # 没有走到 LLM 修代码
    blocked = next(e for e in events if e.get("event") == "debug_blocked")
    assert blocked["dep"] == "cv2"
    assert "pip install opencv-python" in blocked["message"]


@pytest.mark.asyncio
async def test_auto_debug_blocks_on_shadow_package_conflict(monkeypatch):
    """模块被另一个同名发行版占用时也必须终止，并给出卸载指引。

    真实案例：PyPI 上的 `barcode` 是个 DNA 序列工具（依赖 Levenshtein），
    真正的条码库叫 `python-barcode`，两者都提供 `barcode` 模块。只看
    `import barcode` 会误判"已装"，于是跳过安装、工具继续报 AttributeError。
    """
    monkeypatch.setattr(tb, "is_debug_running", lambda _tid: True)

    async def _write_log(*args, **kwargs):
        return None

    async def _fake_install(self, tool_id, deps, stop_event=None, as_pip_names=False):
        dep = list(deps)[0]
        yield {"event": "deps_start", "deps": list(deps), "env": "project_venv"}
        yield {
            "event": "dep_conflict",
            "dep": dep,
            "package": pip_name_for(dep),
            "module": dep,
            "providers": ["barcode"],
            "message": "模块 barcode 已被 barcode 提供，而这里需要的是 python-barcode。"
                       "请先执行 pip uninstall -y barcode 后重试。",
        }

    async def _never_execute(self, *args, **kwargs):
        raise AssertionError("依赖冲突后不应继续执行工具")

    monkeypatch.setattr(tb.ToolCodeBuilder, "_write_debug_log", _write_log, raising=False)
    monkeypatch.setattr(tb.ToolCodeBuilder, "install_deps_stream", _fake_install)
    monkeypatch.setattr(tb.ToolCodeBuilder, "sandbox_execute", _never_execute, raising=False)

    builder = tb.ToolCodeBuilder(llm_client=object())
    code = "import barcode\n\ndef execute(**kwargs):\n    return {'status':'success'}\n"

    events = [ev async for ev in builder._auto_debug_loop("md", code, {}, "t3", 3, None)]
    kinds = [e.get("event") for e in events]
    assert "debug_blocked" in kinds
    blocked = next(e for e in events if e.get("event") == "debug_blocked")
    assert "pip uninstall -y barcode" in blocked["message"]


MD_WITH_DEPS = """## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| barcode | >=1.0 | DNA 序列工具（import barcode） |
| (内置) core.llm | - | 系统统一 LLM |
| python | >=3.9 | 运行环境 |
| math | 内建 | 圆周率 |
| numpy>=1.26 | - | 数值计算 |

## 5. 运行机制

| 参数名 | 类型 |
|--------|------|
| content | string |
"""


def test_extract_declared_deps_keeps_pip_names_and_filters_builtin():
    """MD 依赖表是 pip 包名，必须原样保留（`barcode` 就是 DNA 那个包）。"""
    assert extract_declared_deps(MD_WITH_DEPS) == ["barcode", "numpy>=1.26"]
    assert extract_declared_deps("") == []
    assert extract_declared_deps("## 1. 功能概述\n\n无") == []


def test_dist_name_from_spec_does_not_map_import_names():
    """MD 已写明包名时不能再映射：barcode ≠ python-barcode。"""
    assert dist_name_from_spec("barcode") == "barcode"
    assert dist_name_from_spec("numpy>=1.26") == "numpy"
    assert dist_name_from_spec("python-barcode") == "python-barcode"


@pytest.mark.asyncio
async def test_install_stream_as_pip_names_installs_exact_name(monkeypatch):
    """as_pip_names=True → 直接 pip install 表里写的名字，不做 import→pip 映射。"""
    builder = tb.ToolCodeBuilder(llm_client=object())
    monkeypatch.setattr(builder, "_get_exec_python", lambda _tid: sys.executable)
    monkeypatch.setattr(
        tb.ToolCodeBuilder, "_probe_dep_state",
        staticmethod(lambda exe, mod, dist: {
            "dist_version": None, "import_ok": False, "providers": [],
        }),
    )

    captured: list[list[str]] = []

    def _fake_build_argv(pip_base, dep, adjusted=None):
        captured.append([dep, adjusted])
        return [sys.executable, "-c", "print('fake pip ok')"]

    monkeypatch.setattr(tb, "build_pip_argv", _fake_build_argv)

    events = [e async for e in builder.install_deps_stream(
        "t8", ["barcode"], as_pip_names=True,
    )]
    assert captured and captured[0][0] == "barcode"     # 没被映射成 python-barcode
    assert [e.get("event") for e in events][-1] == "deps_done"


@pytest.mark.asyncio
async def test_auto_debug_installs_declared_deps_before_guessing(monkeypatch):
    """先按 MD 声明装（as_pip_names=True），再补 AST 里未覆盖的 import。"""
    monkeypatch.setattr(tb, "is_debug_running", lambda _tid: True)

    calls: list[tuple[list[str], bool]] = []

    async def _write_log(*args, **kwargs):
        return None

    async def _fake_install(self, tool_id, deps, stop_event=None, as_pip_names=False):
        calls.append((list(deps), as_pip_names))
        for dep in deps:
            yield {"event": "dep_installed", "dep": dep}

    async def _ok_execute(self, code, test_input, tool_id=None, exec_timeout=None, owner_id=None):
        return {"exit_code": 0, "stdout": '{"status":"success"}', "stderr": "", "success": True}

    monkeypatch.setattr(tb.ToolCodeBuilder, "_write_debug_log", _write_log, raising=False)
    monkeypatch.setattr(tb.ToolCodeBuilder, "install_deps_stream", _fake_install)
    monkeypatch.setattr(tb.ToolCodeBuilder, "sandbox_execute", _ok_execute, raising=False)

    builder = tb.ToolCodeBuilder(llm_client=object())
    code = "import barcode\nimport requests\n\ndef execute(**kwargs):\n    return {'status':'success'}\n"
    md = "## 4. 依赖环境\n\n| 依赖 | 版本 | 用途 |\n|---|---|---|\n| barcode | - | DNA |\n"

    async for _ in builder._auto_debug_loop(md, code, {}, "t7", 2, None):
        pass

    assert calls[0] == (["barcode"], True)                  # MD 声明优先，且不做映射
    assert calls[1] == (["requests"], False)                # requests 未被声明 → 映射兜底


def test_dist_name_for_strips_version_and_extras():
    assert dist_name_for("numpy>=1.26") == "numpy"
    assert dist_name_for("opencv-python>=4.8") == "opencv-python"
    assert dist_name_for("cv2") == "opencv-python"
    assert dist_name_for("Crypto") == "pycryptodome"
    assert dist_name_for("python-barcode") == "python-barcode"


def test_probe_dep_state_installed():
    """已装的发行版：dist_version 有值。"""
    state = tb.ToolCodeBuilder._probe_dep_state(sys.executable, "pytest", "pytest")
    assert state["dist_version"]
    assert state["import_ok"] is True


def test_probe_dep_state_shadow_package():
    """模块能 import，但发行版不对 → 影子包冲突（用 numpy 模拟 barcode 的处境）。"""
    state = tb.ToolCodeBuilder._probe_dep_state(sys.executable, "numpy", "barcode")
    assert state["dist_version"] is None
    assert state["import_ok"] is True
    assert "numpy" in state["providers"]


def test_probe_dep_state_missing():
    state = tb.ToolCodeBuilder._probe_dep_state(
        sys.executable, "zzz_nonexistent_module_xyz", "zzz-nonexistent-dist"
    )
    assert state["dist_version"] is None
    assert state["import_ok"] is False


@pytest.mark.asyncio
async def test_install_stream_reports_conflict_without_installing(monkeypatch):
    """影子包（模块能 import 但发行版不对）→ 只发 dep_conflict，不走 pip。"""
    builder = tb.ToolCodeBuilder(llm_client=object())
    monkeypatch.setattr(builder, "_get_exec_python", lambda _tid: sys.executable)
    monkeypatch.setattr(
        tb.ToolCodeBuilder,
        "_probe_dep_state",
        staticmethod(lambda exe, mod, dist: {
            "dist_version": None, "import_ok": True, "providers": ["barcode"],
        }),
    )

    events = [e async for e in builder.install_deps_stream("t9", ["barcode"])]
    kinds = [e.get("event") for e in events]
    assert "dep_conflict" in kinds
    assert "dep_installing" not in kinds      # 不自动覆盖安装
    conflict = next(e for e in events if e.get("event") == "dep_conflict")
    assert "pip uninstall -y barcode" in conflict["message"]


@pytest.mark.asyncio
async def test_auto_debug_survives_install_success_and_reports_exec(monkeypatch):
    """依赖装成功后继续执行；执行成功则直接 done。"""
    monkeypatch.setattr(tb, "is_debug_running", lambda _tid: True)

    async def _write_log(*args, **kwargs):
        return None

    async def _fake_install(self, tool_id, deps, stop_event=None, as_pip_names=False):
        for dep in deps:
            yield {"event": "dep_installing", "dep": dep}
            yield {"event": "dep_installed", "dep": dep, "env": "project_venv"}

    async def _ok_execute(self, code, test_input, tool_id=None, exec_timeout=None, owner_id=None):
        return {
            "exit_code": 0,
            "stdout": '{"status":"success","output_format":"text","message":"ok","data":{}}',
            "stderr": "",
            "success": True,
        }

    monkeypatch.setattr(tb.ToolCodeBuilder, "_write_debug_log", _write_log, raising=False)
    monkeypatch.setattr(tb.ToolCodeBuilder, "install_deps_stream", _fake_install)
    monkeypatch.setattr(tb.ToolCodeBuilder, "sandbox_execute", _ok_execute, raising=False)

    builder = tb.ToolCodeBuilder(llm_client=object())
    code = "import requests\n\ndef execute(**kwargs):\n    return {'status':'success'}\n"

    events = [ev async for ev in builder._auto_debug_loop("md", code, {}, "t2", 3, None)]
    kinds = [e.get("event") for e in events]
    assert "debug_blocked" not in kinds
    assert kinds[-1] == "done"

