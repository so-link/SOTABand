"""数据集精确查找回归测试（api-data-get + Agent _pick_dataset_id）。

核心原则：宁可追问用户，绝不替用户猜数据集。
数据处理工具一旦模糊匹配猜错数据集，会用错数据产出错误结果还报告成功——
这是不可接受的失败模式。因此相似/片段名称一律不命中，重名返回歧义信号。

全部用 monkeypatch 隔离注册表读取，不碰真实用户目录。
"""

import pytest

pytestmark = pytest.mark.unit


# ── ApiDataGet.call() ──────────────────────────────────────────────

@pytest.fixture
def api_data(monkeypatch):
    """把 _load_visible_datasets 替换为可控的假数据。"""
    from core.api.implementations import api_data

    state = {"visible": [], "all": []}
    monkeypatch.setattr(
        api_data, "_load_visible_datasets",
        lambda: (state["visible"], state["all"], "u1"),
    )
    return api_data, state


def _ds(id, name, owner="u1"):
    return {"id": id, "name": name, "data_path": f"/data/{id}", "owner": owner}


def test_get_by_name_unique(api_data):
    mod, state = api_data
    state["visible"] = [_ds("rawgouqi_1795340961", "rawgouqi")]
    r = mod.ApiDataGet.call(name="rawgouqi")
    assert r["dataset"]["id"] == "rawgouqi_1795340961"
    assert "error" not in r


def test_get_by_id_after_agent_resolution(api_data):
    """Agent 把 name 解析成 id 后传给工具，工具拿 id 必须能查到。"""
    mod, state = api_data
    state["visible"] = [_ds("rawgouqi_1795340961", "rawgouqi")]
    r = mod.ApiDataGet.call(name="rawgouqi_1795340961")
    assert r["dataset"]["id"] == "rawgouqi_1795340961"


def test_get_by_name_ambiguous_returns_candidates(api_data):
    """重名不静默取第一个，返回候选让上层追问。"""
    mod, state = api_data
    state["visible"] = [
        _ds("ds_001", "rawgouqi"),
        _ds("ds_002", "rawgouqi"),
    ]
    r = mod.ApiDataGet.call(name="rawgouqi")
    assert r["dataset"] is None
    assert r["error"] == "ambiguous"
    assert {c["id"] for c in r["candidates"]} == {"ds_001", "ds_002"}


def test_get_similar_name_not_matched(api_data):
    """防模糊回归：相似名称不命中，绝不猜。"""
    mod, state = api_data
    state["visible"] = [_ds("ds_001", "rawgouqi")]
    for query in ("rawgou", "gouqi", "raw_gouqi", "rawgouqi2", "枸杞"):
        r = mod.ApiDataGet.call(name=query)
        assert r["dataset"] is None and r["error"] == "not_found", query


def test_get_not_found(api_data):
    mod, state = api_data
    state["visible"] = [_ds("ds_001", "rawgouqi")]
    r = mod.ApiDataGet.call(name="nonexistent")
    assert r["dataset"] is None and r["error"] == "not_found"


def test_get_foreign_dataset_not_accessible(api_data):
    """跨用户隔离：全库有但不属于当前用户 → 明确无权访问。"""
    mod, state = api_data
    state["visible"] = []
    state["all"] = [_ds("ds_999", "secret_data", owner="other")]
    r = mod.ApiDataGet.call(name="secret_data")
    assert r["dataset"] is None and r["error"] == "not_found"
    assert "无权访问" in r["message"]


def test_get_empty_name(api_data):
    mod, state = api_data
    r = mod.ApiDataGet.call(name="")
    assert r["dataset"] is None and r["error"] == "not_found"


# ── Agent._pick_dataset_id() ───────────────────────────────────────

@pytest.fixture
def agent_cls(monkeypatch):
    """把 DataRegistry._read 替换为可控假数据，并加载 InteractiveAgent。"""
    from core.resource.registry.data_registry import DataRegistry
    from resources.agents.implementations.interactive_agent.agent import InteractiveAgent

    state = {"entries": []}
    monkeypatch.setattr(DataRegistry, "_read", lambda self: state["entries"])
    return InteractiveAgent, state


