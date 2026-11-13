#!/usr/bin/env python3
"""从 FastAPI OpenAPI 生成路由清单，并维护覆盖台账骨架。

为什么要用 ``app.openapi()`` 而不是遍历 ``app.routes``：

当前 FastAPI 版本里 ``include_router`` 注册的是惰性 ``_IncludedRouter`` 节点
（没有 ``path`` 属性），直接遍历 ``app.routes`` 只能看到 ``/``、``/api/health``
等少量真实路由，会漏掉全部业务接口。OpenAPI 是框架自己汇总后的唯一事实源。

用法：
    python scripts/e2e/route_inventory.py
    python scripts/e2e/route_inventory.py --check   # 只报告新增/删除，不写文件
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "tests" / "e2e" / "route-inventory.json"
DEFAULT_MANIFEST = PROJECT_ROOT / "tests" / "e2e" / "coverage-manifest.json"

_METHODS = {"get", "post", "put", "patch", "delete"}
#: OpenAPI 自带文档路由，不属于业务接口
_SKIP_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _prepare_isolated_env() -> Path:
    """在临时 SOTABAND_HOME 下导入 app，避免碰真实 SB-USER / storage。"""
    tmp = Path(tempfile.mkdtemp(prefix="sotaband-route-inventory-")).resolve()
    os.environ["SOTABAND_HOME"] = str(tmp / "SB-USER")
    os.environ["SOTABAND_ROOT_PASSWORD"] = "inventory-only"
    os.environ["SOTABAND_AUTH_SECRET"] = "inventory-only-secret"
    # OpenAPI 导入不需要真实模型；把 LLM 指到回环占位，避免任何外部请求。
    os.environ["LLM_PROVIDER"] = "openai"
    os.environ["LLM_API_KEY"] = "inventory-only"
    os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/v1"
    os.environ["LLM_MODEL"] = "inventory-only"
    return tmp


def build_inventory() -> list[dict]:
    tmp = _prepare_isolated_env()
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from app.main import app  # noqa: PLC0415 - 必须在环境变量之后导入

        spec = app.openapi()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    routes: list[dict] = []
    for path, item in sorted(spec.get("paths", {}).items()):
        if path in _SKIP_PATHS:
            continue
        for method, operation in sorted(item.items()):
            if method not in _METHODS:
                continue
            routes.append(
                {
                    "key": f"{method.upper()} {path}",
                    "method": method.upper(),
                    "path": path,
                    "tags": operation.get("tags") or [],
                    "summary": operation.get("summary") or "",
                    "operation_id": operation.get("operationId") or "",
                }
            )
    return routes


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def update_manifest(manifest_path: Path, routes: list[dict]) -> tuple[dict, list[str], list[str]]:
    """保留已登记项；新路由以 ``uncovered`` 加入；已消失的路由保留并标记。"""
    manifest = _load_json(manifest_path, {})
    entries: dict = manifest.get("routes") or {}

    inventory_keys = {r["key"] for r in routes}
    added = sorted(inventory_keys - set(entries))
    removed = sorted(set(entries) - inventory_keys)

    for route in routes:
        key = route["key"]
        if key not in entries:
            entries[key] = {
                "mode": "uncovered",
                "spec": "",
                "reason": "",
            }
        entries[key].setdefault("method", route["method"])
        entries[key].setdefault("path", route["path"])
        entries[key].setdefault("tags", route["tags"])

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "FastAPI app.openapi()",
        "routes": dict(sorted(entries.items())),
    }
    return manifest, added, removed


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 SOTABand 路由清单与覆盖台账")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--check", action="store_true", help="只检查差异，不写文件")
    args = parser.parse_args()

    routes = build_inventory()
    manifest, added, removed = update_manifest(args.manifest, routes)

    print(f"[route-inventory] 共 {len(routes)} 个操作")
    if added:
        print(f"[route-inventory] 新增未登记路由 {len(added)} 个：")
        for key in added:
            print(f"  + {key}")
    if removed:
        print(f"[route-inventory] 清单中有 {len(removed)} 个路由已不存在：")
        for key in removed:
            print(f"  - {key}")

    if args.check:
        return 1 if added else 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "generated_at": manifest["generated_at"],
                "source": manifest["source"],
                "count": len(routes),
                "routes": routes,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[route-inventory] 已写入 {args.output}")
    print(f"[route-inventory] 已更新 {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
