"""API 层：资源迁移（.sotatool.zip / .sotaagent.zip）与依赖安装的白名单。

导出体系刚统一过（tool_bundle / agent_bundle），导入侧此前完全没有 HTTP
用例，属于高风险空白：

1. `/migrate/info`：能力自述字段（前端据此显示「支持 tool/agent」与后缀）
2. `/migrate/tool/import-upload`：空文件 400、非法包 400、真实包导入成功后
   进入工作空间
3. `/migrate/agent/import-upload`：空文件/非法包 400
4. `/migrate/agent/{id}/install-deps`：非超管 403、空依赖 400、**依赖规格
   注入被白名单拒绝**（这条是共享 venv 的安全边界）
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
import uuid

import pytest

pytestmark = pytest.mark.api

TOOL_ID = "calc-square"
AGENT_ID = "city-info-query-agent"


def _call(base: str, token: str, path: str, method: str = "GET",
          body: dict | None = None, raw: bytes | None = None,
          content_type: str = "application/json",
          timeout: float = 120) -> tuple[int, dict]:
    data = raw if raw is not None else (
        json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Content-Type": content_type,
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                return resp.status, json.loads(payload.decode())
            return resp.status, {"_bytes": payload, "_type": ctype}
    except urllib.error.HTTPError as e:
        raw_body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw_body)
        except json.JSONDecodeError:
            return e.code, {"raw": raw_body}


def _upload(field: str, filename: str, payload: bytes,
            extra: dict[str, str] | None = None) -> tuple[bytes, str]:
    boundary = f"----sotaband{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for k, v in (extra or {}).items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
            .encode())
    chunks.append(
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
         f"filename=\"{filename}\"\r\nContent-Type: application/zip\r\n\r\n").encode())
    chunks.append(payload)
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _download(base: str, token: str, path: str) -> bytes:
    _, body = _call(base, token, path)
    data = body.get("_bytes")
    assert isinstance(data, bytes) and data[:2] == b"PK", f"{path} 没有返回 zip"
    return data


@pytest.fixture(scope="session")
def superuser_token(backend_url) -> str:
    import os

    password = os.environ.get("SOTABAND_ROOT_PASSWORD", "")
    if not password:
        pytest.skip("未配置超管密码（SOTABAND_ROOT_PASSWORD），跳过超管接口用例")
    status, body = _call(backend_url, "", "/api/auth/login", "POST",
                         {"username": "root", "password": password})
    assert status == 200 and body.get("token"), body
    return body["token"]


# ────────────────────────── info ──────────────────────────

def test_migrate_info_shape(backend_url, backend_token):
    status, body = _call(backend_url, backend_token, "/api/migrate/info")
    assert status == 200
    assert body.get("supports") == ["tool", "agent"]
    suffixes = body.get("suffixes") or {}
    assert suffixes.get("tool") == ".sotatool.zip", suffixes
    assert suffixes.get("agent") == ".sotaagent.zip", suffixes
    assert body.get("format_version")


# ────────────────────────── tool 导入 ──────────────────────────

def test_tool_import_upload_rejects_empty(backend_url, backend_token):
    payload, ctype = _upload("file", "empty.sotatool.zip", b"")
    status, body = _call(backend_url, backend_token, "/api/migrate/tool/import-upload",
                         "POST", raw=payload, content_type=ctype)
    assert status == 400, f"空上传未被拒绝: {status} {body}"


def test_tool_import_upload_rejects_garbage(backend_url, backend_token):
    payload, ctype = _upload("file", "bad.sotatool.zip", b"not a zip at all")
    status, body = _call(backend_url, backend_token, "/api/migrate/tool/import-upload",
                         "POST", raw=payload, content_type=ctype)
    assert status in (400, 500), f"非法包未被拒绝: {status} {body}"


def test_tool_import_upload_success_adds_to_workspace(backend_url, backend_token):
    """导出真实工具包 → 上传导入 → 自动加入工具空间。"""
    bundle = _download(backend_url, backend_token, f"/api/tool/{TOOL_ID}/export")
    # 归一：先清掉可能的私有遮蔽条目（导入/导出会产生私有副本）
    _call(backend_url, backend_token, f"/api/tool/{TOOL_ID}", "DELETE")
    payload, ctype = _upload("file", f"{TOOL_ID}.sotatool.zip", bundle,
                             {"overwrite": "true"})
    try:
        status, body = _call(backend_url, backend_token,
                             "/api/migrate/tool/import-upload", "POST",
                             raw=payload, content_type=ctype, timeout=180)
        assert status == 200, f"导入失败: {status} {body}"
        assert body.get("status") == "success"
        assert body.get("tool_id") == TOOL_ID or body.get("imported") == TOOL_ID, body
        assert body.get("added_to_workspace") is True, body
        assert "deps" in body, "导入响应没有依赖体检结果"
    finally:
        _call(backend_url, backend_token, f"/api/tool/{TOOL_ID}", "DELETE")


# ────────────────────────── agent 导入 ──────────────────────────

def test_agent_import_upload_rejects_empty(backend_url, backend_token):
    payload, ctype = _upload("file", "empty.sotaagent.zip", b"")
    status, body = _call(backend_url, backend_token, "/api/migrate/agent/import-upload",
                         "POST", raw=payload, content_type=ctype)
    assert status == 400, f"空上传未被拒绝: {status} {body}"


def test_agent_import_upload_rejects_garbage(backend_url, backend_token):
    payload, ctype = _upload("file", "bad.sotaagent.zip", b"still not a zip")
    status, body = _call(backend_url, backend_token, "/api/migrate/agent/import-upload",
                         "POST", raw=payload, content_type=ctype)
    assert status in (400, 500), f"非法包未被拒绝: {status} {body}"


# ────────────────────────── 依赖安装白名单 ──────────────────────────

def test_install_deps_requires_superuser(backend_url, normal_user_token):
    status, body = _call(backend_url, normal_user_token,
                         f"/api/migrate/agent/{AGENT_ID}/install-deps", "POST",
                         {"dependencies": ["requests"]})
    assert status == 403, f"普通用户可以装依赖到共享 venv: {status} {body}"


def test_install_deps_rejects_empty(backend_url, superuser_token):
    status, body = _call(backend_url, superuser_token,
                         f"/api/migrate/agent/{AGENT_ID}/install-deps", "POST",
                         {"dependencies": []})
    assert status == 400, f"空依赖未被拒绝: {status} {body}"


@pytest.mark.parametrize("spec", [
    "--index-url https://evil.example/simple",   # 换源
    "-e ../../evil",                              # 本地可执行
    "requests; python_version>'3'",               # 环境标记
    "file:///etc/passwd",                         # 本地文件
    "https://evil.example/pkg.tar.gz",            # URL
    "../../../etc",                               # 路径穿越
])
def test_install_deps_rejects_injection(backend_url, superuser_token, spec):
    """共享 venv 的安全边界：只允许「包名[extras]+版本约束」。"""
    status, body = _call(backend_url, superuser_token,
                         f"/api/migrate/agent/{AGENT_ID}/install-deps", "POST",
                         {"dependencies": [spec]})
    assert status == 400, f"危险依赖规格被接受: {spec!r} → {status} {body}"
    assert "依赖规格不被允许" in str(body.get("detail", "")), body


def test_install_deps_unknown_agent_404(backend_url, superuser_token):
    status, body = _call(backend_url, superuser_token,
                         "/api/migrate/agent/not-exist-agent-xyz/install-deps", "POST",
                         {"dependencies": ["requests"]})
    assert status in (404, 400), f"不存在的 Agent 应拒绝: {status} {body}"
