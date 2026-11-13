"""设置/用户管理/定时任务：管理员页面与列表渲染。"""

from __future__ import annotations

import pytest

from tests.e2e.helpers.selectors import S

pytestmark = pytest.mark.e2e


def test_admin_settings_and_users_pages(page_factory, admin_token, fixtures):
    page = page_factory(admin_token)
    page.get_by_test_id(S.open_settings).click()
    page.get_by_test_id(S.settings_root).wait_for(state="visible", timeout=15_000)

    page.get_by_test_id(S.open_users).click()
    users_root = page.get_by_test_id(S.users_root)
    users_root.wait_for(state="visible", timeout=15_000)
    user = (fixtures.get("user") or {}).get("username") or ""
    if user:
        from tests.e2e.helpers.wait import wait_until
        wait_until(
            lambda: user in users_root.inner_text(),
            timeout=15,
            description="用户列表应加载出 E2E 普通用户",
        )


def test_cron_view_opens_and_lists(page):
    page.get_by_test_id(S.left_tab_resources).click()
    entry = page.get_by_test_id("cron-jobs-entry")
    entry.wait_for(state="visible", timeout=15_000)
    entry.click()
    root = page.get_by_test_id(S.cron_root)
    root.wait_for(state="visible", timeout=15_000)
    assert "定时任务" in root.inner_text()
