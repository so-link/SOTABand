"""覆盖层（fork）与私有优先读取的单元测试。

覆盖 2026-11-04 方案 C 的核心不变量，全部在 tmp_path 里搭两套假资源根
（内置 + 私有），不碰真实的 resources/ 与 USER_HOME：

- resource_roots() 私有在前
- registry 合并：同 id 私有胜出，source 标注正确
- fork_copy/drop_copy 的文件面行为与重复 fork 幂等
- content_hash 随内置内容变化（上游更新检测的依据）
"""

import json

import pytest

from core.resource import fork, paths


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """假内置根 + 假私有根。"""
    builtin = tmp_path / "builtin"
    private = tmp_path / "private"
    (builtin / "tools" / "definitions").mkdir(parents=True)
    (builtin / "tools" / "implementations" / "demo").mkdir(parents=True)
    private.mkdir()

    (builtin / "tools" / "definitions" / "demo.md").write_text("# 原版\n", encoding="utf-8")
    (builtin / "tools" / "implementations" / "demo" / "tool.py").write_text(
        "def execute(**kw):\n    return {'v': 'builtin'}\n", encoding="utf-8")
    (builtin / "tools" / "registry.json").write_text(
        json.dumps([{"id": "demo", "name": "演示工具", "param_meta": []}]), encoding="utf-8")

    monkeypatch.setattr(paths, "BUILTIN_ROOT", builtin)
    monkeypatch.setattr(paths, "private_root", lambda uid=None: private)
    # 共享根（超管发布、全员可用）同样指向临时目录，避免读到真实 USER_HOME
    shared = tmp_path / "shared"
    monkeypatch.setattr(paths, "shared_root", lambda: shared)
    # registry 构造期用 ensure_private_dirs → 内部调 private_root()，已动态
    return builtin, private


def test_resource_roots_private_first(roots, tmp_path):
    builtin, private = roots
    r = paths.resource_roots()
    # 顺序：私有（覆盖层）→ 共享（超管发布）→ 内置（兜底）
    assert r[0] == private and r[1] == tmp_path / "shared" and r[2] == builtin


def test_fork_copy_and_overlay_reads(roots):
    builtin, private = roots
    from core.resource.registry.tool_registry import ToolRegistry

    reg = ToolRegistry()
    entry = reg._read()[0] if len(reg._read()) == 1 else next(
        e for e in reg._read() if e["id"] == "demo")
    assert entry["source"] == "builtin"
    assert not fork.exists_in_private("tools", "demo")

    n = fork.fork_copy("tools", "demo")
    assert n == 2  # 定义 + 实现
    assert (private / "tools" / "definitions" / "demo.md").exists()
    assert (private / "tools" / "implementations" / "demo" / "tool.py").exists()

    # 修改私有副本 → 合并读取必须命中副本（覆盖层生效）
    (private / "tools" / "implementations" / "demo" / "tool.py").write_text(
        "def execute(**kw):\n    return {'v': 'mine'}\n", encoding="utf-8")
    import asyncio
    asyncio.run(reg.update("demo", {"name": "我的副本"}))  # 生产 fork 后端点即如此落条目
    entry = next(e for e in reg._read() if e["id"] == "demo")
    assert entry["source"] == "private" and entry["name"] == "我的副本"
    assert fork.content_hash(private, "tools", "demo") != fork.content_hash(builtin, "tools", "demo")

    # 丢弃副本 → 回到内置（与 discard 端点一致：删文件 + 删私有条目）
    fork.drop_copy("tools", "demo")
    data = [e for e in reg._read_private() if e.get("id") != "demo"]
    reg._write(data)
    assert not fork.exists_in_private("tools", "demo")
    entry = next(e for e in reg._read() if e["id"] == "demo")
    assert entry["source"] == "builtin"


def test_re_fork_resets_to_current_builtin(roots):
    """重复 fork 必须先清残留：旧副本的修改不能混进新副本。"""
    builtin, private = roots
    fork.fork_copy("tools", "demo")
    p = private / "tools" / "definitions" / "demo.md"
    p.write_text("# 被我改过\n", encoding="utf-8")
    fork.fork_copy("tools", "demo")
    assert p.read_text(encoding="utf-8") == "# 原版\n"


def test_content_hash_detects_builtin_update(roots):
    builtin, private = roots
    h0 = fork.content_hash(builtin, "tools", "demo")
    # 模拟 git pull 升级内置实现
    (builtin / "tools" / "implementations" / "demo" / "tool.py").write_text(
        "def execute(**kw):\n    return {'v': 'builtin-v2'}\n", encoding="utf-8")
    assert fork.content_hash(builtin, "tools", "demo") != h0


def test_fork_skips_venv(roots):
    """虚拟环境不是资源内容：不参与复制与哈希。"""
    builtin, _ = roots
    venv = builtin / "tools" / "implementations" / "demo" / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "spam.py").write_text("x=1\n", encoding="utf-8")
    files = fork.iter_resource_files(builtin, "tools", "demo")
    assert all(".venv" not in str(f) for f in files)


def test_update_strips_derived_source(roots):
    """source 是派生字段：update 以合并视图为基底 fork 条目时不能把它落盘。"""
    from core.resource.registry.tool_registry import ToolRegistry

    reg = ToolRegistry()
    import asyncio
    asyncio.run(reg.update("demo", {"tags": ["x"]}))
    raw = json.loads((roots[1] / "tools" / "registry.json").read_text(encoding="utf-8"))
    assert "source" not in raw[0]
    assert raw[0]["tags"] == ["x"]


def test_unregister_scopes_keep_layers_independent(roots):
    """删除按层生效，内置与个人定制版互不牵连（覆盖层模型的独立性约定）。"""
    import asyncio

    from core.resource.registry.tool_registry import ToolRegistry

    builtin, private = roots
    reg = ToolRegistry()
    fork.fork_copy("tools", "demo")
    asyncio.run(reg.update("demo", {"name": "我的副本"}))

    # 删除个人层（我的副本）：内置条目必须原样保留
    asyncio.run(reg.unregister("demo", scope="private"))
    entries = [e for e in reg._read() if e["id"] == "demo"]
    assert len(entries) == 1 and entries[0]["source"] == "builtin"

    # 重新 fork 后删除内置层：个人条目必须原样保留
    fork.fork_copy("tools", "demo")
    asyncio.run(reg.update("demo", {"name": "我的副本"}))
    asyncio.run(reg.unregister("demo", scope="builtin"))
    raw_builtin = json.loads((builtin / "tools" / "registry.json").read_text(encoding="utf-8"))
    assert raw_builtin == []
    entries = [e for e in reg._read() if e["id"] == "demo"]
    assert len(entries) == 1 and entries[0]["source"] == "private"
    assert fork.exists_in_private("tools", "demo")

    # scope=all：两层全清（彻底清除 id 的场景）
    asyncio.run(reg.unregister("demo", scope="all"))
    assert [e for e in reg._read() if e["id"] == "demo"] == []
