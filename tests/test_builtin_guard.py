"""内置层写/删闸门：内置版工具和 Agent 仅超级管理员可修改或删除。

覆盖 app.api.deps.ensure_can_touch_builtin 与 gateway._role_of 的核心语义：
- 命中私有层（个人定制版）→ 任何身份放行
- 命中内置层 → 超管放行、普通用户 403、匿名按单用户模式视为超管放行
- 网关注入身份的角色回用户表查（超管 uid=0 恒为 admin）
"""

import pytest
from fastapi import HTTPException

from app.api.deps import ensure_can_touch_builtin
from core.auth.session import AuthUser, reset_current_user, set_current_user


@pytest.fixture(autouse=True)
def _clean_identity():
    yield
    reset_current_user  # 每个用例自行 set，结束清干净


def _as(user: AuthUser | None):
    """以指定身份执行一段断言（contextvar 作用域模拟请求）。"""
    return set_current_user(user)


def _assert_gate(user, source, *, should_pass=True, action="修改"):
    token = _as(user)
    try:
        if should_pass:
            ensure_can_touch_builtin(source, action=action)
        else:
            with pytest.raises(HTTPException) as ei:
                ensure_can_touch_builtin(source, action=action)
            assert ei.value.status_code == 403
            assert "超级用户" in ei.value.detail
    finally:
        reset_current_user(token)


def test_private_layer_open_to_everyone():
    """个人定制版：普通用户、超管、匿名都可改删。"""
    for user in (None,
                 AuthUser(id="1000", username="alice", role="user"),
                 AuthUser(id="0", username="root", role="admin")):
        _assert_gate(user, "private")


def test_builtin_layer_superuser_only():
    """内置版：超管与匿名（单用户模式）放行，普通用户 403。"""
    _assert_gate(AuthUser(id="0", username="root", role="admin"), "builtin")
    _assert_gate(None, "builtin")  # 匿名 = 单用户兼容模式，中间件会注入超管
    _assert_gate(AuthUser(id="1000", username="alice", role="user"),
                 "builtin", should_pass=False, action="删除")


def test_unknown_source_treated_as_builtin():
    """source 缺失（异常数据）按内置对待——保守拒绝普通用户。"""
    _assert_gate(AuthUser(id="1000", username="alice", role="user"),
                 None, should_pass=False)


def test_gateway_role_of_superuser_and_regular():
    from core.chat_control.gateway import _role_of

    assert _role_of("0") == "admin"  # 超管 uid 恒定，不查表也成立


def test_gateway_role_of_regular_user_falls_back(monkeypatch):
    import core.chat_control.gateway as gw
    import core.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_user_by_id",
                        lambda uid: {"id": uid, "role": "user"})
    assert gw._role_of("1000") == "user"

    # 用户表里标了 admin 的普通 uid 也算 admin（表是唯一事实源）
    monkeypatch.setattr(auth_mod, "get_user_by_id",
                        lambda uid: {"id": uid, "role": "admin"})
    assert gw._role_of("1001") == "admin"


def test_gateway_role_of_unknown_user_is_regular(monkeypatch):
    """查不到的 uid 按普通用户处理：宁可不给权，不可错给权。"""
    import core.auth as auth_mod
    import core.chat_control.gateway as gw

    monkeypatch.setattr(auth_mod, "get_user_by_id", lambda uid: None)
    assert gw._role_of("9999") == "user"
