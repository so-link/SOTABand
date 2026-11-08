"""覆盖层（fork）与私有优先读取的单元测试。

覆盖资源归属改造（2026-11）后的核心不变量，全部在 tmp_path 里搭两套假
资源根（公共系统层 + 私有），不碰真实的 SB-USER 目录：

- resource_roots() 私有在前、系统兜底，去重
- registry 合并：同 id 私有胜出，source 标注正确（private / system）
- fork_copy/drop_copy 的文件面行为与重复 fork 幂等
- content_hash 随上游（系统层）内容变化（上游更新检测的依据）
"""

import json

import pytest

from tests.conftest import run_coro

pytestmark = pytest.mark.unit


from core.resource import fork, paths


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """假公共（系统用户）根 + 假私有根。"""
    system = tmp_path / "system"
    private = tmp_path / "private"
    (system / "tools" / "definitions").mkdir(parents=True)
    (system / "tools" / "implementations" / "demo").mkdir(parents=True)
    private.mkdir()

    (system / "tools" / "definitions" / "demo.md").write_text("# 原版\n", encoding="utf-8")
    (system / "tools" / "implementations" / "demo" / "tool.py").write_text(
        "def execute(**kw):\n    return {'v': 'system'}\n", encoding="utf-8")
    (system / "tools" / "registry.json").write_text(
        json.dumps([{"id": "demo", "name": "演示工具", "param_meta": []}]), encoding="utf-8")

    # 种子包根指向不存在的临时子目录：默认情况下上游 = 系统层
    monkeypatch.setattr(paths, "BUILTIN_ROOT", tmp_path / "seed-nope")
    monkeypatch.setattr(paths, "private_root", lambda uid=None: private)
    monkeypatch.setattr(paths, "system_root", lambda: system)
    # registry 构造期用 ensure_private_dirs → 内部调 private_root()，已动态
    return system, private


def test_resource_roots_private_first(roots):
    system, private = roots
    r = paths.resource_roots()
    # 顺序：私有（覆盖层）→ 系统（公共兜底）
    assert r == [private, system]


def test_fork_copy_and_overlay_reads(roots):
    system, private = roots
    from core.resource.registry.tool_registry import ToolRegistry

    reg = ToolRegistry()
    entry = reg._read()[0] if len(reg._read()) == 1 else next(
        e for e in reg._read() if e["id"] == "demo")
    assert entry["source"] == "system"
    assert not fork.exists_in_private("tools", "demo")

    n = fork.fork_copy("tools", "demo")
    assert n == 2  # 定义 + 实现
    assert (private / "tools" / "definitions" / "demo.md").exists()
    assert (private / "tools" / "implementations" / "demo" / "tool.py").exists()

    # 修改私有副本 → 合并读取必须命中副本（覆盖层生效）
    (private / "tools" / "implementations" / "demo" / "tool.py").write_text(
        "def execute(**kw):\n    return {'v': 'mine'}\n", encoding="utf-8")
    run_coro(reg.update("demo", {"name": "我的副本"}))  # 生产 fork 后端点即如此落条目
    entry = next(e for e in reg._read() if e["id"] == "demo")
    assert entry["source"] == "private" and entry["name"] == "我的副本"
    assert fork.content_hash(private, "tools", "demo") != fork.content_hash(system, "tools", "demo")

    # 丢弃副本 → 回到公共层（与 discard 端点一致：删文件 + 删私有条目）
    fork.drop_copy("tools", "demo")
    data = [e for e in reg._read_private() if e.get("id") != "demo"]
    reg._write(data)
    assert not fork.exists_in_private("tools", "demo")
    entry = next(e for e in reg._read() if e["id"] == "demo")
    assert entry["source"] == "system"


def test_re_fork_resets_to_current_upstream(roots):
    """重复 fork 必须先清残留：旧副本的修改不能混进新副本。"""
    _system, private = roots
    fork.fork_copy("tools", "demo")
    p = private / "tools" / "definitions" / "demo.md"
    p.write_text("# 被我改过\n", encoding="utf-8")
    fork.fork_copy("tools", "demo")
    assert p.read_text(encoding="utf-8") == "# 原版\n"


def test_upstream_falls_back_to_seed_package(roots, tmp_path, monkeypatch):
    """系统层没有该资源时，上游回退到出厂种子包（未导入的极端场景）。"""
    system, _private = roots
    seed = tmp_path / "seed"
    (seed / "tools" / "definitions").mkdir(parents=True)
    (seed / "tools" / "definitions" / "legacy.md").write_text("# 种子\n", encoding="utf-8")
    monkeypatch.setattr(paths, "BUILTIN_ROOT", seed)

    assert fork.upstream_root("tools", "demo") == system
    assert fork.upstream_root("tools", "legacy") == seed
    assert fork.exists_in_upstream("tools", "legacy")
    assert not fork.exists_in_upstream("tools", "ghost")


def test_content_hash_detects_upstream_update(roots):
    system, _private = roots
    h0 = fork.content_hash(system, "tools", "demo")
    # 模拟系统身份升级公共层实现
    (system / "tools" / "implementations" / "demo" / "tool.py").write_text(
        "def execute(**kw):\n    return {'v': 'system-v2'}\n", encoding="utf-8")
    assert fork.content_hash(system, "tools", "demo") != h0


def test_fork_skips_venv(roots):
    """虚拟环境不是资源内容：不参与复制与哈希。"""
    system, _ = roots
    venv = system / "tools" / "implementations" / "demo" / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "spam.py").write_text("x=1\n", encoding="utf-8")
    files = fork.iter_resource_files(system, "tools", "demo")
    assert all(".venv" not in str(f) for f in files)


def test_update_strips_derived_source(roots):
    """source 是派生字段：update 以合并视图为基底 fork 条目时不能把它落盘。"""
    from core.resource.registry.tool_registry import ToolRegistry

    reg = ToolRegistry()
    run_coro(reg.update("demo", {"tags": ["x"]}))
    raw = json.loads((roots[1] / "tools" / "registry.json").read_text(encoding="utf-8"))
    assert "source" not in raw[0]
    assert raw[0]["tags"] == ["x"]


def test_unregister_scopes_keep_layers_independent(roots):
    """删除按层生效，公共与个人定制版互不牵连（覆盖层模型的独立性约定）。"""
    from core.resource.registry.tool_registry import ToolRegistry

    system, _private = roots
    reg = ToolRegistry()
    fork.fork_copy("tools", "demo")
    run_coro(reg.update("demo", {"name": "我的副本"}))

    # 删除个人层（我的副本）：公共条目必须原样保留
    run_coro(reg.unregister("demo", scope="private"))
    entries = [e for e in reg._read() if e["id"] == "demo"]
    assert len(entries) == 1 and entries[0]["source"] == "system"

    # 重新 fork 后删除公共层：个人条目必须原样保留
    fork.fork_copy("tools", "demo")
    run_coro(reg.update("demo", {"name": "我的副本"}))
    run_coro(reg.unregister("demo", scope="system"))
    raw_system = json.loads((system / "tools" / "registry.json").read_text(encoding="utf-8"))
    assert raw_system == []
    entries = [e for e in reg._read() if e["id"] == "demo"]
    assert len(entries) == 1 and entries[0]["source"] == "private"
    assert fork.exists_in_private("tools", "demo")

    # scope=all：两层全清（彻底清除 id 的场景）
    run_coro(reg.unregister("demo", scope="all"))
    assert [e for e in reg._read() if e["id"] == "demo"] == []
