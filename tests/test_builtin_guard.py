"""公共层写/删闸门：公共版工具和 Agent 仅系统身份（act-as 令牌）可修改或删除。

覆盖 app.api.deps.ensure_can_touch_builtin 与 gateway._role_of 的核心语义：
- 命中私有层（个人定制版）→ 任何身份放行
- 命中公共层 → 系统用户（uid=100）放行；超管令牌也不例外（严格闸门）；
  普通用户 403；匿名（单用户模式注入 uid=0）同样 403，需先 act-as
- 网关注入身份的角色按 uid 判定（唯一 admin = .env 超管 uid=0）
"""

import pytest

pytestmark = pytest.mark.unit

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


def test_public_layer_system_identity_only():
    """公共版：仅系统用户（uid=100）放行；超管与普通用户 403。"""
    _assert_gate(AuthUser(id="100", username="system", role="user"), "system")
    _assert_gate(AuthUser(id="100", username="system", role="user"), "builtin")  # 旧名兼容
    _assert_gate(AuthUser(id="100", username="system", role="user"), "shared")   # 旧名兼容
    _assert_gate(AuthUser(id="0", username="root", role="admin"),
                 "system", should_pass=False)
    _assert_gate(None, "system", should_pass=False)  # 匿名 = 单用户模式注入 uid=0，同样须 act-as
    _assert_gate(AuthUser(id="1000", username="alice", role="user"),
                 "system", should_pass=False, action="删除")


def test_unknown_source_treated_as_public_layer():
    """source 缺失（异常数据）按公共层对待——保守拒绝非系统身份。"""
    _assert_gate(AuthUser(id="1000", username="alice", role="user"),
                 None, should_pass=False)
    _assert_gate(AuthUser(id="100", username="system", role="user"), None)


def test_gateway_role_of_superuser_and_regular():
    from core.chat_control.gateway import _role_of

    assert _role_of("0") == "admin"  # 超管 uid 恒定，不查表也成立


def test_gateway_role_of_regular_user_falls_back(monkeypatch):
    import core.chat_control.gateway as gw
    import core.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_user_by_id",
                        lambda uid: {"id": uid, "role": "user"})
    assert gw._role_of("1000") == "user"

    # 表内遗留 role=admin 的普通 uid 也是 user：唯一 admin = .env 超管（uid=0），
    # 角色按 uid 判定，表里的 role 不再是权限依据
    monkeypatch.setattr(auth_mod, "get_user_by_id",
                        lambda uid: {"id": uid, "role": "admin"})
    assert gw._role_of("1001") == "user"


def test_gateway_role_of_unknown_user_is_regular(monkeypatch):
    """查不到的 uid 按普通用户处理：宁可不给权，不可错给权。"""
    import core.auth as auth_mod
    import core.chat_control.gateway as gw

    monkeypatch.setattr(auth_mod, "get_user_by_id", lambda uid: None)
    assert gw._role_of("9999") == "user"
