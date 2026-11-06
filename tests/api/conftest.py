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


@pytest.fixture(scope="session")
def backend_token(backend_url: str) -> str:
    """登录令牌：测试账号优先，其次超管（.env 的 SOTABAND_ROOT_PASSWORD）。"""
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
