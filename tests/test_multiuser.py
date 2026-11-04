"""多用户系统核心逻辑测试

覆盖：超级用户（.env 驱动）、普通用户 uid 分配、按 uid 分库隔离、个人偏好。

全部隔离到 tmp_path：用户目录根与 storage/auth 都不碰真实数据。
"""

import json

import pytest

from core import user_paths
from core.auth import (
    AuthError,
    FIRST_USER_UID,
    SUPERUSER_UID,
    authenticate,
    change_own_password,
    create_user,
    delete_user,
    get_user_by_id,
    issue_token,
    list_users,
    rename_user,
    set_password,
    superuser,
    superuser_password_configured,
    verify_token,
)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """用户目录根、用户表、令牌密钥、各存储连接池全部指向临时位置。"""
    from core.chat_control import audit
    from core.cron import store as cron_store
    from core.observability import chat_store, task_history
    from core import auth as auth_mod
    from core import user_prefs

    root = tmp_path / "SB-USER"
    root.mkdir()
    monkeypatch.setattr(user_paths, "USER_HOME_ROOT", root)
    monkeypatch.setattr(auth_mod, "USERS_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_mod, "USERS_FILE", tmp_path / "auth" / "users.json")
    monkeypatch.setattr(auth_mod, "SECRET_FILE", tmp_path / "auth" / "secret.key")
    monkeypatch.setattr(auth_mod, "PROFILE_FILE", tmp_path / "auth" / "superuser.json")
    monkeypatch.delenv("SOTABAND_AUTH_SECRET", raising=False)
    monkeypatch.delenv("SOTABAND_USER_ID", raising=False)

    pools = [
        chat_store._pool, task_history._pool, audit._pool,
        cron_store._pool, user_prefs._pool,
    ]
    for p in pools:
        p.reset()
    yield
    for p in pools:
        p.reset()


@pytest.fixture
def superuser_env(monkeypatch):
    monkeypatch.setenv("SOTABAND_ROOT_USERNAME", "root")
    monkeypatch.setenv("SOTABAND_ROOT_PASSWORD", "sup3r-secret-pw")


# ── 超级用户 ──

def test_superuser_identity_always_available(monkeypatch):
    """未配置密码时身份仍然可见（单用户模式），但不可用密码登录。"""
    monkeypatch.setenv("SOTABAND_ROOT_PASSWORD", "")
    su = superuser()
    assert su["id"] == "0" and su["role"] == "admin" and su["builtin"] is True
    # 用户管理页必须能看到超管自己这一行，否则看起来像"超级用户不存在"
    assert list_users()[0]["id"] == "0"
    # 但该模式下没有密码可比，任何登录尝试都失败
    assert superuser_password_configured() is False
    assert authenticate("root", "whatever-password") is None


def test_superuser_defaults_to_root_name(superuser_env, monkeypatch):
    monkeypatch.setenv("SOTABAND_ROOT_USERNAME", "")
    assert superuser()["username"] == "root"
    assert superuser()["id"] == SUPERUSER_UID == "0"


def test_superuser_login_and_token(superuser_env):
    user = authenticate("root", "sup3r-secret-pw")
    assert user and user["id"] == "0" and user["role"] == "admin"
    assert "password" not in user
    assert authenticate("root", "wrong-password") is None

    payload = verify_token(issue_token(user))
    assert payload["uid"] == "0" and payload["role"] == "admin"


def test_list_users_has_superuser_first_and_no_passwords(superuser_env):
    create_user("alice", "alice-password")
    users = list_users()
    assert [u["id"] for u in users] == ["0", "1000"]
    assert all("password" not in u for u in users)


def test_superuser_password_never_in_users_file(superuser_env):
    from core.auth import USERS_FILE

    create_user("alice", "alice-password")
    raw = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    # 超管不入库：文件里只有普通用户，且任何人都不该有明文/散列外泄
    assert [u["username"] for u in raw] == ["alice"]


# ── 普通用户 uid 分配 ──

def test_uid_starts_at_1000_and_increments(superuser_env):
    a = create_user("alice", "alice-password")
    b = create_user("bob", "bob-password")
    assert a["id"] == str(FIRST_USER_UID) == "1000"
    assert b["id"] == "1001"


def test_uid_never_reused_after_delete(superuser_env):
    a = create_user("alice", "alice-password")
    delete_user(a["id"])
    # 目录还在（删号保留数据），号位不能被复用，否则新用户继承旧数据
    b = create_user("bob", "bob-password")
    assert b["id"] != a["id"]
    assert b["id"] == "1001"


def test_user_home_dir_created_on_create(superuser_env):
    u = create_user("alice", "alice-password")
    assert user_paths.user_home(u["id"]).is_dir()
    assert user_paths.user_home(u["id"]).name == "1000"


def test_username_validation(superuser_env):
    with pytest.raises(AuthError):
        create_user("x", "alice-password")          # 太短
    with pytest.raises(AuthError):
        create_user("alice", "short")               # 密码不足 8 位
    create_user("alice", "alice-password")
    with pytest.raises(AuthError):
        create_user("alice", "another-password")    # 重名


def test_superuser_name_reserved(superuser_env):
    with pytest.raises(AuthError):
        create_user("root", "some-password")


# ── 改名 / 改密 ──

def test_rename_keeps_uid(superuser_env):
    u = create_user("alice", "alice-password")
    renamed = rename_user(u["id"], "alice2")
    assert renamed["id"] == u["id"] == "1000"
    assert renamed["username"] == "alice2"
    assert authenticate("alice2", "alice-password")["id"] == "1000"
    assert authenticate("alice", "alice-password") is None


def test_rename_superuser_via_override(superuser_env):
    """超管可在界面改名：写覆盖层文件，不碰 .env、不入库，登录名随之生效。"""
    renamed = rename_user(SUPERUSER_UID, "admin2")
    assert renamed["id"] == SUPERUSER_UID
    assert renamed["username"] == "admin2"
    assert renamed["display_name"] == "admin2"
    # 新名字可登录（密码仍是 .env 里那个），旧名字失效
    assert authenticate("admin2", "sup3r-secret-pw") is not None
    assert authenticate("root", "sup3r-secret-pw") is None
    # 覆盖层不污染普通用户表
    assert "admin2" not in json.dumps(
        [u["username"] for u in list_users() if u["id"] != SUPERUSER_UID])


def test_superuser_display_name_override(superuser_env):
    from core.auth import set_display_name

    u = set_display_name(SUPERUSER_UID, "大管家")
    assert u["username"] == "root" and u["display_name"] == "大管家"


def test_superuser_password_change_rejected(superuser_env):
    with pytest.raises(AuthError):
        set_password(SUPERUSER_UID, "brand-new-password")
    with pytest.raises(AuthError):
        change_own_password(SUPERUSER_UID, "sup3r-secret-pw", "brand-new-password")
    # 未被改动
    assert authenticate("root", "sup3r-secret-pw") is not None


def test_change_own_password_requires_old(superuser_env):
    u = create_user("alice", "alice-password")
    with pytest.raises(AuthError):
        change_own_password(u["id"], "wrong-old-password", "new-password-1")
    change_own_password(u["id"], "alice-password", "new-password-1")
    assert authenticate("alice", "new-password-1") is not None
    assert authenticate("alice", "alice-password") is None


def test_superuser_can_reset_any_password(superuser_env):
    u = create_user("alice", "alice-password")
    set_password(u["id"], "reset-by-admin")
    assert authenticate("alice", "reset-by-admin") is not None


def test_password_never_leaks_from_public_api(superuser_env):
    create_user("alice", "alice-password")
    for u in list_users():
        assert "password" not in u
    assert "password" not in (get_user_by_id("1000") or {})


def test_delete_superuser_rejected(superuser_env):
    with pytest.raises(AuthError):
        delete_user(SUPERUSER_UID)


# ── 按 uid 分库隔离 ──

def test_chat_store_isolated_per_user(superuser_env):
    from core.observability import chat_store
    from core.auth.session import AuthUser, reset_current_user, set_current_user

    alice = create_user("alice", "alice-password")
    bob = create_user("bob", "bob-password")

    t1 = set_current_user(AuthUser(id=alice["id"], username="alice", role="user"))
    chat_store.append_message("s-alice", "", "user", "alice 的秘密")
    reset_current_user(t1)

    t2 = set_current_user(AuthUser(id=bob["id"], username="bob", role="user"))
    chat_store.append_message("s-bob", "", "user", "bob 的秘密")
    sessions = [s["session_id"] for s in chat_store.list_sessions()]
    reset_current_user(t2)

    assert sessions == ["s-bob"]
    assert chat_store.get_messages("s-alice") == []

    # 超管（uid=0）也只看得到自己的——三个库互不可见
    t3 = set_current_user(AuthUser(id="0", username="root", role="admin"))
    assert [s["session_id"] for s in chat_store.list_sessions()] == []
    reset_current_user(t3)

    # 库文件确实落在各自目录下
    assert user_paths.platform_db(alice["id"]).exists()
    assert user_paths.platform_db(bob["id"]).exists()


def test_cron_store_isolated_and_iterates_all_users(superuser_env):
    from core.cron import store as cron_store
    from core.auth.session import AuthUser, reset_current_user, set_current_user

    alice = create_user("alice", "alice-password")
    bob = create_user("bob", "bob-password")
    job = {"name": "j", "kind": "tool", "target_id": "t", "cron": "0 * * * *",
           "owner": alice["id"]}

    t = set_current_user(AuthUser(id=alice["id"], username="alice", role="user"))
    created = cron_store.create(dict(job, owner=alice["id"]), uid=alice["id"])
    reset_current_user(t)

    # bob 的库里没有这个任务
    assert cron_store.get(created["id"], bob["id"]) is None
    assert cron_store.list_jobs(uid=bob["id"]) == []

    # 系统级遍历能跨用户看到，并带上正确的 uid
    found = {uid: j["id"] for uid, j in cron_store.iter_jobs(include_disabled=False)}
    assert found.get(alice["id"]) == created["id"]


def test_prefs_isolated_per_user(superuser_env):
    from core import user_prefs
    from core.auth.session import AuthUser, reset_current_user, set_current_user

    alice = create_user("alice", "alice-password")
    bob = create_user("bob", "bob-password")

    t = set_current_user(AuthUser(id=alice["id"], username="alice", role="user"))
    user_prefs.set_many({"ui.theme": "light", "layout.widths": {"left": 400}})
    reset_current_user(t)

    t = set_current_user(AuthUser(id=bob["id"], username="bob", role="user"))
    assert user_prefs.get_all() == {}
    user_prefs.set_many({"ui.theme": "dark"})
    assert user_prefs.get_all() == {"ui.theme": "dark"}
    reset_current_user(t)

    t = set_current_user(AuthUser(id=alice["id"], username="alice", role="user"))
    assert user_prefs.get_all() == {"ui.theme": "light", "layout.widths": {"left": 400}}
    reset_current_user(t)


def test_prefs_rejects_oversized_value(superuser_env):
    from core import user_prefs
    from core.auth.session import AuthUser, reset_current_user, set_current_user

    t = set_current_user(AuthUser(id="0", username="root", role="admin"))
    try:
        with pytest.raises(ValueError):
            user_prefs.set_many({"big": "x" * (user_prefs.VALUE_MAX_CHARS + 1)})
        with pytest.raises(ValueError):
            user_prefs.set_many({"k" * 100: 1})
    finally:
        reset_current_user(t)
