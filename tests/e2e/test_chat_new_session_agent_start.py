"""浏览器端到端：新会话 + 自动确认 / 手工确认两路，启动 Agent 后继续原提问。

对应线上问题：新对话输入「广州明天天气怎么样？」→ 匹配到「城市信息查询
Agent」→ 该 Agent 未运行 → 启动后原始提问被丢弃。

## 检查项

1. **必须从新会话开始**：每例先在页面上点「新建对话」并断言对话区旧消息为
   0；跑完再断言本轮落在一个**新的独立会话**里，原有会话消息数不变。
2. **自动确认 / 手工确认分别测试**：
   - 手工确认：Agent 未运行时只提示用户确认启动（不声称已启动），用户点
     「确认执行」后自动继续处理原提问，不需要重新描述需求。
   - 自动确认：不点任何按钮自动启动，时间线上能看到启动这一步
     （「未运行，需确认后启动」+ 平台回执「平台操作已执行」），随后自动
     继续处理原提问。

## 运行

    SOTABAND_E2E_USER=test SOTABAND_E2E_PASSWORD=12345678 \
        ./venv/bin/python -m pytest tests/e2e -q --run-e2e
"""

from __future__ import annotations

import time

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import platform_op

AGENT_ID = "city-info-query-agent"
AGENT_NAME = "城市信息查询 Agent"
USER_REQUEST = "广州明天天气怎么样？"
# 本轮上限：交互 Agent 转交子 Agent 的 RPC 超时是 300s，这里要比它长，
# 否则还没等到结果就先断言失败。
TIMEOUT_MS = int(float(__import__("os").environ.get("SOTABAND_E2E_TIMEOUT", "420")) * 1000)

pytestmark = pytest.mark.e2e


# ────────────────────────── 页面动作 ──────────────────────────

def _set_confirm_policy(page, policy: str) -> None:
    select = page.locator("select").first
    select.select_option(policy)
    assert select.input_value() == policy, f"确认策略未能切换为 {policy}"


def _new_session(page) -> None:
    """点「新建对话」，断言对话区旧消息为 0（检查项 1）。"""
    button = page.get_by_role("button", name="新建对话")
    if button.count() == 0:
        # 空对话时不渲染该按钮，此时本身就是新会话
        assert page.locator('[data-role="user"]').count() == 0, \
            "找不到「新建对话」按钮，且对话区已有消息"
        return
    button.first.click()
    page.wait_for_function(
        "() => document.querySelectorAll('[data-role=\"user\"]').length === 0",
        timeout=10_000)
    assert page.locator('[data-role="user"]').count() == 0, \
        "新建对话后对话区仍有旧消息：用例不是从新会话开始"


def _send_request(page) -> None:
    page.fill("textarea", USER_REQUEST)
    page.press("textarea", "Enter")
    page.wait_for_selector('[data-role="user"]', timeout=30_000)
    page.wait_for_selector('[data-role="agent"]', timeout=60_000)


def _agent_text(page) -> str:
    return "\n".join(page.locator('[data-role="agent"]').all_inner_texts())


def _confirm_button(page):
    return page.get_by_role("button", name="确认执行", exact=True)


def _wait_result(page) -> str:
    """等本轮结束：最后一个助手气泡出现结果卡、输入框可用、内容不再增长。"""
    deadline = time.time() + TIMEOUT_MS / 1000
    last_signature = None
    stable_since = time.time()
    while time.time() < deadline:
        bubbles = page.locator('[data-role="agent"]')
        idle = page.locator("textarea").is_enabled()
        if bubbles.count():
            last = bubbles.last
            text = last.inner_text()
            # 气泡内的时间线每秒刷新，只比较「身份 + 长度」判断是否还在增长
            signature = (last.get_attribute("data-msg"), len(text))
            if idle and ("Agent 执行" in text or "执行失败" in text):
                if signature != last_signature:
                    last_signature, stable_since = signature, time.time()
                elif time.time() - stable_since > 1.5:
                    return _agent_text(page)
        page.wait_for_timeout(500)
    raise AssertionError(f"本轮未在 {TIMEOUT_MS / 1000}s 内给出 Agent 执行结果："
                         f"{_agent_text(page)[-800:]}")


def _session_snapshot(token: str) -> dict[str, int]:
    from tests.e2e.conftest import api_call

    out: dict[str, int] = {}
    for s in api_call("/api/chat/sessions?limit=50", token).get("sessions", []):
        sid = s.get("session_id", "")
        if sid:
            out[sid] = len(api_call(f"/api/chat/sessions/{sid}", token).get("messages", []))
    return out


# ────────────────────────── 用例 ──────────────────────────

@pytest.mark.parametrize("policy", ["auto", "manual"])
def test_new_session_agent_start(page, token, policy):
    before = _session_snapshot(token)
    # 前置：Agent 未运行，才能覆盖「启动 Agent」这条路径
    platform_op(token, "stop_agent", {"agent_id": AGENT_ID})
    time.sleep(1.2)

    # 浏览器操作全部走 Playwright 的自动等待/可操作性检查：
    # - expect_* 断言失败时自带重试与超时提示
    # - get_by_role 按无障碍角色定位，界面改样式不影响用例
    _set_confirm_policy(page, policy)
    _new_session(page)
    _send_request(page)

    if policy == "manual":
        confirm = _confirm_button(page)
        confirm.first.wait_for(state="visible", timeout=120_000)
        mid = _agent_text(page)
        assert "未运行" in mid and "需确认后启动" in mid, \
            f"没有展示「Agent 未运行，需确认后启动」这一步：{mid[-600:]}"
        assert "已就绪" not in mid, "用户尚未确认就声称已就绪"
        assert "平台操作已执行" not in mid and "Agent 执行" not in mid, \
            f"用户尚未确认，就已经出现了执行结果：{mid[-600:]}"
        assert "确认后会自动启动该 Agent" in mid, \
            f"没有提示用户「确认执行」即可启动：{mid[-600:]}"
        confirm.first.click()
    else:
        # 自动确认：不点按钮，卡片应被策略自动执行
        expect(page.locator("body")).to_contain_text("已确认", timeout=120_000)

    text = _wait_result(page)

    if policy == "auto":
        assert "未运行" in text and "平台操作已执行" in text, \
            f"自动确认下时间线里没有展示启动这一步：{text[-800:]}"
    else:
        assert "平台操作已执行" in text, \
            f"确认后时间线里看不到启动被执行这一步：{text[-800:]}"

    # 原提问被真正处理
    assert "Agent 执行" in text, f"没有出现 Agent 执行结果卡：{text[-800:]}"
    assert "暂时没有获取到" not in text, f"Agent 未返回天气内容：{text[-800:]}"
    assert "请重新描述" not in text and "回复我一句（如「继续」）" not in text, \
        f"仍在要求用户手动续跑：{text[-800:]}"

    # 检查项 1：本轮落在新会话里，旧会话没有被续写
    after = _session_snapshot(token)
    new_sessions = [s for s in after if s not in before]
    assert new_sessions, f"没有产生新会话，候选: {sorted(after)}"
    grown = [s for s, n in after.items() if s in before and n != before[s]]
    assert not grown, f"旧会话被续写了: {grown}"
