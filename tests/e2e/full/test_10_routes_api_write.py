"""API 写接口覆盖：使用临时用户/私有工具/临时数据集/临时 cron，并逐步清理。"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.e2e


def _suffix() -> str:
    raw = os.environ.get("E2E_RUN_ID", "local")
    return "".join(ch for ch in raw if ch.isalnum())[-10:].lower()


def _allowed(response, allowed: set[int], label: str):
    assert response.status in allowed, f"{label} → HTTP {response.status}: {response.text[:500]}"
    return response


def _private_tool_spec() -> tuple[str, str]:
    spec = """# E2E API 私有工具

## 功能概述
端到端测试临时工具，回显输入。

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| x | float | 是 | 1 | 输入值 |

## 输出规范
返回 {"x": x}。

## 依赖环境
无第三方依赖。

## 运行机制
调用 execute(x=...) 返回。
"""
    code = (
        "def execute(**kwargs):\n"
        "    return {'status': 'success', 'data': {'x': kwargs.get('x', 0)}}\n"
    )
    return spec, code


def test_private_tool_write_routes(api, fixtures):
    suffix = _suffix()
    tool_id = f"e2e_api_{suffix}"
    spec, code = _private_tool_spec()
    created = _allowed(
        api.post(
            "/api/tool/register",
            json_body={
                "specMd": spec,
                "code": code,
                "toolId": tool_id,
                "toolName": "E2E API 私有工具",
                "version": "0.1.0",
                "tags": ["E2E"],
            },
        ),
        {200},
        "tool register",
    )
    assert (created.json() or {}).get("tool_id") == tool_id

    _allowed(api.post(f"/api/tool/{tool_id}/save-code", json_body={"code": code}), {200}, "save-code")
    _allowed(
        api.post(f"/api/tool/{tool_id}/save-spec", json_body={"spec_md": spec, "resync_params": False}),
        {200},
        "save-spec",
    )
    _allowed(
        api.post(f"/api/tool/{tool_id}/save-step1", json_body={"demand_md": "E2E 需求", "reference_code": code}),
        {200},
        "save-step1",
    )
    _allowed(api.post(f"/api/tool/{tool_id}/tags", json_body={"tags": ["E2E", "API"]}), {200}, "tags")

    outline = _allowed(
        api.post(f"/api/tool/{tool_id}/spec-outline", json_body={"spec_md": spec}),
        {200},
        "spec-outline",
    )
    nodes = (outline.json() or {}).get("nodes") or []
    node_id = nodes[0].get("id") if nodes else ""
    _allowed(
        api.post(f"/api/tool/{tool_id}/spec-table", json_body={"spec_md": spec}),
        {200},
        "spec-table",
    )
    if node_id:
        _allowed(
            api.post(
                f"/api/tool/{tool_id}/update-table-cell",
                json_body={
                    "node_id": node_id,
                    "row_index": 0,
                    "column": "默认值",
                    "value": "2",
                    "spec_md": spec,
                    "save": False,
                },
            ),
            {200, 400, 404},
            "update-table-cell",
        )

    _allowed(
        api.post(
            "/api/tool/workspace",
            json_body={"id": tool_id, "name": "E2E API 私有工具", "tags": ["E2E"]},
        ),
        {200},
        "workspace add",
    )
    _allowed(api.delete(f"/api/tool/workspace/{tool_id}"), {200, 404}, "workspace remove")

    builtin_id = (fixtures.get("tools") or [])[0]["id"]
    fork = api.post(f"/api/tool/{builtin_id}/fork")
    _allowed(fork, {200, 400, 404}, "tool fork")
    _allowed(api.delete(f"/api/tool/{builtin_id}/fork"), {200, 400, 404}, "tool fork discard")

    _allowed(api.delete(f"/api/tool/{tool_id}"), {200}, "tool delete")


def test_dataset_register_and_delete(api, fixtures):
    suffix = _suffix()
    uploaded = _allowed(
        api.post(
            "/api/file/upload",
            files={"file": ("e2e-api-dataset.csv", b"name,value\nA,1\n", "text/csv")},
        ),
        {200},
        "dataset source upload",
    )
    uploaded_payload = uploaded.json() or {}
    file_id = str(
        uploaded_payload.get("file_id")
        or uploaded_payload.get("id")
        or uploaded_payload.get("fileId")
        or ""
    )
    assert file_id, uploaded_payload

    dataset_id = f"e2e_api_ds_{suffix}"
    spec = """# E2E API 数据集

