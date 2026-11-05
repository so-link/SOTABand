"""文件接口测试：上传/下载的鉴权与归属边界。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient

from app.api.routes import file_routes


@pytest.fixture()
def _auth_override(override_auth):
    return override_auth


@pytest.mark.asyncio
async def test_upload_uses_user_namespace(tmp_path, monkeypatch, override_auth):
    """上传落盘到上传根下的 **user_id 子目录**（跨用户不可见/不可删）。"""
    import core.file_guard as guard

    monkeypatch.setattr(guard, "UPLOAD_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(file_routes, "UPLOAD_ROOT", tmp_path / "uploads")

    first = await file_routes.upload_file(
        UploadFile(filename="window_features.pkl", file=io.BytesIO(b"features")),
        subdir="batch-1",
        ctx=override_auth,
    )
    second = await file_routes.upload_file(
        UploadFile(filename="window_features.pkl", file=io.BytesIO(b"info")),
        subdir="batch-1",
        ctx=override_auth,
    )

    assert first["id"] != second["id"]
    first_path = Path(first["filePath"]).resolve()
    assert override_auth.user_id in str(first_path)
    assert first_path.parent == (tmp_path / "uploads" / override_auth.user_id / "batch-1").resolve()
    assert first_path.read_bytes() == b"features"
    assert second["filePath"] != first["filePath"]


def test_excel_download_returns_attachment(tmp_path, monkeypatch, override_auth):
    import core.file_guard as guard

    excel = tmp_path / "evaluation.xlsx"
    excel.write_bytes(b"xlsx response fixture")

    # D3：下载路径必须落在允许根内；测试把 tmp_path 临时加入该用户的允许根
    monkeypatch.setattr(
        guard, "allowed_read_roots", lambda owner_id=None: [tmp_path.resolve()]
    )

    app = FastAPI()
    app.include_router(file_routes.router, prefix="/api/file")
    app.dependency_overrides[file_routes.get_auth_context] = lambda: override_auth

    response = TestClient(app).get("/api/file/download", params={"path": str(excel)})

    assert response.status_code == 200
    assert response.content == excel.read_bytes()
    assert response.headers["content-disposition"].startswith("attachment;")
