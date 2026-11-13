"""数据空间预览（核心回归）：接口文件清单/表格/图片/规范必须被页面消费。"""

from __future__ import annotations

import pytest

from tests.e2e.helpers.consumption import ConsumptionRule, expect_api_consumed, expect_image_loaded
from tests.e2e.helpers.selectors import S
from tests.e2e.helpers.wait import wait_until

pytestmark = pytest.mark.e2e


def _open_fresh_data_preview(page, dataset_id: str) -> None:
    """先离开数据预览再点数据集，确保本次点击会重新拉 preview。"""
    chat_tab = page.locator('[data-view="chat"]')
    if chat_tab.count() > 0:
        chat_tab.first.click()
    page.get_by_test_id(S.left_tab_resources).click()
    row = page.get_by_test_id(S.resource_row("data", dataset_id))
    row.wait_for(state="visible", timeout=15_000)
    row.click()


def _pick_dataset_with_files(page, api, fixtures):
    """选一个 preview 接口有文件的 seed 数据集，并返回 (dataset, preview)。"""
    for dataset in fixtures.get("datasets") or []:
        dataset_id = dataset["id"]
        response = api.get(f"/api/data/{dataset_id}/preview")
        if response.status != 200 or not isinstance(response.payload, dict):
            continue
        files = response.payload.get("files") or []
        if files:
            return dataset, response.payload
    raise AssertionError("seed 数据集中没有可预览文件")


def test_preview_files_and_table_are_consumed(page, api, fixtures):
    dataset, preview = _pick_dataset_with_files(page, api, fixtures)
    dataset_id = dataset["id"]

    page.get_by_test_id(S.left_tab_resources).click()
    row = page.get_by_test_id(S.resource_row("data", dataset_id))
    row.wait_for(state="visible", timeout=15_000)
    # 先切到对话再点数据集；否则页面可能已由 prefs 恢复在数据预览，不会再发 preview。
    page.locator('[data-view="chat"]').click()
    page.wait_for_timeout(200)

    rule = ConsumptionRule(
        api=f"/api/data/{dataset_id}/preview",
        fields=["files[].name"],
        require=min(3, len(preview.get("files") or [])),
        scope=f'[data-testid="{S.data_preview_files}"]',
    )
    expect_api_consumed(page, rule, row.click, timeout=25_000)

    page.get_by_test_id(S.data_preview_root).wait_for(state="visible", timeout=15_000)
    file_rows = page.get_by_test_id(S.data_preview_file_row)
    wait_until(
        lambda: file_rows.count() == len(preview["files"]),
        timeout=15,
        description="右栏文件行数应与 preview.files 一致",
    )
    for item in preview["files"]:
        assert page.locator(f'[data-file-name="{item["name"]}"]').count() >= 1

    image_items = [
        item for item in preview["files"]
        if str(item.get("format", "")).lower() in {"png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"}
    ]
    if image_items:
        expect_image_loaded(page, f'[data-testid="{S.data_preview_image}"]', timeout=20_000)

    table_items = [
        item for item in preview["files"]
        if str(item.get("format", "")).lower() in {"csv", "tsv", "xlsx", "xls", "parquet", "pq"}
    ]
    if table_items:
        table = page.get_by_test_id(S.data_table_root)
        table.wait_for(state="visible", timeout=25_000)
        assert table.locator("thead th").count() >= 2
        assert table.locator("tbody tr").count() >= 1


def test_preview_spec_markdown_is_consumed(page, api, fixtures):
    dataset, preview = _pick_dataset_with_files(page, api, fixtures)
    spec_md = str(preview.get("spec_md") or "").strip()
    if not spec_md:
        pytest.skip("该 seed 数据集没有 spec_md")

    page.locator('[data-view="chat"]').click()
    page.get_by_test_id(S.left_tab_resources).click()
    row = page.get_by_test_id(S.resource_row("data", dataset["id"]))
    row.wait_for(state="visible", timeout=15_000)
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/data/{dataset['id']}/preview"),
        timeout=20_000,
    ):
        row.click()

    page.get_by_test_id(S.data_preview_root).wait_for(state="visible", timeout=15_000)
    toggle = page.get_by_role("button", name="MD 文档")
    if toggle.count() == 0:
        pytest.skip("界面没有 MD 文档入口")
    toggle.first.click()
    spec = page.get_by_test_id(S.data_preview_spec)
    spec.wait_for(state="visible", timeout=10_000)
    # spec 可能很长；取第一行可辨识文本做对账
    needle = next((line.strip("# ").strip() for line in spec_md.splitlines() if line.strip()), "")
    assert needle
    assert needle[:40] in spec.inner_text()
