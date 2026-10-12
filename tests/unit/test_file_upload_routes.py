import io
from pathlib import Path

import pytest
from fastapi import UploadFile
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import file_routes


@pytest.mark.asyncio
async def test_window_upload_batch_uses_one_isolated_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(file_routes, "UPLOAD_ROOT", tmp_path)
    first = await file_routes.upload_file(
        UploadFile(filename="window_features.pkl", file=io.BytesIO(b"features")),
        subdir="batch-1",
    )
    second = await file_routes.upload_file(
        UploadFile(filename="window_info.pkl", file=io.BytesIO(b"info")),
        subdir="batch-1",
    )

    assert first["id"] != second["id"]
    assert Path(first["filePath"]).parent == Path(second["filePath"]).parent == tmp_path / "batch-1"
    assert Path(first["filePath"]).read_bytes() == b"features"
    assert Path(second["filePath"]).read_bytes() == b"info"


def test_excel_download_returns_attachment(tmp_path):
    excel = tmp_path / "evaluation.xlsx"
    excel.write_bytes(b"xlsx response fixture")
    app = FastAPI()
    app.include_router(file_routes.router, prefix="/api/file")

    response = TestClient(app).get("/api/file/download", params={"path": str(excel)})

    assert response.status_code == 200
    assert response.content == excel.read_bytes()
    assert response.headers["content-disposition"].startswith("attachment;")
