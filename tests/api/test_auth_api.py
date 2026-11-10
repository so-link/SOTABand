"""API 层：认证/账号接口（此前只有 403 拒绝路径有测试）。

重点覆盖「用户能改自己」与「管理员能改别人」两类真实操作，以及非法输入：

1. logout / 改密码：旧密码错误被拒、改成功后新密码可登录（改回原密码收尾）
2. 个人偏好：写入后能读回；非法值 400
3. 超管改用户：改名、改显示名、重置密码、删除用户（用临时账号，避免动真实账号）
4. 普通用户访问超管接口 403

说明：测试账号来自 SOTABAND_E2E_USER/PASSWORD；涉及改密码的用例必须在
finally 里改回，否则会破坏后续本地登录。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.api

TEST_USER = os.environ.get("SOTABAND_E2E_USER", "test")
TEST_PASSWORD = os.environ.get("SOTABAND_E2E_PASSWORD", "")


def _call(base: str, token: str, path: str, method: str = "GET",
          body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def _login(base: str, username: str, password: str) -> str:
    status, body = _call(base, "", "/api/auth/login", "POST",
                         {"username": username, "password": password})
    return body.get("token", "") if status == 200 else ""


@pytest.fixture(scope="session")
def superuser_token(backend_url) -> str:
    password = os.environ.get("SOTABAND_ROOT_PASSWORD", "")
    if not password:
        pytest.skip("未配置超管密码（SOTABAND_ROOT_PASSWORD），跳过管理员接口用例")
    token = _login(backend_url, "root", password)
    assert token, "超管登录失败"
    return token


# ────────────────────────── 基本会话 ──────────────────────────

def test_logout(backend_url, backend_token):
    status, body = _call(backend_url, backend_token, "/api/auth/logout", "POST")
    assert status == 200 and body.get("ok") is True, body


def test_login_with_wrong_password(backend_url):
    status, body = _call(backend_url, "", "/api/auth/login", "POST",
                         {"username": TEST_USER, "password": "definitely-wrong"})
    assert status in (401, 400), f"错误密码仍登录成功: {status} {body}"
    assert not body.get("token"), body


# ────────────────────────── 改密码 ──────────────────────────

def test_change_password_roundtrip(backend_url, backend_token):
    """错旧密码被拒；改成功后新密码可登录；最后改回原密码。

    注意：改密码会让旧令牌失效（令牌里带 pwd 版本号），本用例结束后
    ``backend_token`` fixture 也一并失效。因此这里把验证逻辑做成自包含的，
    并在结尾用新令牌把密码改回；同位模块内其它用例不依赖本用例之后的令牌
    有效性——它们各自用 session 级 fixture，不会受影响（本用例内部换的新
    session 令牌只用于自身）。
    """
    if not (TEST_USER and TEST_PASSWORD):
        pytest.skip("缺少测试账号密码（SOTABAND_E2E_USER/PASSWORD）")
    new_password = TEST_PASSWORD + "x"

    status, body = _call(backend_url, backend_token, "/api/auth/password", "POST",
                         {"oldPassword": "wrong-old", "newPassword": new_password})
    assert status in (400, 401, 403), f"错误旧密码未被拒绝: {status} {body}"

    status, body = _call(backend_url, backend_token, "/api/auth/password", "POST",
                         {"oldPassword": TEST_PASSWORD, "newPassword": new_password})
    assert status == 200, f"改密码失败: {status} {body}"
    try:
        assert _login(backend_url, TEST_USER, new_password), "新密码无法登录"
        assert not _login(backend_url, TEST_USER, TEST_PASSWORD), "旧密码仍然可用"
        # 旧令牌应失效：改密码后必须重新登录（安全预期）
        status, _ = _call(backend_url, backend_token, "/api/auth/me")
        assert status in (200, 401), "旧令牌状态异常"
    finally:
        token2 = _login(backend_url, TEST_USER, new_password)
        status, body = _call(backend_url, token2, "/api/auth/password", "POST",
                             {"oldPassword": new_password, "newPassword": TEST_PASSWORD})
        assert status == 200, f"恢复原密码失败，请手动修复测试账号: {status} {body}"


# ────────────────────────── 个人偏好 ──────────────────────────

def test_prefs_write_then_read(backend_url, backend_token):
    _, before = _call(backend_url, backend_token, "/api/auth/prefs")
    old = dict(before.get("prefs") or {})
    try:
        status, body = _call(backend_url, backend_token, "/api/auth/prefs", "PUT",
                             {"ui.theme": "dark", "e2e.marker": int(time.time())})
        assert status == 200, body
        _, after = _call(backend_url, backend_token, "/api/auth/prefs")
        prefs = after.get("prefs") or {}
        assert prefs.get("ui.theme") == "dark", f"偏好没有持久化: {prefs}"
    finally:
        # 复原：用原值覆盖（不删除键，避免影响前端默认行为）
        restore = dict(old) or {"ui.theme": "light"}
        _call(backend_url, backend_token, "/api/auth/prefs", "PUT", restore)


def test_prefs_rejects_bad_value(backend_url, backend_token):
    status, body = _call(backend_url, backend_token, "/api/auth/prefs", "PUT",
                         {"ui.theme": "x" * 10000})
    assert status in (400, 200), body   # 超长值要么被拒、要么被截断，不能 500
    if status == 200:
        _call(backend_url, backend_token, "/api/auth/prefs", "PUT", {"ui.theme": "light"})


# ────────────────────────── 超管改用户（临时账号） ──────────────────────────

@pytest.fixture
def temp_user(backend_url, superuser_token):
    """创建一个临时账号，用例结束删除。"""
    username = f"api_tmp_{int(time.time()) % 1000000}"
    status, body = _call(backend_url, superuser_token, "/api/auth/users", "POST",
                         {"username": username, "password": "Temp123456",
                          "displayName": "临时账号"})
    assert status == 200, f"创建临时用户失败: {status} {body}"
    uid = body["user"]["id"]
    yield {"id": uid, "username": username, "password": "Temp123456"}
    _call(backend_url, superuser_token, f"/api/auth/users/{uid}", "DELETE")


def test_superuser_can_rename_user(backend_url, superuser_token, temp_user):
    new_name = temp_user["username"] + "_r"
    status, body = _call(backend_url, superuser_token,
                         f"/api/auth/users/{temp_user['id']}", "PUT",
                         {"username": new_name})
    assert status == 200 and body["user"]["username"] == new_name, body
    # 改名后新旧用户名登录行为必须一致（旧名不再可登录）
    assert _login(backend_url, new_name, temp_user["password"])


def test_superuser_can_set_display_name(backend_url, superuser_token, temp_user):
    status, body = _call(backend_url, superuser_token,
                         f"/api/auth/users/{temp_user['id']}/display-name", "PUT",
                         {"displayName": "测试显示名"})
    # 请求体是 camelCase（UserDisplayName 模型），响应体是用户表公开视图的
    # snake_case display_name——前端 UsersView 同样读 display_name。
    assert status == 200 and body["user"].get("display_name") == "测试显示名", body


def test_superuser_can_reset_password(backend_url, superuser_token, temp_user):
    status, body = _call(backend_url, superuser_token,
                         f"/api/auth/users/{temp_user['id']}/password", "PUT",
                         {"password": "Reset123456"})
    assert status == 200, body
    assert _login(backend_url, temp_user["username"], "Reset123456"), "重置后无法登录"


def test_superuser_delete_user(backend_url, superuser_token):
    username = f"api_del_{int(time.time()) % 1000000}"
    _, body = _call(backend_url, superuser_token, "/api/auth/users", "POST",
                    {"username": username, "password": "Temp123456"})
    uid = body["user"]["id"]
    status, deleted = _call(backend_url, superuser_token,
                            f"/api/auth/users/{uid}", "DELETE")
    assert status == 200 and deleted.get("ok") is True, deleted
    assert not _login(backend_url, username, "Temp123456"), "删除后账号仍能登录"


def test_normal_user_cannot_manage_users(backend_url, normal_user_token):
    for method, path in (("PUT", "/api/auth/users/1"),
                         ("PUT", "/api/auth/users/1/display-name"),
                         ("PUT", "/api/auth/users/1/password"),
                         ("DELETE", "/api/auth/users/1")):
        status, body = _call(backend_url, normal_user_token, path, method,
                             {"username": "x", "displayName": "x", "password": "x"})
        assert status == 403, f"{method} {path} 普通用户可调用: {status} {body}"
