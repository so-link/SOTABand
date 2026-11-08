"""工具资源双根查找优先级测试。

覆盖历史回归（引入于 b5cb0eb）：详情类读取被改成「内置优先」，而写入一律
落私有根，导致「同 id 同时存在于内置与私有」的工具永远读到内置那份旧文件
—— 表现为「改了代码 / MD，重新打开还是旧的」，且沙箱测试通过、实际执行
却跑内置旧代码。

约定（本测试锁定）：
- ``paths.resource_roots()`` = 内置在前（注册表合并依赖「后者覆盖前者」）
- ``paths.read_roots()``      = 私有在前（定位实际文件用）
"""

import json

import pytest

from core.executor.tool_executor import ToolExecutor
from core.resource import paths
from core.resource.registry import tool_registry


@pytest.fixture()
def two_roots(tmp_path, monkeypatch):
    """把内置根、私有根都指向 tmp，并各放一份同 id 的工具。"""
    builtin = tmp_path / "resources"
    private = tmp_path / "resources-private"
    monkeypatch.setattr(paths, "BUILTIN_ROOT", builtin)
    monkeypatch.setattr(paths, "PRIVATE_ROOT", private)

    for root, code, spec in (
        (builtin, "BUILTIN_CODE = 1\n", "# 内置版"),
        (private, "PRIVATE_CODE = 1\n", "# 私有版"),
    ):
        impl = root / "tools" / "implementations" / "dup-tool"
        impl.mkdir(parents=True)
        (impl / "tool.py").write_text(code, encoding="utf-8")
        defs = root / "tools" / "definitions"
        defs.mkdir(parents=True, exist_ok=True)
        (defs / "dup-tool.md").write_text(spec, encoding="utf-8")

    return builtin, private


def test_root_order_invariants(two_roots):
    builtin, private = two_roots
    assert paths.resource_roots() == [builtin, private], "合并顺序必须保持内置在前"
    assert paths.read_roots() == [private, builtin], "定位文件必须私有在前"


def test_registry_finds_private_copy_first(two_roots):
    reg = tool_registry.ToolRegistry()

    assert reg.read_code_text("dup-tool").startswith("PRIVATE_CODE")
    assert reg.read_def_text("dup-tool")[0] == "# 私有版"
    assert reg.find_impl_dir("dup-tool") == two_roots[1] / "tools" / "implementations" / "dup-tool"
    assert reg.find_def_file("dup-tool") == two_roots[1] / "tools" / "definitions" / "dup-tool.md"


def test_falls_back_to_builtin_when_private_has_no_copy(two_roots):
    builtin, private = two_roots
    reg = tool_registry.ToolRegistry()

    # 私有根删掉实现 → 应回落到内置，而不是返回空
    import shutil
    shutil.rmtree(private / "tools" / "implementations" / "dup-tool")
    (private / "tools" / "definitions" / "dup-tool.md").unlink()

    assert reg.read_code_text("dup-tool").startswith("BUILTIN_CODE")
    assert reg.read_def_text("dup-tool")[0] == "# 内置版"
    assert reg.find_impl_dir("dup-tool") == builtin / "tools" / "implementations" / "dup-tool"


def test_executor_resolves_private_copy_first(two_roots):
    _builtin, private = two_roots

    assert ToolExecutor._tool_space_dirs()[0] == private / "tools"
    assert ToolExecutor._resolve_impl_dir("dup-tool") == (
        private / "tools" / "implementations" / "dup-tool"
    )


def test_registry_merge_still_private_overrides_builtin(two_roots):
    builtin, private = two_roots
    for root, version in ((builtin, "0.1.0"), (private, "0.2.0")):
        (root / "tools").mkdir(parents=True, exist_ok=True)
        (root / "tools" / "registry.json").write_text(
            json.dumps([{"id": "dup-tool", "name": "dup", "version": version}]),
            encoding="utf-8",
        )

    merged = {e["id"]: e for e in tool_registry.iter_tool_entries()}
    assert merged["dup-tool"]["version"] == "0.2.0", "私有条目必须覆盖内置条目"


def test_resource_roots_unchanged_when_private_equals_builtin(monkeypatch):
    """私有根 == 内置根（单根部署）时不应重复出现。"""
    only = paths.BUILTIN_ROOT
    monkeypatch.setattr(paths, "PRIVATE_ROOT", only)
    assert paths.resource_roots() == [only]
    assert paths.read_roots() == [only]
