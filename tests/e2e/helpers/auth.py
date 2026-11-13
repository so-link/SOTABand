"""登录/退出与 token 注入。"""

from __future__ import annotations

import json

from .selectors import S

TOKEN_KEY = "sotaband-token"


def set_token(page, token: str) -> None:
    frontend_url = str(getattr(page, "e2e_frontend_url", page.url or ""))
    page.goto(frontend_url)
    page.evaluate(
        f"localStorage.setItem({json.dumps(TOKEN_KEY)}, {json.dumps(token)})"
    )
    page.reload()


def clear_token(page) -> None:
    page.evaluate(f"localStorage.removeItem({json.dumps(TOKEN_KEY)})")


def open_login(page) -> None:
    dialog = page.get_by_test_id(S.login_root)
    if dialog.count() == 0 or not dialog.first.is_visible():
        page.get_by_test_id(S.topbar_login).click()
        dialog.first.wait_for(state="visible", timeout=10_000)


def login_via_ui(page, username: str, password: str) -> None:
    open_login(page)
    page.get_by_test_id(S.login_username).fill(username)
    page.get_by_test_id(S.login_password).fill(password)
    page.get_by_test_id(S.login_submit).click()
    page.get_by_test_id(S.user_menu).wait_for(state="visible", timeout=15_000)


def logout_via_ui(page) -> None:
    page.get_by_test_id(S.user_menu).click()
    page.get_by_test_id(S.logout_button).click()
    page.get_by_test_id(S.topbar_login).wait_for(state="visible", timeout=10_000)
