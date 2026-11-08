"""发布归属校验（P0-2）单元测试 —— 管理员评审意见 2。

发布 = 把资源开放给某个范围，因此必须确认发布者有权处置该资源。
此前只校验"发布范围权限"，漏了"资源归属"：任何成员都能发布别人的私有
数据集/工具，进目录、可安装、可调用 → 信息泄露。

这里直接对服务层 helper 做规则级验证（不依赖 DB/registry 文件）。

⚠️ 2026-11 独立终审新增：工具/Agent 注册时 ``visibility`` 恒为 ``public`` 且没有
修改接口 —— 旧的「public 即允许代发」规则实际等价于「任何登录用户都能代他人发布」
（实测 201，目录里署名变成代发布者）。现在 owner 存在且不是本人时，还要求
租户 Owner/Admin（与 §D2 写权限口径一致）。本文件相应把"public 代发"的用例
改成两条：普通成员 404、租户管理员允许。
"""

from __future__ import annotations

import pytest

from core.errors import NotFoundError
from core.sharing import service as sharing


class _Ctx:
    def __init__(
        self,
        user_id: str = "user-a",
        tenant_id: str = "11111111-1111-1111-1111-111111111111",
        tenant_role: str = "member",
    ):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.tenant_role = tenant_role
        self.request_id = "req-1"


@pytest.fixture()
def fake_entries(monkeypatch):
    """用内存条目替掉 registry 查询（避免测试污染真实资源根）。"""
    entries: dict[str, dict | None] = {}

    async def _fake_find(resource_type: str, release_id: str):
        return entries.get(f"{resource_type}:{release_id}")

    monkeypatch.setattr(sharing, "_find_release_entry", _fake_find)
    return entries


def _entry(owner: str | None, visibility: str | None = None) -> dict:
    data = {"id": "x", "owner": owner or ""}
    if visibility:
        data["visibility"] = visibility
    return data


async def test_data_owned_by_self_passes(fake_entries):
    fake_entries["data:ds-1"] = _entry("user-a")
    await sharing._assert_can_publish_release(_Ctx("user-a"), resource_type="data", release_id="ds-1")


async def test_data_owned_by_other_rejected(fake_entries):
    fake_entries["data:ds-1"] = _entry("user-b")
    with pytest.raises(NotFoundError):
        await sharing._assert_can_publish_release(
            _Ctx("user-a"), resource_type="data", release_id="ds-1"
        )


async def test_missing_release_rejected_fail_closed(fake_entries):
    with pytest.raises(NotFoundError):
        await sharing._assert_can_publish_release(
            _Ctx("user-a"), resource_type="tool", release_id="never-exists"
        )


async def test_tool_owned_by_other_public_rejected_for_member(fake_entries):
    """工具/Agent 是共享资产，但**代他人发布**必须由租户管理员做。

    （旧规则是"public 即允许"；而注册恒写 public 且无修改接口，实际等于人人可代发。）
    """
    fake_entries["tool:tool-1"] = _entry("user-b", visibility="public")
    with pytest.raises(NotFoundError):
        await sharing._assert_can_publish_release(
            _Ctx("user-a", tenant_role="member"),
            resource_type="tool",
            release_id="tool-1",
        )


async def test_tool_owned_by_other_public_allowed_for_tenant_admin(fake_entries):
    """租户 Owner/Admin 可以代发布共享资产（目录治理的兜底能力）。"""
    fake_entries["tool:tool-1"] = _entry("user-b", visibility="public")
    for role in ("owner", "admin"):
        await sharing._assert_can_publish_release(
            _Ctx("user-a", tenant_role=role),
            resource_type="tool",
            release_id="tool-1",
        )


async def test_agent_owned_by_other_public_rejected_for_member(fake_entries):
    """Agent 与工具同口径：成员不能替别人发布 Agent。"""
    fake_entries["agent:agent-1"] = _entry("user-b", visibility="public")
    with pytest.raises(NotFoundError):
        await sharing._assert_can_publish_release(
            _Ctx("user-a", tenant_role="member"),
            resource_type="agent",
            release_id="agent-1",
        )


async def test_tool_owned_by_other_private_rejected(fake_entries):
    fake_entries["tool:tool-1"] = _entry("user-b", visibility="private")
    with pytest.raises(NotFoundError):
        await sharing._assert_can_publish_release(
            _Ctx("user-a", tenant_role="admin"),
            resource_type="tool",
            release_id="tool-1",
        )


async def test_tool_owned_by_self_allowed(fake_entries):
    fake_entries["tool:tool-1"] = _entry("user-a", visibility="private")
    await sharing._assert_can_publish_release(
        _Ctx("user-a"), resource_type="tool", release_id="tool-1"
    )


async def test_builtin_tool_without_owner_allowed(fake_entries):
    """内置共享资产（无 owner）允许发布。"""
    fake_entries["agent:builtin-agent"] = _entry(None)
    await sharing._assert_can_publish_release(
        _Ctx("user-a"), resource_type="agent", release_id="builtin-agent"
    )


async def test_denied_publish_writes_audit(monkeypatch):
    """拒绝也要留痕（FR-N9）。"""
    recorded: list[dict] = []

    def _fake_audit(session, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(sharing, "add_audit_event", _fake_audit)
    sharing.audit_publish_denied(
        object(),
        ctx=_Ctx("user-a"),
        request_id="req-1",
        resource_type="data",
        release_id="ds-secret",
    )
    assert recorded and recorded[0]["result"] == "denied"
    assert recorded[0]["target_id"] == "ds-secret"
