"""浏览器 e2e 共享设施（Python Playwright + pytest）。

保留原有 ``browser`` / ``token`` / ``page`` 契约，并增加：
- ``admin_token`` / ``api`` / ``admin_api`` / ``fixtures``
- ``anon_page`` / ``page_factory``（多身份、登录框场景）
- 每个页面自动挂载 coverage 记录与静默失败守卫
- 失败时截图到 ``dist/e2e-report/screenshots/``

API 层（tests/api）仍用标准库直连后端；本文件只做浏览器编排与测试前置。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.e2e.helpers.api import ApiClient
from tests.e2e.helpers.coverage import coverage_file, record_hit, record_page_requests
from tests.e2e.helpers.fixtures import load_fixtures
from tests.e2e.helpers.guards import Guards

BACKEND_URL = os.environ.get("SOTABAND_E2E_BACKEND", "http://127.0.0.1:8211")
FRONTEND_URL = os.environ.get("SOTABAND_E2E_FRONTEND", "http://127.0.0.1:5183")
TOKEN_KEY = "sotaband-token"
SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "dist" / "e2e-report" / "screenshots"
_TRACKED: dict[str, list[tuple[object, Guards]]] = {}


# ────────────────────────── API 小工具（前置准备 / 结果校验） ──────────────────────────

def api_call(
    path: str,
    token: str = "",
    method: str = "GET",
    body: dict | None = None,
    timeout: int = 30,
) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{BACKEND_URL}{path}"
    record_hit(method, url, source="conftest")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw.strip() else {}


def platform_op(
    token: str, tool: str, args: dict, session_id: str = "e2e-setup"
) -> dict:
    """经平台网关执行操作（写操作自动确认），供用例准备前置状态。"""
    response = api_call(
        "/api/chat/platform-call",
        token,
        "POST",
        {"tool": tool, "args": args, "session_id": session_id},
    )
    if response.get("status") == "pending":
        confirm = response["confirm"]
        response = api_call(
            "/api/chat/platform-call/confirm",
            token,
            "POST",
            {
                "opId": confirm["op_id"],
                "token": confirm["token"],
                "fingerprint": confirm["fingerprint"],
            },
        )
    return response


# ────────────────────────── pytest 报告钩子 ──────────────────────────

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)
    if report.when != "call":
        return
    pages = _TRACKED.get(item.nodeid, [])
    if report.failed:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        for index, (page, _guards) in enumerate(pages):
            name = f"{item.name}-{index}.png".replace("/", "_").replace(" ", "_")
            try:
                page.screenshot(path=str(SCREENSHOT_DIR / name), full_page=True)
            except Exception:
                pass


# ────────────────────────── fixtures ──────────────────────────

@pytest.fixture(scope="session")
def token() -> str:
    """普通测试账号优先，其次超管。"""
    candidates = [
        (
            os.environ.get("SOTABAND_E2E_USER", "test"),
            os.environ.get("SOTABAND_E2E_PASSWORD", ""),
        ),
        ("root", os.environ.get("SOTABAND_ROOT_PASSWORD", "")),
    ]
    for user, password in candidates:
        if not password:
            continue
        try:
            client = ApiClient.login(BACKEND_URL, user, password, coverage_file())
            return client.token
        except Exception:  # noqa: BLE001
            continue
    pytest.skip(
        "没有可用的登录凭据（SOTABAND_E2E_PASSWORD 或 SOTABAND_ROOT_PASSWORD）"
    )


@pytest.fixture(scope="session")
def admin_token() -> str:
    password = os.environ.get("SOTABAND_ROOT_PASSWORD", "")
    if not password:
        pytest.skip("缺少 SOTABAND_ROOT_PASSWORD，无法使用管理员身份")
    username = os.environ.get("SOTABAND_ROOT_USERNAME", "root")
    client = ApiClient.login(BACKEND_URL, username, password, coverage_file())
    return client.token


@pytest.fixture(scope="session")
def fixtures() -> dict:
    return load_fixtures()


@pytest.fixture()
def api(token: str) -> ApiClient:
    return ApiClient(BACKEND_URL, token, coverage_file())


@pytest.fixture()
def admin_api(admin_token: str) -> ApiClient:
    return ApiClient(BACKEND_URL, admin_token, coverage_file())


@pytest.fixture(scope="session", autouse=True)
def _services_ready() -> None:
    for url, name in ((FRONTEND_URL, "前端"), (BACKEND_URL, "后端")):
        try:
            urllib.request.urlopen(url, timeout=3)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"{name}未就绪（{url}）: {exc}")


@pytest.fixture(scope="session")
def browser():
    """Chromium 单例（整个 e2e 会话共享），缺依赖/浏览器时 skip。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip(
            "未安装 playwright：./venv/Scripts/python -m pip install playwright "
            "&& ./venv/Scripts/playwright install chromium"
        )
    pw = sync_playwright().start()
    try:
        browser = _launch(pw)
    except Exception as exc:  # noqa: BLE001
        pw.stop()
        pytest.skip(
            f"Chromium 启动失败（先 ./venv/Scripts/playwright install chromium）: {exc}"
        )
    yield browser
    browser.close()
    pw.stop()


