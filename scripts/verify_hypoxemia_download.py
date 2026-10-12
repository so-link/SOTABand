"""Verify the platform Excel download route in the platform environment."""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.api.routes.file_routes import router


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", required=True)
    parser.add_argument("--model-id", required=True)
    args = parser.parse_args()

    excel = Path(args.excel).resolve()
    if not excel.is_file():
        raise AssertionError(f"Excel file does not exist: {excel}")

    api = FastAPI()
    api.include_router(router, prefix="/api/file")
    response = TestClient(api).get("/api/file/download", params={"path": str(excel)})
    assert response.status_code == 200
    assert response.content == excel.read_bytes()
    assert "attachment" in response.headers["content-disposition"]

    book = load_workbook(io.BytesIO(response.content), read_only=True)
    rows = list(book.active.values)
    book.close()
    assert ("provenance", "model_resource_id", args.model_id) in rows
    assert ("provenance", "dataset_id", "synthetic-evaluation") in rows
    assert any(row[1] == "f1" for row in rows)

    print(json.dumps({
        "status": "passed",
        "path": str(excel),
        "model_id": args.model_id,
        "download_status": response.status_code,
        "workbook_checked": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
