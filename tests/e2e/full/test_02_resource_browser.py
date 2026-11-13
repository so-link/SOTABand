"""资源浏览器：四分区计数/名称来自真实接口，点击数据集打开预览。"""

from __future__ import annotations

import pytest

from tests.e2e.helpers.selectors import S
from tests.e2e.helpers.wait import wait_until

pytestmark = pytest.mark.e2e


def _count(page, resource_type: str) -> str:
    return page.get_by_test_id(S.resource_count(resource_type)).inner_text().strip()


def test_sections_counts_match_api(page, api, fixtures):
    page.get_by_test_id(S.left_tab_resources).click()
    page.get_by_test_id(S.resource_root).wait_for(state="visible", timeout=10_000)

    data_body = api.get("/api/data/list", params={"available_only": "true"}).json()
    tool_body = api.get("/api/tool/workspace").json()
    model_body = api.get("/api/model/list").json()
    agent_body = api.get("/api/agent/list").json()
    expected = {
        "data": len(data_body.get("datasets") or []),
        "tool": len(tool_body.get("tools") or []),
        "model": len(model_body.get("models") or []),
        "agent": len(agent_body.get("agents") or []),
    }

    for resource_type, count in expected.items():
        section = page.get_by_test_id(S.resource_section(resource_type))
        section.scroll_into_view_if_needed()
        section.wait_for(state="visible", timeout=10_000)
        wait_until(
            lambda rt=resource_type, n=count: _count(page, rt) == str(n),
            timeout=15,
            description=f"{resource_type} 计数应为 {count}",
        )

    dataset = (fixtures.get("datasets") or [])[0]
    row = page.get_by_test_id(S.resource_row("data", dataset["id"]))
    row.wait_for(state="visible", timeout=10_000)
    assert dataset["name"] in row.inner_text()


def test_click_dataset_opens_data_preview(page, fixtures):
    page.get_by_test_id(S.left_tab_resources).click()
    dataset = (fixtures.get("datasets") or [])[0]
    row = page.get_by_test_id(S.resource_row("data", dataset["id"]))
    row.wait_for(state="visible", timeout=15_000)
    row.click()
    page.get_by_test_id(S.data_preview_root).wait_for(state="visible", timeout=15_000)
    page.locator(S.view_tab(f"data-preview:{dataset['id']}")).wait_for(
        state="visible", timeout=10_000
    )
    assert dataset["name"] in page.get_by_test_id(S.data_preview_title).inner_text()