def _chromium_candidates() -> list[str]:
    """优先用完整 Chromium；Playwright 的 headless shell 可能未下载。"""
    out: list[str] = []
    explicit = os.environ.get("SOTABAND_E2E_CHROMIUM", "").strip()
    if explicit:
        out.append(explicit)
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
        patterns = ("chromium-*/chrome-win64/chrome.exe", "chromium-*/chrome-win/chrome.exe")
    else:
        base = Path(os.environ.get("HOME", "")) / ".cache" / "ms-playwright"
        patterns = ("chromium-*/chrome-linux/chrome",)
    for pattern in patterns:
        out.extend(str(path) for path in sorted(base.glob(pattern)))
    return [path for path in out if Path(path).exists()]


def _launch(pw):
    """启动 Chromium；优先完整可执行文件，失败再回退默认/最小环境。"""
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    for exe in _chromium_candidates():
        try:
            return pw.chromium.launch(executable_path=exe, args=args)
        except Exception:  # noqa: BLE001
            continue
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
        for exe in _chromium_candidates():
            try:
                return pw.chromium.launch(executable_path=exe, args=args, env=env)
            except Exception:  # noqa: BLE001
                continue
        return pw.chromium.launch(args=args, env=env)


def _track(request, page, guards: Guards) -> None:
    _TRACKED.setdefault(request.node.nodeid, []).append((page, guards))


def _open_page(browser, token_value: str, *, wait_selector: str = '[data-testid="main-layout"]'):
    context = browser.new_context(viewport={"width": 1600, "height": 1000})
    # 令牌必须在首次导航前就写入，否则首屏会先发一轮 401 再 reload。
    if token_value:
        context.add_init_script(
            f"localStorage.setItem({json.dumps(TOKEN_KEY)}, {json.dumps(token_value)});"
        )
    page = context.new_page()
    guards = Guards()
    # 未登录首屏并发拉资源，401 是预期状态；有令牌时 401 仍然算失败。
    guards.allow_unauthorized = not bool(token_value)
    guards.attach(page)
    record_page_requests(page)
    page.e2e_guards = guards
    page.e2e_frontend_url = FRONTEND_URL
    page.goto(FRONTEND_URL, wait_until="domcontentloaded")
    if wait_selector:
        page.locator(wait_selector).first.wait_for(state="visible", timeout=40_000)
    return context, page, guards


@pytest.fixture()
def page(request, browser, token):
    """已登录页面：令牌写进 localStorage 后重开页面。"""
    context, page, guards = _open_page(browser, token)
    _track(request, page, guards)
    yield page
    report = getattr(request.node, "rep_call", None)
    try:
        if report is not None and report.passed:
            guards.assert_clean()
    finally:
        context.close()


@pytest.fixture()
def anon_page(request, browser):
    """未登录页面：用于登录框/错误密码/退出登录。"""
    context, page, guards = _open_page(
        browser, "", wait_selector='[data-testid="login-dialog"], [data-testid="topbar-login"]'
    )
    # 多用户模式下未登录首屏会并发拉资源，401 是预期状态而不是静默失败。
    guards.allow_unauthorized = True
    _track(request, page, guards)
    yield page
    report = getattr(request.node, "rep_call", None)
    try:
        if report is not None and report.passed:
            guards.assert_clean()
    finally:
        context.close()


@pytest.fixture()
def page_factory(request, browser):
    """按需创建带/不带令牌的页面，测试结束统一清理。"""
    contexts = []

    def factory(
        token_value: str = "",
        wait_selector: str = '[data-testid="main-layout"]',
    ):
        context, page, guards = _open_page(
            browser, token_value, wait_selector=wait_selector
        )
        if not token_value:
            guards.allow_unauthorized = True
        contexts.append(context)
        _track(request, page, guards)
        return page

    yield factory
    report = getattr(request.node, "rep_call", None)
    try:
        if report is not None and report.passed:
            for _page, guards in _TRACKED.get(request.node.nodeid, []):
                guards.assert_clean()
    finally:
        for context in contexts:
            context.close()