def test_pick_by_id(agent_cls):
    cls, state = agent_cls
    state["entries"] = [_ds("ds_001", "rawgouqi")]
    mid, status, _ = cls._pick_dataset_id("ds_001")
    assert mid == "ds_001" and status == "ok"


def test_pick_by_name_unique(agent_cls):
    cls, state = agent_cls
    state["entries"] = [_ds("rawgouqi_1795340961", "rawgouqi")]
    mid, status, _ = cls._pick_dataset_id("rawgouqi")
    assert mid == "rawgouqi_1795340961" and status == "ok"


def test_pick_ambiguous_returns_candidates(agent_cls):
    """重名不静默取第一个，返回 ambiguous + 候选（格式 name/id）。"""
    cls, state = agent_cls
    state["entries"] = [_ds("ds_001", "rawgouqi"), _ds("ds_002", "rawgouqi")]
    mid, status, cands = cls._pick_dataset_id("rawgouqi")
    assert mid is None and status == "ambiguous"
    assert {c["id"] for c in cands} == {"ds_001", "ds_002"}
    assert all("name" in c and "id" in c for c in cands)


def test_pick_similar_name_not_selected(agent_cls):
    """防模糊回归：相似输入不自动选中，但可出现在候选提示里。"""
    cls, state = agent_cls
    state["entries"] = [_ds("ds_001", "rawgouqi")]
    for query in ("rawgou", "gouqi", "raw_gouqi", "rawgouqi2", "枸杞"):
        mid, status, cands = cls._pick_dataset_id(query)
        assert mid is None and status == "not_found", query
        # 相似名可出现在候选（仅提示），但绝不能被选中
        assert all(c["id"] != "ds_001" or mid is None for c in cands)


def test_pick_not_found(agent_cls):
    cls, state = agent_cls
    state["entries"] = [_ds("ds_001", "rawgouqi")]
    mid, status, cands = cls._pick_dataset_id("nonexistent")
    assert mid is None and status == "not_found"
    assert cands == []


def test_pick_empty(agent_cls):
    cls, state = agent_cls
    state["entries"] = [_ds("ds_001", "rawgouqi")]
    mid, status, cands = cls._pick_dataset_id("")
    assert mid is None and status == "not_found" and cands == []


def test_pick_fuzzy_candidates_limited_and_sorted(agent_cls):
    """模糊候选最多 5 条，且按相似度降序。"""
    cls, state = agent_cls
    state["entries"] = [
        _ds("ds_1", "rawgouqi"), _ds("ds_2", "rawgouqi2"),
        _ds("ds_3", "rawgouqi_01"), _ds("ds_4", "gouqi_raw"),
        _ds("ds_5", "raw_gouqi"), _ds("ds_6", "gouqi"),
        _ds("ds_7", "fgouqif"), _ds("ds_8", "rawgouqixxx"),
    ]
    mid, status, cands = cls._pick_dataset_id("rawgouqi")
    assert mid == "ds_1" and status == "ok"  # 精确命中优先
    mid, status, cands = cls._pick_dataset_id("rawgouqiX")
    assert mid is None and status == "not_found"
    assert len(cands) <= 5
    sims = [cls._similarity(c["name"], "rawgouqiX") for c in cands]
    assert sims == sorted(sims, reverse=True)


def test_dataset_hint_format(agent_cls):
    """提示格式：区分重名/未找到，候选为 名称 : id，引导去数据空间查。"""
    cls, _ = agent_cls
    hint = cls._dataset_hint("gouqi", "not_found", [
        {"name": "枸杞数据集", "id": "ds_001"},
        {"name": "rawgouqi", "id": "ds_002"},
    ])
    assert "没有找到" in hint
    assert "名称 : id" in hint
    assert "枸杞数据集 : ds_001" in hint
    assert "数据空间" in hint

    hint2 = cls._dataset_hint("rawgouqi", "ambiguous", [
        {"name": "rawgouqi", "id": "ds_001"},
        {"name": "rawgouqi", "id": "ds_002"},
    ])
    assert "多个" in hint2 and "id" in hint2
    assert "ds_001" in hint2 and "ds_002" in hint2
