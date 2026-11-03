"""必填参数的平台兜底校验 + 界面切换必填的实现测试。

背景：历史生成 prompt 没禁止模型自己写「缺少必填参数」校验，内置工具里约
一半在 tool.py 内部硬编码拦截，导致「界面把参数改成可选」不生效。现在改为
平台在执行前按 registry 的 param_meta 统一拦截，生成侧禁止自写校验。
"""

import json

import pytest

from core.executor.tool_executor import ToolExecutor
from core.resource import param_guard, paths, spec_param_meta, tool_usage


def _make_root(tmp_path, monkeypatch, entries):
    root = tmp_path / "resources"
    (root / "tools").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "registry.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(paths, "resource_roots", lambda: [root])
    return root


# ── 缺失口径 ──────────────────────────────────────────────

def test_is_missing_semantics():
    assert param_guard.is_missing(None) is True
    assert param_guard.is_missing("") is True
    assert param_guard.is_missing("   ") is True
    # 有意义的假值不算缺失
    assert param_guard.is_missing(0) is False
    assert param_guard.is_missing(False) is False
    assert param_guard.is_missing([]) is False


def test_missing_required_params_filters_mode_param_and_modes():
    meta = [
        {"name": "mode", "required": True},
        {"name": "a", "required": True},
        {"name": "b", "required": False},
        {"name": "c", "required": True, "mode": "train"},
    ]
    assert [p["name"] for p in param_guard.missing_required_params(meta, {})] == ["a", "c"]
    # 当前模式为 test 时，train 专属参数不参与校验
    assert [p["name"] for p in param_guard.missing_required_params(meta, {}, "test")] == ["a"]
    assert param_guard.missing_required_params(meta, {"a": "x"}, "test") == []


# ── 执行前校验 ────────────────────────────────────────────

def test_check_params_skips_unregistered_tool(tmp_path, monkeypatch):
    _make_root(tmp_path, monkeypatch, [])
    assert param_guard.check_params("not-registered", {}) is None


def test_check_params_reports_missing(tmp_path, monkeypatch):
    _make_root(tmp_path, monkeypatch, [{
        "id": "t1", "name": "工具1",
        "param_meta": [{"name": "city", "required": True}, {"name": "n", "required": False}],
    }])
    ok = param_guard.check_params("t1", {"city": "北京"})
    assert ok is None

    bad = param_guard.check_params("t1", {"city": "   "})
    assert bad is not None
    assert bad["status"] == "failed"
    assert "city" in bad["message"]
    assert bad["missing_params"] == ["city"]


def test_check_params_respects_enforce_required_switch(tmp_path, monkeypatch):
    _make_root(tmp_path, monkeypatch, [{
        "id": "t2", "name": "工具2", "enforce_required": False,
        "param_meta": [{"name": "city", "required": True}],
    }])
    assert param_guard.check_params("t2", {}) is None


@pytest.mark.asyncio
async def test_executor_short_circuits_before_spawning(tmp_path, monkeypatch):
    """缺参时不得启动子进程：_build_script 一旦被调用就说明守卫失效。"""
    _make_root(tmp_path, monkeypatch, [{
        "id": "t3", "name": "工具3",
        "param_meta": [{"name": "city", "required": True}],
    }])

    def _boom(*args, **kwargs):
        raise AssertionError("缺参时不应当构建/执行子进程")

    monkeypatch.setattr(ToolExecutor, "_build_script", staticmethod(_boom))

    result = await ToolExecutor.execute("t3", {})
    assert result["status"] == "failed"
    assert "缺少必填参数" in result["message"]


# ── MD 必填列定点改写 ─────────────────────────────────────

MD = """---
id: demo-tool
name: 演示工具
---

# 演示工具

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| city   | string | 是 | 无     | 城市名 |
| n      | integer | 否 | 5     | 数量 |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
"""