## 功能概述
临时注册数据集。

## 数据格式
| 文件 | 格式 | 大小 | 说明 |
| --- | --- | --- | --- |
| sample.csv | csv | 1 KB | 测试 |
"""
    registered = api.post(
        "/api/data/register",
        json_body={
            "specMd": spec,
            "datasetId": dataset_id,
            "datasetName": "E2E API 数据集",
            "sourceFiles": [],
            "sourceFileIds": [file_id],
            "fileCount": 1,
            "totalSize": 1,
            "formats": ["csv"],
            "tags": ["E2E"],
        },
    )
    try:
        _allowed(registered, {200}, "data register")
        _allowed(api.delete(f"/api/data/{dataset_id}"), {200}, "data delete")
    finally:
        api.delete(f"/api/file/{file_id}")


def test_file_upload_get_delete(api):
    uploaded = api.post(
        "/api/file/upload",
        files={"file": ("e2e-api.txt", b"E2E API FILE", "text/plain")},
    )
    _allowed(uploaded, {200}, "file upload")
    payload = uploaded.json() or {}
    file_id = str(payload.get("file_id") or payload.get("id") or payload.get("fileId") or "")
    assert file_id, payload
    _allowed(api.get(f"/api/file/{file_id}"), {200, 404}, "file get")
    _allowed(api.delete(f"/api/file/{file_id}"), {200}, "file delete")


def test_user_management_write_routes(admin_api):
    suffix = _suffix()
    username = f"e2e_api_user_{suffix}"
    created = _allowed(
        admin_api.post(
            "/api/auth/users",
            json_body={"username": username, "password": "E2eApi!2026", "displayName": "E2E API"},
        ),
        {200},
        "user create",
    )
    uid = str(((created.json() or {}).get("user") or {}).get("id") or "")
    assert uid
    try:
        _allowed(
            admin_api.put(f"/api/auth/users/{uid}", json_body={"username": f"{username}_x"}),
            {200},
            "user rename",
        )
        _allowed(
            admin_api.put(
                f"/api/auth/users/{uid}/display-name",
                json_body={"displayName": "E2E API 改名"},
            ),
            {200},
            "user display-name",
        )
        _allowed(
            admin_api.put(
                f"/api/auth/users/{uid}/password",
                json_body={"password": "E2eApi!2026x"},
            ),
            {200},
            "user password reset",
        )
    finally:
        _allowed(admin_api.delete(f"/api/auth/users/{uid}"), {200}, "user delete")


def test_cron_write_routes(api, fixtures):
    tool_id = (fixtures.get("tools") or [])[0]["id"]
    payload = {
        "name": "E2E API 定时任务",
        "kind": "tool",
        "target_id": tool_id,
        "params": {},
        "cron": "0 0 1 1 *",
        "timezone": "UTC",
        "enabled": False,
        "timeout": 60,
        "misfire": "skip",
    }
    created = _allowed(api.post("/api/cron/jobs", json_body=payload), {200}, "cron create")
    job_id = str(((created.json() or {}).get("job") or {}).get("id") or "")
    assert job_id
    try:
        _allowed(api.get(f"/api/cron/jobs/{job_id}"), {200}, "cron get")
        _allowed(
            api.put(f"/api/cron/jobs/{job_id}", json_body={**payload, "name": "E2E API 定时任务改"}),
            {200},
            "cron update",
        )
        _allowed(api.post(f"/api/cron/jobs/{job_id}/toggle"), {200}, "cron toggle")
        _allowed(api.get(f"/api/cron/jobs/{job_id}/runs"), {200}, "cron runs")
        _allowed(api.get("/api/cron/runs/recent"), {200}, "cron recent runs")
    finally:
        _allowed(api.delete(f"/api/cron/jobs/{job_id}"), {200}, "cron delete")
