"""API 层：Agent 模块关键链路。

覆盖用户实际会踩到的编辑 / 覆盖层 / 导出 / 服务信息路径：

1. 详情 / 列表 / 状态：字段齐全；不存在 → 404
2. 保存规范 / 代码 / 需求：空内容 400；内置资源非超管 403；私有副本可写
3. fork 覆盖层：内置 fork → fork-status 标记 → 丢弃后回落内置（无副本 400）
4. 导出：application/zip（.sotaagent.zip）；不存在 → 404
5. Web 服务式：service 返回 API 定义；api-doc / 下载可用；服务未启动时
   test-api → 400，未声明的 API 名 → 404
6. 对话历史：迁移 → 读取 → 清空（走真实 SQLite）

副作用约束：本套用例**不修改内置 Agent、不碰共享 Agent**（超管密码未配置，
改内置必然 403）。所有写入都发生在用例自己注册的临时私有 Agent 上，并在
finally 中删除，避免污染开发环境的私有资源根。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid

import pytest

pytestmark = pytest.mark.api

BUILTIN_AGENT = "message-router-agent"   # 内置 web-service：声明了 API，用于只读用例
SHARED_AGENT = "etf-target-price-agent"  # 共享 Agent：只读，不写

# 临时私有 Agent 的规范：合法 id + 交互式角色，代码只需通过静态审查
TEMP_SPEC = """---
id: {agent_id}
name: API 测试 Agent
version: 0.1.0
role: interactive
status: active
---

# API 测试 Agent

## 1. 功能概述

仅用于接口测试，用例结束即删除。

