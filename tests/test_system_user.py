"""系统用户（uid=100）身份层测试

覆盖：不可登录、不可删/改名/设密码、保留用户名、普通用户 uid 下限
不撞系统号位、act-as 令牌的签发/校验/过期。

与 test_multiuser.py 共用隔离策略：用户目录根与用户表都在 tmp_path。
"""

import pytest

pytestmark = pytest.mark.unit


from core import user_paths
from core.auth import (
    SYSTEM_USER_UID,
    AuthError,
    authenticate,
    create_user,
    delete_user,
    is_system_uid,
    issue_system_token,
    rename_user,
    set_password,
    system_user,
    verify_token,
)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    from core import auth as auth_mod
    from core.cron import store as cron_store

    root = tmp_path / "SB-USER"
    root.mkdir()
    monkeypatch.setattr(user_paths, "USER_HOME_ROOT", root)
    monkeypatch.setattr(auth_mod, "USERS_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_mod, "USERS_FILE", tmp_path / "auth" / "users.json")
    monkeypatch.setattr(auth_mod, "SECRET_FILE", tmp_path / "auth" / "secret.key")
    monkeypatch.delenv("SOTABAND_AUTH_SECRET", raising=False)
    monkeypatch.delenv("SOTABAND_USER_ID", raising=False)
    cron_store._pool.reset()
    yield
    cron_store._pool.reset()


# ── 身份基元 ──

def test_system_row_seeded_and_builtin():
    su = system_user()
    assert su["id"] == SYSTEM_USER_UID == "100"
    assert su["builtin"] is True and su["role"] == "user"
    assert "password" not in su
    assert is_system_uid("100") and not is_system_uid("0")


def test_system_user_cannot_login():
    """即使手工往行里塞密码散列也不可登录（纵深防御，同超管行）。"""
    from core.auth import _hash_password, _load_users, _save_users

    users = _load_users()
    row = next(u for u in users if str(u.get("id")) == SYSTEM_USER_UID)
    salt, digest, iters = _hash_password("guess-me")
    row["password"] = {"algo": "sha256", "salt": salt, "hash": digest, "iterations": iters}
    _save_users(users)
    assert authenticate("system", "guess-me") is None


def test_system_user_protected_from_mutation():
    with pytest.raises(AuthError):
        delete_user(SYSTEM_USER_UID)
    with pytest.raises(AuthError):
        rename_user(SYSTEM_USER_UID, "sys2")
    with pytest.raises(AuthError):
        set_password(SYSTEM_USER_UID, "some-password-123")


def test_reserved_username_blocked():
    with pytest.raises(AuthError):
        create_user("system", "some-password-123")


def test_normal_uids_never_hit_reserved_band():
    """普通用户从 1000 起，任何情况下不会拿到 <1000 的号位（含 100）。"""
    a = create_user("alice", "alice-password")
    assert int(a["id"]) >= 1000
    # 残留系统目录也不能把分配下限拉低
    (user_paths.USER_HOME_ROOT / "100").mkdir()
    b = create_user("bob", "bob-password")
    assert int(b["id"]) >= 1000


# ── act-as 代管令牌 ──

def test_system_token_roundtrip():
    token = issue_system_token()
    payload = verify_token(token)
    assert payload is not None
    assert payload["uid"] == SYSTEM_USER_UID
    # 系统用户不是 admin：资源写权限来自闸门层的 is_system_uid，不是角色
    assert payload["role"] == "user"


def test_system_token_expiry():
    from core.auth import issue_token, system_user

    token = issue_token(system_user(), ttl=-1)
    assert verify_token(token) is None


def test_system_user_row_survives_in_cron_alive_set():
    """cron 调度以 known_uids() 判存活：系统用户行在表 → 系统定时任务会被调度。"""
    from core.auth import known_uids

    assert SYSTEM_USER_UID in known_uids()
