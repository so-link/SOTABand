"""对话：假 LLM 流式内容渲染、新会话、停止按钮。"""

from __future__ import annotations

import os

import pytest

from tests.e2e.helpers.selectors import S
from tests.e2e.helpers.wait import wait_until

pytestmark = pytest.mark.e2e


def _open_chat(page) -> None:
    tab = page.locator('[data-view="chat"]')
    if tab.count() > 0:
        tab.first.click()
    page.get_by_test_id(S.chat_input).wait_for(state="visible", timeout=20_000)


def _new_session(page) -> None:
    button = page.get_by_test_id("chat-new-session")
    if button.count() > 0:
        button.click()
        wait_until(
            lambda: page.locator('[data-role="user"]').count() == 0,
            timeout=10,
            description="新建对话后旧用户消息清空",
        )


def test_fake_llm_stream_is_rendered(page):
    _open_chat(page)
    _new_session(page)
    marker = f"E2E_CHAT_{os.environ.get('E2E_RUN_ID', 'local')}"
    page.get_by_test_id(S.chat_input).fill(marker)
    page.get_by_test_id(S.chat_send).click()

    expected = f"E2E_REPLY_{os.environ.get('E2E_RUN_ID', 'local')}"
    wait_until(
        lambda: expected in page.get_by_test_id(S.chat_messages).inner_text(),
        timeout=40,
        description=f"对话消息应包含假 LLM 标记 {expected}",
    )
    assert marker in page.get_by_test_id(S.chat_messages).inner_text()


def test_stop_button_interrupts_stream(page):
    _open_chat(page)
    _new_session(page)
    page.get_by_test_id(S.chat_input).fill("SLOWSTREAM_9f3c")
    page.get_by_test_id(S.chat_send).click()

    wait_until(
        lambda: "E2E_SLOW_CHUNK" in page.get_by_test_id(S.chat_messages).inner_text(),
        timeout=30,
        description="慢流内容开始渲染",
    )
    page.get_by_test_id(S.chat_stop).click()
    wait_until(
        lambda: page.get_by_test_id(S.chat_input).is_enabled(),
        timeout=20,
        description="停止后输入框恢复可用",
    )
    assert page.get_by_test_id(S.chat_send).count() >= 1
