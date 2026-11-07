"""API 层（进程内）：工具生成类 SSE 端点的事件序列与校验。

这些端点是「新需求 → 规范 → 代码 → 自动调试」的主链路，前端按事件名渲染
思考过程/时间线。此前完全无测试，回归依靠手点；这里用 FakeLLM 锁定契约：

- generate-spec：description 为空 400；正常时 reasoning → delta… → done
- generate-code：spec_md 为空 400；缺必需段落 400；正常时 done 带 code
- modify-code：工具不存在 404；request 为空 400；正常时 done 带 modified_code
- auto-debug：test_input_json 非法 400/500；tool_id 非法 400；能产出
  debug_start → round_start → exec_result → done，且「停止」接口 400 校验
"""

from __future__ import annotations

import pytest

from tests.api import _stream_support as S

pytestmark = pytest.mark.api

TOOL_ID = "api-stream-tool"


def _tool_routes():
    from app.api.routes import tool_routes
    return tool_routes


# ────────────────────────── generate-spec ──────────────────────────

def test_generate_spec_rejects_empty_description(client):
    events = S.read_sse(client, "POST", "/api/tool/generate-spec",
                        json={"description": "   "})
    assert events and events[0][0] == "http_error", events
    assert events[0][1]["status"] == 400, events


def test_generate_spec_streams_and_returns_spec(client, monkeypatch, fake_llm):
    S.patch_llm(monkeypatch, _tool_routes(), fake_llm)
    events = S.read_sse(client, "POST", "/api/tool/generate-spec",
                        json={"description": "做一个平方计算工具"})
    flow = S.names(events)
    assert "error" not in flow, events
    assert flow[0] == "reasoning", f"事件流应以思考开始: {flow}"
    assert "delta" in flow, f"没有流式正文: {flow}"
    assert flow[-1] == "done", f"事件流必须以 done 收尾: {flow}"
    done = dict(events)["done"]
    assert "spec_md" in done, f"done 事件缺规范正文: {done}"


# ────────────────────────── generate-code ──────────────────────────

def test_generate_code_rejects_empty_spec(client):
    events = S.read_sse(client, "POST", "/api/tool/generate-code",
                        json={"specMd": ""})
    assert events[0][0] == "http_error" and events[0][1]["status"] == 400, events


def test_generate_code_rejects_incomplete_spec(client, monkeypatch, fake_llm):
    S.patch_llm(monkeypatch, _tool_routes(), fake_llm)
    events = S.read_sse(client, "POST", "/api/tool/generate-code",
                        json={"specMd": "# 缺段落的规范\n\n只有一句话。", "toolId": TOOL_ID})
    assert events[0][0] == "http_error" and events[0][1]["status"] == 400, events


def test_generate_code_streams_done_with_code(client, monkeypatch, fake_llm):
    # 代码生成走 builder 的 chat_stream，让它返回可解析的工具代码
    fake_llm.reply = S.TOOL_CODE
    S.patch_llm(monkeypatch, _tool_routes(), fake_llm)
    events = S.read_sse(client, "POST", "/api/tool/generate-code",
                        json={"specMd": S.TOOL_SPEC_MD, "toolId": TOOL_ID,
                              "toolName": "平方计算器"})
    flow = S.names(events)
    assert "error" not in flow, events
    assert flow[-1] == "done", f"事件流必须以 done 收尾: {flow}"
    done = dict(events)["done"]
    assert str(done.get("code", "")).strip(), f"done 事件没有代码: {done}"


# ────────────────────────── modify-code ──────────────────────────

def test_modify_code_unknown_tool_404(client):
    events = S.read_sse(client, "POST",
                        "/api/tool/not-exist-tool-xyz/modify-code",
                        json={"currentCode": "print(1)", "request": "加个参数"})
    assert events[0][0] == "http_error" and events[0][1]["status"] == 404, events


def test_modify_code_rejects_empty_request(client):
    events = S.read_sse(client, "POST", f"/api/tool/{TOOL_ID}/modify-code",
                        json={"currentCode": "print(1)", "request": "  "})
    assert events[0][0] == "http_error", events
    assert events[0][1]["status"] in (400, 404), events


# ────────────────────────── auto-debug ──────────────────────────

def test_auto_debug_rejects_invalid_test_input(client):
    """非法 test_input_json 不能被当成正常请求跑下去。

    现状：路由里的 ``json.loads`` 没有捕获，直接抛异常 → 500 空响应体。
    这里用 ``raise_server_exceptions=False`` 观察真实返回码，并把 500 记为
    已知缺陷（xfail strict=False）：一旦修成 400，用例转成正常通过。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    # TestClient 默认会把服务端异常重新抛出；这里要看真实状态码
    raw = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    resp = raw.post("/api/tool/auto-debug",
                    data={"spec_md": "", "tool_id": "", "tool_name": "",
                          "code": "", "test_input_json": "not-json"})
    assert resp.status_code != 200, "坏 JSON 被当成正常请求处理了"
    if resp.status_code == 500:
        pytest.xfail("已知缺陷：auto-debug 对非法 test_input_json 未校验，直接 500")


def test_auto_debug_rejects_bad_tool_id(client):
    events = S.read_sse(client, "POST", "/api/tool/auto-debug",
                        data={"spec_md": S.TOOL_SPEC_MD, "tool_id": "../evil",
                              "tool_name": "x", "code": S.TOOL_CODE,
                              "test_input_json": "{}"})
    assert events[0][0] == "http_error" and events[0][1]["status"] == 400, events


def test_auto_debug_stream_has_round_events(client, monkeypatch, fake_llm):
    """一次通过时：debug_start → round_start → exec_result → done。"""
    routes = _tool_routes()
    S.patch_llm(monkeypatch, routes, fake_llm)
    S.patch_sandbox(monkeypatch, routes)
    events = S.read_sse(client, "POST", "/api/tool/auto-debug",
                        data={"spec_md": S.TOOL_SPEC_MD, "tool_id": TOOL_ID,
                              "tool_name": "平方计算器", "code": S.TOOL_CODE,
                              "test_input_json": '{"n": 5}'})
    flow = S.names(events)
    assert flow[0] == "debug_start", f"应先发 debug_start: {flow}"
    assert "round_start" in flow and "exec_result" in flow, f"缺少调试轮次事件: {flow}"
    assert flow[-1] in ("done", "error"), f"事件流未收敛: {flow}"


def test_auto_debug_stop_requires_tool_id(client):
    resp = client.post("/api/tool/auto-debug/stop", json={})
    assert resp.status_code == 400, resp.text
    resp = client.post("/api/tool/auto-debug/stop", json={"tool_id": TOOL_ID})
    assert resp.status_code == 200 and resp.json().get("status") == "ok", resp.text
