"""API 层公共设施：真实后端 HTTP（8201），未启动或缺凭据时整批 skip。

运行：

    ./venv/bin/python -m pytest tests/api -q          # 只跑接口层
    ./venv/bin/python -m pytest -q                    # unit + api（默认）
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

BACKEND_URL = os.environ.get("SOTABAND_API_BACKEND", "http://127.0.0.1:8201")


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
        ("root", os.environ.get("SOTABAND_ROOT_PASSWORD", "")),
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


# ────────────────────────── 进程内用例（SSE 生成链路） ──────────────────────────

@pytest.fixture
def client():
    """进程内应用实例（TestClient）。

    单用户模式下匿名请求按超管执行，因此不需要登录；用于必须替换 LLM 的
    SSE 生成类用例（真实后端进程里没法注入假客户端）。

    ``base_url`` 必须是回环地址：应用有「仅接受本机 Host」的防护，默认的
    ``testserver`` 会被 403 挡下。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture
def fake_llm():
    """固定输出的假 LLM 客户端（见 tests/api/_stream_support.py）。"""
    from tests.api._stream_support import FakeLLM

    return FakeLLM()
