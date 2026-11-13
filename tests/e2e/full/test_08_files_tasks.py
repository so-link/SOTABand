"""文件：上传 Markdown → 文件树 → 预览内容。任务列表走 API 覆盖。"""

from __future__ import annotations

import time

import pytest

from tests.e2e.helpers.selectors import S
from tests.e2e.helpers.wait import wait_until

pytestmark = pytest.mark.e2e


def test_upload_markdown_and_preview(page):
    marker = f"E2E_UPLOAD_{int(time.time())}"
    page.get_by_test_id(S.left_tab_files).click()
    page.get_by_test_id(S.file_tree_root).wait_for(state="visible", timeout=15_000)
    file_input = page.locator(f'[data-testid="{S.file_tree_root}"] input[type=file]').first
    with page.expect_response(
        lambda response: response.url.endswith("/api/file/upload")
        and response.request.method == "POST",
        timeout=30_000,
    ):
        file_input.set_input_files(
            {
                "name": "e2e-note.md",
                "mimeType": "text/markdown",
                "buffer": f"# E2E 文件\n\n唯一标记：{marker}\n".encode(),
            }
        )

    node = page.get_by_test_id(S.file_node("e2e-note.md"))
    node.wait_for(state="visible", timeout=20_000)
    node.dblclick()

    preview = page.get_by_test_id(S.file_preview_root)
    preview.wait_for(state="visible", timeout=20_000)
    wait_until(
        lambda: marker in preview.inner_text(),
        timeout=20,
        description="文件预览应渲染上传内容",
    )
