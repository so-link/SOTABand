"""带 Bearer Token 的 API 客户端，同时记录路由命中。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .coverage import record_hit


@dataclass
class ApiResponse:
    status: int
    payload: Any
    text: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        return self.payload


class ApiClient:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        coverage_file: Path | None = None,
        source: str = "api",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.coverage_file = coverage_file
        self.source = source
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _headers(self, extra: dict | None = None) -> dict:
        headers = dict(extra or {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict | None = None,
        data: Any = None,
        files: dict | None = None,
        timeout: float = 90,
    ) -> ApiResponse:
        url = f"{self.base_url}{path}"
        record_hit(method, url, source=self.source)
        response = self.session.request(
            method,
            url,
            json=json_body,
            params=params,
            data=data,
            files=files,
            headers=self._headers(),
            timeout=timeout,
        )
        text = response.text
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = text
        return ApiResponse(response.status_code, payload, text)

    def get(self, path: str, **kwargs) -> ApiResponse:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> ApiResponse:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> ApiResponse:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs) -> ApiResponse:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs) -> ApiResponse:
        return self.request("DELETE", path, **kwargs)

    def download(self, path: str, **kwargs) -> bytes:
        url = f"{self.base_url}{path}"
        record_hit("GET", url, source=self.source)
        response = self.session.get(
            url, headers=self._headers(), timeout=kwargs.pop("timeout", 90), **kwargs
        )
        response.raise_for_status()
        return response.content

    @classmethod
    def login(
        cls,
        base_url: str,
        username: str,
        password: str,
        coverage_file: Path | None = None,
    ) -> "ApiClient":
        client = cls(base_url, coverage_file=coverage_file, source="login")
        response = client.post(
            "/api/auth/login", json_body={"username": username, "password": password}
        )
        if response.status != 200 or not isinstance(response.payload, dict):
            raise RuntimeError(
                f"登录失败：HTTP {response.status} {str(response.payload)[:300]}"
            )
        token = str(response.payload.get("token") or "")
        if not token:
            raise RuntimeError("登录响应缺少 token")
        client.token = token
        return client
