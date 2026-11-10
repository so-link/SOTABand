"""API 层公共设施：真实后端 HTTP（8201），未启动或缺凭据时整批 skip。

运行：

    ./venv/bin/python -m pytest tests/api -q          # 只跑接口层
    ./venv/bin/python -m pytest -q                    # unit + api（默认）
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

BACKEND_URL = os.environ.get("SOTABAND_API_BACKEND", "http://127.0.0.1:8201")


def _root_password() -> str:
    """超管密码：环境变量优先，其次直接读 .env。

    settings 模块导入时也会把 .env 灌进 os.environ，但纯 HTTP 用例可能先于
    任何应用导入运行，这里不依赖导入时机。
    """
    pw = os.environ.get("SOTABAND_ROOT_PASSWORD", "").strip()
    if pw:
        return pw
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, val = line.strip().partition("=")
            if key.strip() == "SOTABAND_ROOT_PASSWORD":
                return val.strip().strip('"').strip("'")
    return ""


@pytest.fixture(scope="session")
def backend_url() -> str:
    try:
        urllib.request.urlopen(f"{BACKEND_URL}/api/auth/me", timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"后端未就绪（{BACKEND_URL}）: {e}")
    return BACKEND_URL


@pytest.fixture
def backend_token(backend_url: str) -> str:
    """登录令牌：测试账号优先，其次超管（.env 的 SOTABAND_ROOT_PASSWORD）。

    为什么是 function 级而不是 session 级：改密码会让令牌里的 pwd 版本号
    失效，session 级令牌一旦被某个用例改坏，后续所有用例都会 401（本人踩过）。
    登录开销很小，每个用例重登一次更稳。
    """
    return _login_token(backend_url)


def _login_token(backend_url: str) -> str:
    candidates = [
        (os.environ.get("SOTABAND_E2E_USER", "test"),
         os.environ.get("SOTABAND_E2E_PASSWORD", "")),
        ("root", _root_password()),
    ]
    for user, password in candidates:
        if not password:
            continue
        req = urllib.request.Request(
            f"{backend_url}/api/auth/login", method="POST",
            data=json.dumps({"username": user, "password": password}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                token = json.loads(resp.read().decode()).get("token", "")
            if token:
                return token
        except urllib.error.HTTPError:
            continue
    pytest.skip("没有可用的登录凭据（SOTABAND_E2E_PASSWORD 或 SOTABAND_ROOT_PASSWORD）")


def _http(backend_url: str, token: str, path: str, method: str = "GET",
          body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{backend_url}{path}", data=data, method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


@pytest.fixture
def normal_user_token(backend_url: str):
    """普通用户令牌：现场建临时账号 → 登录 → 用例结束删号。

    越权类断言（期望 403）必须用**真正的普通身份**。不能用 backend_token
    代替：它在缺少 SOTABAND_E2E_PASSWORD 时会回落到 root 超管令牌，拿超管
    令牌断言 403 必然失败。这里不依赖环境变量，两种模式（单用户/多用户）
    都能拿到普通用户令牌。
    """
    root_pw = _root_password()
    admin_token = ""
    if root_pw:
        status, body = _http(backend_url, "", "/api/auth/login", "POST",
                             {"username": "root", "password": root_pw})
        assert status == 200 and body.get("token"), f"超管登录失败: {status} {body}"
        admin_token = body["token"]
    # 单用户模式下匿名即超管，无需令牌也能建号；多用户模式带超管令牌。
    username = f"api_norm_{int(time.time()) % 1000000}"
    status, body = _http(backend_url, admin_token, "/api/auth/users", "POST",
                         {"username": username, "password": "Norm123456"})
    assert status == 200, f"创建临时普通用户失败: {status} {body}"
    uid = body["user"]["id"]
    try:
        status, body = _http(backend_url, "", "/api/auth/login", "POST",
                             {"username": username, "password": "Norm123456"})
        assert status == 200 and body.get("token"), f"临时账号登录失败: {status} {body}"
        yield body["token"]
    finally:
        _http(backend_url, admin_token, f"/api/auth/users/{uid}", "DELETE")


# ────────────────────────── 进程内用例（SSE 生成链路） ──────────────────────────

@pytest.fixture
def client():
    """进程内应用实例（TestClient）。

    单用户模式下匿名请求按超管执行，不需要登录；多用户模式（.env 配置了
    SOTABAND_ROOT_PASSWORD）下匿名 /api 一律 401，因此先在本进程内走一次
    /api/auth/login 拿超管令牌，再挂到默认请求头上——两种模式都能用。
    用于必须替换 LLM 的 SSE 生成类用例（真实后端进程里没法注入假客户端）。

    ``base_url`` 必须是回环地址：应用有「仅接受本机 Host」的防护，默认的
    ``testserver`` 会被 403 挡下。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    root_pw = _root_password()
    with TestClient(app, base_url="http://127.0.0.1") as c:
        if root_pw:
            resp = c.post("/api/auth/login", json={
                "username": "root", "password": root_pw,
            })
            assert resp.status_code == 200, f"进程内超管登录失败: {resp.text}"
            c.headers["Authorization"] = f"Bearer {resp.json()['token']}"
        yield c


@pytest.fixture
def fake_llm():
    """固定输出的假 LLM 客户端（见 tests/api/_stream_support.py）。"""
    from tests.api._stream_support import FakeLLM

    return FakeLLM()
