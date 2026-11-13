"""运行期路由命中记录：浏览器请求 + API 客户端 + 准备脚本共用同一 JSONL。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def coverage_file() -> Path | None:
    raw = os.environ.get("E2E_COVERAGE_FILE", "").strip()
    return Path(raw) if raw else None


def record_hit(method: str, url: str, source: str = "api") -> None:
    target = coverage_file()
    if target is None:
        return
    path = urlsplit(str(url)).path or "/"
    if not (path.startswith("/api/") or path == "/"):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "method": str(method).upper(),
                    "url": str(url),
                    "source": source,
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def record_page_requests(page) -> None:
    """把页面发出的 /api 请求记入覆盖文件。"""
    page.on(
        "request",
        lambda request: record_hit(request.method, request.url, source="browser"),
    )
