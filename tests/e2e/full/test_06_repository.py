"""工具仓库：列表/计数/搜索来自真实接口。"""

from __future__ import annotations

import re

import pytest

from tests.e2e.helpers.selectors import S
from tests.e2e.helpers.wait import wait_until

pytestmark = pytest.mark.e2e


def test_tool_repository_count_and_search(page, api):
    tools = (api.get("/api/tool/list").json() or {}).get("tools") or []
    assert tools
    page.get_by_test_id(S.left_tab_resources).click()
    entry = page.get_by_text(re.compile("工具仓库另有"))
    entry.wait_for(state="visible", timeout=15_000)
    entry.click()
    page.get_by_test_id(S.repository_root).wait_for(state="visible", timeout=15_000)
    wait_until(
        lambda: page.get_by_test_id(S.repository_count).inner_text().strip() == str(len(tools)),
        timeout=15,
        description="工具仓库计数应与 /api/tool/list 一致",
    )

    query = "calculate"
    page.get_by_test_id("tool-repository-search").fill(query)
    expected = next((tool for tool in tools if query in str(tool.get("id", "")).lower()), None)
    if expected:
        card = page.get_by_test_id(f"tool-repository-card-{expected['id']}")
        card.wait_for(state="visible", timeout=10_000)
        assert str(expected.get("name") or expected["id"]) in card.inner_text()
