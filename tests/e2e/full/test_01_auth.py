"""认证：登录框、错误密码、普通用户登录/退出、token 持久化。"""

from __future__ import annotations

import os

import pytest

from tests.e2e.helpers.auth import login_via_ui, logout_via_ui
from tests.e2e.helpers.selectors import S

pytestmark = pytest.mark.e2e


def test_login_dialog_wrong_password_then_success(anon_page, fixtures):
    page = anon_page
    page.e2e_guards.allow_api("/api/auth/login")
    page.e2e_guards.allow_console("401")

    dialog = page.get_by_test_id(S.login_root)
    dialog.wait_for(state="visible", timeout=15_000)
    page.get_by_test_id(S.login_username).fill("root")
    page.get_by_test_id(S.login_password).fill("definitely-wrong-password")
    page.get_by_test_id(S.login_submit).click()
    page.get_by_test_id(S.login_error).wait_for(state="visible", timeout=10_000)

    user = fixtures.get("user") or {}
    page.get_by_test_id(S.login_username).fill(str(user.get("username") or os.environ["SOTABAND_E2E_USER"]))
    page.get_by_test_id(S.login_password).fill(str(user.get("password") or os.environ["SOTABAND_E2E_PASSWORD"]))
    page.get_by_test_id(S.login_submit).click()
    page.get_by_test_id(S.user_menu).wait_for(state="visible", timeout=15_000)

    logout_via_ui(page)
    page.get_by_test_id(S.topbar_login).wait_for(state="visible", timeout=10_000)


def test_token_persisted_and_admin_entry_visibility(page, fixtures):
    page.reload(wait_until="domcontentloaded")
    page.get_by_test_id(S.user_menu).wait_for(state="visible", timeout=15_000)
    token = page.evaluate("() => localStorage.getItem('sotaband-token')")
    assert token, "刷新后 localStorage 中 token 丢失"

    user = fixtures.get("user") or {}
    # 普通用户不应看到管理员设置/用户管理入口
    assert page.get_by_test_id(S.open_settings).count() == 0
    assert page.get_by_test_id(S.open_users).count() == 0
    assert user.get("username")
