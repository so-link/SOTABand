"""浏览器 e2e 的共享设施（Playwright）。

分层约定（见项目根 pyproject 的 markers 说明）：
- 浏览器用例默认不执行，需要 ``--run-e2e``（根 conftest 按 marker 跳过）。
- 缺 Playwright / Chromium / 前端 / 后端 / 登录凭据时 skip，不报红。

API 层（tests/api）用标准库直连后端；浏览器层只管「用户能看到什么、点了
会发生什么」，两层边界清晰，浏览器里不直接发业务请求。
"""

from __future__ import annotations

import json
import os
import urllib.request

import pytest

BACKEND_URL = os.environ.get("SOTABAND_E2E_BACKEND", "http://127.0.0.1:8201")
FRONTEND_URL = os.environ.get("SOTABAND_E2E_FRONTEND", "http://127.0.0.1:5173")
TOKEN_KEY = "sotaband-token"


# ────────────────────────── API 小工具（前置准备 / 结果校验） ──────────────────────────

def api_call(path: str, token: str = "", method: str = "GET",
             body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BACKEND_URL}{path}", data=data, method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw.strip() else {}


def platform_op(token: str, tool: str, args: dict, session_id: str = "e2e-setup") -> dict:
    """经平台网关执行操作（写操作自动确认），供用例准备前置状态。"""
    r = api_call("/api/chat/platform-call", token, "POST",
                 {"tool": tool, "args": args, "session_id": session_id})
    if r.get("status") == "pending":
        c = r["confirm"]
        r = api_call("/api/chat/platform-call/confirm", token, "POST",
                     {"opId": c["op_id"], "token": c["token"],
                      "fingerprint": c["fingerprint"]})
    return r


# ────────────────────────── fixtures ──────────────────────────

@pytest.fixture(scope="session")
def token() -> str:
    """登录令牌：普通测试账号优先，其次超管（.env 的 SOTABAND_ROOT_PASSWORD）。"""
    candidates = [
        (os.environ.get("SOTABAND_E2E_USER", "test"),
         os.environ.get("SOTABAND_E2E_PASSWORD", "")),
        ("root", os.environ.get("SOTABAND_ROOT_PASSWORD", "")),
    ]
    for user, password in candidates:
        if not password:
            continue
        try:
            r = api_call("/api/auth/login", method="POST",
                         body={"username": user, "password": password})
        except Exception:  # noqa: BLE001
            continue
        if r.get("token"):
            return r["token"]
    pytest.skip("没有可用的登录凭据（SOTABAND_E2E_PASSWORD 或 SOTABAND_ROOT_PASSWORD）")


@pytest.fixture(scope="session", autouse=True)
def _services_ready() -> None:
    for url, name in ((FRONTEND_URL, "前端"), (BACKEND_URL, "后端")):
        try:
            urllib.request.urlopen(url, timeout=3)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"{name}未就绪（{url}）: {e}")


@pytest.fixture(scope="session")
def browser():
    """Chromium 单例（整个 e2e 会话共享），缺依赖/浏览器时 skip。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("未安装 playwright：./venv/bin/pip install playwright "
                    "&& ./venv/bin/playwright install chromium")
    pw = sync_playwright().start()
    try:
        b = _launch(pw)
    except Exception as e:  # noqa: BLE001
        pw.stop()
        pytest.skip(f"Chromium 启动失败（先 ./venv/bin/playwright install chromium）: {e}")
    yield b
    b.close()
    pw.stop()


def _launch(pw):
    """启动 Chromium。

    当前容器里直接继承完整环境会让 headless Chrome 起不来（实测启动即
    退出），清空环境后正常；因此先按原环境试一次，失败再用最小环境重试，
    把 SOTABAND_E2E_* 变量带过去即可。
    """
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    try:
        return pw.chromium.launch(args=args)
    except Exception:  # noqa: BLE001
        keep = {k: v for k, v in os.environ.items() if k.startswith("SOTABAND_E2E_")}
        env = {
            "HOME": os.environ.get("HOME", "/root"),
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "LANG": "C.UTF-8",
            **keep,
        }
        return pw.chromium.launch(args=args, env=env)


@pytest.fixture
def page(browser, token):
    """已登录页面：令牌写进 localStorage 后重开页面，跳过登录弹窗。"""
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
    p = ctx.new_page()
    p.goto(FRONTEND_URL)
    p.evaluate(f"localStorage.setItem({json.dumps(TOKEN_KEY)}, {json.dumps(token)})")
    p.goto(FRONTEND_URL)
    p.wait_for_selector("textarea", timeout=40_000)
    yield p
    ctx.close()