## 2. 输入规范

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | 是 | 用户输入 |
"""

TEMP_CODE = '''"""接口测试用的最小 Agent"""


def execute(content: str):
    return {"content": content}
'''


# ────────────────────────── HTTP 小工具 ──────────────────────────

def _call(base: str, token: str, path: str, method: str = "GET",
          body: dict | None = None, timeout: float = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                return resp.status, json.loads(payload.decode()), resp.headers
            return resp.status, payload, resp.headers
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw), e.headers
        except json.JSONDecodeError:
            return e.code, {"raw": raw}, e.headers


# ────────────────────────── 临时 Agent 夹具 ──────────────────────────

class _TempAgent:
    def __init__(self, base: str, token: str):
        self.base = base
        self.token = token
        self.id = f"api-test-agent-{uuid.uuid4().hex[:8]}"

    def create(self) -> "_TempAgent":
        status, body, _ = _call(self.base, self.token, "/api/agent/register", "POST", {
            "specMd": TEMP_SPEC.format(agent_id=self.id),
            "code": TEMP_CODE,
            "agentId": self.id,
            "agentName": "API 测试 Agent",
            "role": "interactive",
            "demandDesc": "接口测试临时 Agent",
        })
        assert status == 200, f"注册临时 Agent 失败: {status} {body}"
        assert body.get("agent_id") == self.id, body
        return self

    def delete(self) -> None:
        status, body, _ = _call(self.base, self.token,
                                f"/api/agent/{self.id}", "DELETE")
        assert status in (200, 404), f"清理临时 Agent 失败: {status} {body}"


@pytest.fixture
def temp_agent(backend_url, backend_token):
    agent = _TempAgent(backend_url, backend_token).create()
    try:
        yield agent
    finally:
        agent.delete()


# ─────────────────────── 列表 / 详情 / 状态 ───────────────────────

def test_agent_list_contains_builtin(backend_url, backend_token):
    status, body, _ = _call(backend_url, backend_token, "/api/agent/list")
    assert status == 200 and isinstance(body.get("agents"), list)
    ids = [a.get("id") for a in body["agents"]]
    assert BUILTIN_AGENT in ids, f"内置 Agent 不在可见列表里: {ids}"


def test_agent_detail_returns_spec_and_code(backend_url, backend_token):
    status, body, _ = _call(backend_url, backend_token, f"/api/agent/{BUILTIN_AGENT}")
    assert status == 200, body
    assert body.get("id") == BUILTIN_AGENT
    # 编辑器依赖这些字段渲染，缺任一就是空白页
    for key in ("spec_md", "code", "has_demand", "source"):
        assert key in body, f"详情缺少字段 {key}: {list(body)}"
    assert body["spec_md"].strip(), "详情没有返回规范文档"
    assert body["code"].strip(), "详情没有返回实现代码"


def test_agent_detail_404(backend_url, backend_token):
    status, _, _ = _call(backend_url, backend_token, "/api/agent/not-exist-agent-xyz")
    assert status == 404


def test_agent_status_reports_running_flag(backend_url, backend_token):
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{BUILTIN_AGENT}/status")
    assert status == 200, body
    assert body.get("id") == BUILTIN_AGENT
    assert isinstance(body.get("running"), bool), body


# ─────────────────────── 保存规范 / 代码 / 需求 ───────────────────────

def test_save_spec_empty_rejected(backend_url, backend_token, temp_agent):
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{temp_agent.id}/save-spec", "POST",
                            {"spec_md": "   "})
    assert status == 400, f"空规范应拒绝: {status} {body}"


def test_save_spec_on_builtin_requires_superuser(backend_url, backend_token):
    """内置 Agent 不能被普通用户就地改写（避免污染全平台共享规范）。"""
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{BUILTIN_AGENT}/save-spec", "POST",
                            {"spec_md": "# 覆盖内置\n"})
    assert status == 403, f"普通用户改内置 Agent 应 403: {status} {body}"


def test_save_spec_on_private_agent_persists(backend_url, backend_token, temp_agent):
    new_spec = TEMP_SPEC.format(agent_id=temp_agent.id) + "\n## 备注\n\n接口测试改写的规范。\n"
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{temp_agent.id}/save-spec", "POST",
                            {"spec_md": new_spec})
    assert status == 200 and body.get("saved") == temp_agent.id, body

    _, detail, _ = _call(backend_url, backend_token, f"/api/agent/{temp_agent.id}")
    assert "接口测试改写的规范" in detail.get("spec_md", ""), "规范没有落盘"


def test_save_code_empty_rejected(backend_url, backend_token, temp_agent):
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{temp_agent.id}/save-code", "POST",
                            {"code": "  "})
    assert status == 400, f"空代码应拒绝: {status} {body}"


def test_save_code_rejects_unsafe_code(backend_url, backend_token, temp_agent):
    bad = "import os\nos.system('rm -rf /tmp/x')\n"
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{temp_agent.id}/save-code", "POST",
                            {"code": bad})
    assert status == 400 and "安全审查" in str(body.get("detail", "")), \
        f"危险代码未被静态审查拦下: {status} {body}"


def test_save_code_on_private_agent_persists(backend_url, backend_token, temp_agent):
    code = TEMP_CODE + "\n# 接口测试标记\n"
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{temp_agent.id}/save-code", "POST",
                            {"code": code})
    assert status == 200 and body.get("saved") == temp_agent.id, body

    _, detail, _ = _call(backend_url, backend_token, f"/api/agent/{temp_agent.id}")
    assert "接口测试标记" in detail.get("code", ""), "代码没有落盘"


def test_save_step1_on_private_agent(backend_url, backend_token, temp_agent):
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{temp_agent.id}/save-step1", "POST",
                            {"demand_md": "把输入转成小写后返回"})
    assert status == 200 and "demand" in body.get("written", []), body

    _, detail, _ = _call(backend_url, backend_token, f"/api/agent/{temp_agent.id}")
    assert detail.get("has_demand") is True
    assert "小写" in detail.get("demand_md", ""), "需求描述没有落盘"


# ────────────────────────── fork 覆盖层 ──────────────────────────

def test_fork_status_on_builtin_without_copy(backend_url, backend_token):
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{BUILTIN_AGENT}/fork-status")
    assert status == 200, body
    assert body.get("is_builtin") is True
    # 开发环境里若有人手工 fork 过，这里只断言字段自洽，不强制无副本
    assert isinstance(body.get("has_copy"), bool)
    assert body.get("forked") is False or body.get("has_copy") is True


def test_fork_rejects_non_builtin(backend_url, backend_token):
    """共享 / 私有 Agent 不是内置资源，fork 直接 400 并提示就地编辑。"""
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{SHARED_AGENT}/fork", "POST")
    assert status == 400 and "内置" in str(body.get("detail", "")), \
        f"非内置 Agent 不应允许 fork: {status} {body}"


def test_fork_status_of_fresh_private_agent(backend_url, backend_token, temp_agent):
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{temp_agent.id}/fork-status")
    assert status == 200, body
    assert body.get("is_builtin") is False
    assert body.get("has_copy") is True       # 私有层里的条目本身就是「副本」
    assert body.get("forked") is False        # 但不是从内置复制来的
    assert body.get("forked_from") is None


# ────────────────────────── 导出 ──────────────────────────

def test_export_agent_returns_zip(backend_url, backend_token, temp_agent):
    status, body, headers = _call(backend_url, backend_token,
                                  f"/api/agent/{temp_agent.id}/export")
    assert status == 200, body
    assert "application/zip" in headers.get("content-type", "")
    disp = headers.get("content-disposition", "")
    assert ".sotaagent.zip" in disp, f"导出文件名不符合约定: {disp}"
    assert body[:2] == b"PK", "导出内容不是 zip（缺少 PK 头）"


def test_export_agent_404(backend_url, backend_token):
    status, _, _ = _call(backend_url, backend_token,
                         "/api/agent/not-exist-agent-xyz/export")
    assert status == 404


# ──────────────────── Web 服务式：service / api-doc / test-api ────────────────────

def test_service_returns_api_definitions(backend_url, backend_token):
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{BUILTIN_AGENT}/service")
    assert status == 200, body
    assert body.get("agent_id") == BUILTIN_AGENT
    assert isinstance(body.get("running"), bool)
    apis = body.get("apis") or []
    assert apis, "web-service Agent 应返回 API 定义"
    for a in apis:
        for key in ("name", "method", "path"):
            assert key in a, f"API 定义缺少字段 {key}: {a}"


def test_api_doc_markdown_available(backend_url, backend_token):
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{BUILTIN_AGENT}/api-doc")
    assert status == 200, body
    md = body.get("markdown", "")
    assert md.startswith("#"), "api-doc 没有返回 Markdown"
    assert BUILTIN_AGENT in md


def test_api_doc_download_as_attachment(backend_url, backend_token):
    status, body, headers = _call(backend_url, backend_token,
                                  f"/api/agent/{BUILTIN_AGENT}/api-doc/download")
    assert status == 200
    assert "text/markdown" in headers.get("content-type", "")
    assert f"{BUILTIN_AGENT}-api.md" in headers.get("content-disposition", "")
    assert b"#" in body


def test_api_doc_400_for_non_web_service_agent(backend_url, backend_token, temp_agent):
    """交互式 Agent 未声明 API：文档接口应明确 400，而不是返回空文档。"""
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{temp_agent.id}/api-doc")
    assert status == 400, f"非 Web 服务式 Agent 应 400: {status} {body}"


def test_service_of_private_agent_has_no_apis(backend_url, backend_token, temp_agent):
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{temp_agent.id}/service")
    assert status == 200, body
    assert body.get("running") is False
    assert body.get("apis") == []


def test_test_api_400_when_service_not_running(backend_url, backend_token):
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/agent/{BUILTIN_AGENT}/test-api", "POST",
                            {"api_name": "route", "params": {}})
    # 服务未启动 → 400（服务真在跑时这条断言需换环境，故容忍 200 并校验结构）
    if status == 200:
        assert "status_code" in body and "result" in body, body
    else:
        assert status == 400 and "未运行" in str(body.get("detail", "")), body


# ────────────────────────── 对话历史 ──────────────────────────

def test_agent_chat_history_migrate_read_and_clear(backend_url, backend_token, temp_agent):
    """浏览器旧对话迁移 → 读取 → 再次迁移被拒 → 清空。"""
    path = f"/api/agent/{temp_agent.id}/chat-history"
    _call(backend_url, backend_token, path, "DELETE")     # 归一为空历史
    try:
        status, body, _ = _call(backend_url, backend_token,
                                f"{path}/migrate", "POST", {"messages": [
                                    {"role": "user", "content": "你好"},
                                    {"role": "agent", "content": "在的"},
                                    {"role": "system", "content": "应被忽略"},
                                ]})
        assert status == 200 and body.get("migrated") == 2, body

        status, history, _ = _call(backend_url, backend_token, path)
        assert status == 200
        contents = [m.get("content") for m in history.get("messages", [])]
        assert contents == ["你好", "在的"], f"历史顺序或内容不正确: {history}"

        # 已有历史时不重复导入
        status, again, _ = _call(backend_url, backend_token,
                                 f"{path}/migrate", "POST",
                                 {"messages": [{"role": "user", "content": "再来"}]})
        assert status == 200 and again.get("migrated") == 0
        assert again.get("reason") == "already_has_history", again

        status, cleared, _ = _call(backend_url, backend_token, path, "DELETE")
        assert status == 200 and cleared.get("deleted") == temp_agent.id
        _, empty, _ = _call(backend_url, backend_token, path)
        assert empty.get("messages") == []
    finally:
        _call(backend_url, backend_token, path, "DELETE")


def test_agent_chat_history_404(backend_url, backend_token):
    status, _, _ = _call(backend_url, backend_token,
                         "/api/agent/not-exist-agent-xyz/chat-history")
    assert status == 404