def test_set_param_required_changes_only_target_cell():
    new_md, changed = spec_param_meta.set_param_required(MD, "city", False)

    assert changed == 1
    # 只有一行变化，且表格对齐没有被重排
    old_lines, new_lines = MD.split("\n"), new_md.split("\n")
    assert len(old_lines) == len(new_lines)
    diffs = [(a, b) for a, b in zip(old_lines, new_lines) if a != b]
    assert diffs == [("| city   | string | 是 | 无     | 城市名 |",
                      "| city   | string | 否 | 无     | 城市名 |")]

    back, _ = spec_param_meta.set_param_required(new_md, "city", True)
    assert back == MD


def test_set_param_required_missing_param_returns_unchanged():
    new_md, changed = spec_param_meta.set_param_required(MD, "nope", False)
    assert changed == 0
    assert new_md == MD


def test_sync_param_meta_keeps_llm_hints():
    old = [{"name": "city", "type": "string", "required": True, "hints": ["北京", "上海"]},
           {"name": "gone", "type": "string", "required": True}]
    new_md, _ = spec_param_meta.set_param_required(MD, "city", False)

    params = spec_param_meta.sync_param_meta(new_md, old)
    by_name = {p["name"]: p for p in params}
    assert by_name["city"]["required"] is False        # 以 MD 为准
    assert by_name["city"]["hints"] == ["北京", "上海"]  # 保留 LLM 抽取的提示
    assert "gone" not in by_name                        # MD 里已没有的参数被丢弃


def test_preserve_manual_required_only_when_param_set_matches():
    old = [{"name": "city", "required": False}, {"name": "n", "required": True}]
    same = [{"name": "city", "required": True}, {"name": "n", "required": True}]
    assert [p["required"] for p in spec_param_meta.preserve_manual_required(same, old)] == [False, True]

    # 参数集合变了 → 以新结果为准，不继承人工值
    changed = [{"name": "city", "required": True}, {"name": "new_param", "required": False}]
    assert [p["required"] for p in spec_param_meta.preserve_manual_required(changed, old)] == [True, False]


# ── 引用体检 ──────────────────────────────────────────────

def test_scan_references_covers_agents_and_tools(tmp_path, monkeypatch):
    root = tmp_path / "resources"
    (root / "tools" / "registry.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "tools" / "registry.json").write_text(json.dumps([
        {"id": "target-tool", "name": "目标工具", "param_meta": []},
        {"id": "user-tool", "name": "调用方工具", "param_meta": []},
    ], ensure_ascii=False), encoding="utf-8")
    (root / "agents" / "definitions").mkdir(parents=True)
    (root / "tools" / "definitions").mkdir(parents=True)
    (root / "tools" / "implementations" / "user-tool").mkdir(parents=True)

    (root / "agents" / "definitions" / "demo-agent.md").write_text(
        "---\nid: demo-agent\nname: 演示 Agent\nrole: task\n---\n\n"
        "# 演示\n\n```flow\n"
        '[{"step": 1, "tool": "target-tool", "params": {"city": "{{input.city}}"}}]\n'
        "```\n", encoding="utf-8")

    (root / "tools" / "definitions" / "user-tool.md").write_text(
        "---\nid: user-tool\nname: 调用方工具\n---\n\n正文引用【【目标工具】】\n", encoding="utf-8")
    # 每个 tool.py 都有 def _call_tool(...)，扫描必须只认调用
    (root / "tools" / "implementations" / "user-tool" / "tool.py").write_text(
        "def _call_tool(tool_name, **params):\n    return {}\n\n"
        "def execute(**kwargs):\n"
        "    return _call_tool(\"目标工具\", city=kwargs.get(\"city\"))\n", encoding="utf-8")

    monkeypatch.setattr(paths, "resource_roots", lambda: [root])

    refs = tool_usage.scan_references("target-tool", "目标工具", "city")
    assert [a["id"] for a in refs["agents"]] == ["demo-agent"]
    assert refs["agents"][0]["passes_param"] is True
    assert [t["id"] for t in refs["tools"]] == ["user-tool"]
    assert refs["tools"][0]["via"] == ["code", "text"]
    assert refs["total"] == 2
