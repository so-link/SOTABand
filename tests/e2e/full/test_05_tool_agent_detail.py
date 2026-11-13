"""工具/Agent 详情：名称、规范、代码来自真实接口并渲染。"""

from __future__ import annotations

import re

import pytest

from tests.e2e.helpers.selectors import S

pytestmark = pytest.mark.e2e


def _open_tool_repository(page):
    page.get_by_test_id(S.left_tab_resources).click()
    entry = page.get_by_text(re.compile("工具仓库另有"))
    entry.wait_for(state="visible", timeout=15_000)
    entry.click()
    page.get_by_test_id(S.repository_root).wait_for(state="visible", timeout=15_000)


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:40]
    return ""


def test_tool_detail_renders_name_spec_and_code(page, api, fixtures):
    tool = (fixtures.get("tools") or [])[0]
    detail = api.get(f"/api/tool/{tool['id']}")
    assert detail.status == 200, detail.text
    payload = detail.json()
    _open_tool_repository(page)
    page.get_by_test_id("tool-repository-search").fill(tool["id"])
    card = page.get_by_test_id(f"tool-repository-card-{tool['id']}")
    card.wait_for(state="visible", timeout=15_000)
    card.click()

    root = page.get_by_test_id(S.tool_detail_root)
    root.wait_for(state="visible", timeout=15_000)
    assert tool["name"] in page.get_by_test_id(S.tool_detail_name).inner_text()

    spec = str(payload.get("spec_md") or "")
    if spec:
        page.get_by_test_id("tool-md-toggle").click()
        needle = _first_nonempty_line(spec)
        if needle:
            assert needle in root.inner_text()

    code = str(payload.get("code") or "")
    if code:
        page.get_by_test_id("tool-code-toggle").click()
        assert ("def execute" in root.inner_text()) or ("import" in root.inner_text())


def test_agent_detail_renders_name_spec_and_status(page, api, fixtures):
    agent = (fixtures.get("agents") or [])[0]
    detail = api.get(f"/api/agent/{agent['id']}")
    assert detail.status == 200, detail.text
    payload = detail.json()

    page.get_by_test_id(S.left_tab_resources).click()
    row = page.get_by_test_id(S.resource_row("agent", agent["id"]))
    row.wait_for(state="visible", timeout=15_000)
    row.click()

    root = page.get_by_test_id(S.agent_detail_root)
    root.wait_for(state="visible", timeout=15_000)
    assert agent["name"] in page.get_by_test_id(S.agent_detail_name).inner_text()
    assert ("运行中" in root.inner_text()) or ("未启动" in root.inner_text())

    spec = str(payload.get("spec_md") or "")
    if spec:
        page.get_by_test_id("agent-md-toggle").click()
        needle = _first_nonempty_line(spec)
        if needle:
            assert needle in root.inner_text()
