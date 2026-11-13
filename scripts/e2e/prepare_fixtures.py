#!/usr/bin/env python3
"""为 wxbit E2E 准备隔离用户与资源夹具。

前置：后端已启动，`SOTABAND_HOME` 已播种系统资源。
步骤：
1. root 登录；
2. 创建一个普通 E2E 用户（写自己的 home，不污染 system/root）；
3. 普通用户登录，读取数据/工具/Agent/模型清单；
4. 写非敏感 fixtures JSON（不含 token/password）。

用户密码由编排器通过环境变量传入；脚本不写日志、不打印凭据。
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

if sys.platform == "win32":
    for _stream in ("stdout", "stderr"):
        try:
            getattr(sys, _stream).reconfigure(encoding="utf-8")
        except Exception:
            pass


class Api:
    def __init__(self, base_url: str, coverage_file: Path | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.coverage_file = coverage_file
        self.token = ""

    def _record(self, method: str, path: str) -> None:
        if self.coverage_file is None:
            return
        self.coverage_file.parent.mkdir(parents=True, exist_ok=True)
        with self.coverage_file.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"method": method.upper(), "url": f"{self.base_url}{path}", "source": "prepare"},
                    ensure_ascii=False,
                )
                + "\n"
            )

    def request(self, method: str, path: str, payload: dict | None = None, timeout: int = 30):
        self._record(method, path)
        data = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return resp.status, json.loads(body) if body.strip() else None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body) if body.strip() else None
            except json.JSONDecodeError:
                parsed = body
            return exc.code, parsed

    def login(self, username: str, password: str) -> str:
        status, body = self.request(
            "POST", "/api/auth/login", {"username": username, "password": password}
        )
        if status != 200 or not isinstance(body, dict) or not body.get("token"):
            raise SystemExit(f"登录失败：HTTP {status} {str(body)[:300]}")
        self.token = str(body["token"])
        return self.token


def _items(body, key: str) -> list[dict]:
    if isinstance(body, dict):
        value = body.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _make_png(path: Path, size: int = 16) -> None:
    raw = b"".join(b"\x00" + bytes([30, 120, 200]) * size for _ in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _create_dataset_fixture(api: Api, home: Path, run_id: str) -> dict:
    folder = home / "uploads" / "e2e-fixture"
    folder.mkdir(parents=True, exist_ok=True)
    png = folder / "sample.png"
    csv = folder / "sample.csv"
    md = folder / "README.md"
    _make_png(png)
    csv.write_text("name,value\nAlpha,1\nBeta,2\n", encoding="utf-8")
    md.write_text(
        f"# E2E Markdown\n\n唯一标记：E2E_MARKDOWN_{run_id}\n\n- 第一项\n- 第二项\n",
        encoding="utf-8",
    )
    spec = f"""# E2E 数据集规范

## 功能概述
端到端测试生成的确定性数据集，覆盖图片、CSV 表格与 Markdown。

## 数据格式
| 文件 | 格式 | 大小 | 说明 |
| --- | --- | --- | --- |
| sample.png | png | 1 KB | 图片预览 |
| sample.csv | csv | 1 KB | 表格预览 |
| README.md | md | 1 KB | Markdown 预览 |

## 使用说明
- 测试夹具，随临时运行目录删除。
"""
    status, body = api.request(
        "POST",
        "/api/data/register",
        {
            "specMd": spec,
            "datasetId": "e2e-dataset",
            "datasetName": "E2E 数据集",
            "sourceFiles": [str(png), str(csv), str(md)],
            "fileCount": 3,
            "totalSize": png.stat().st_size + csv.stat().st_size + md.stat().st_size,
            "formats": ["png", "csv", "md"],
            "tags": ["E2E"],
        },
    )
    if status >= 400:
        raise SystemExit(f"E2E 数据集注册失败：HTTP {status} {str(body)[:300]}")
    return {
        "id": "e2e-dataset",
        "name": "E2E 数据集",
        "file_count": 3,
        "formats": ["png", "csv", "md"],
        "spec_md": spec,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="准备 wxbit E2E 夹具")
    parser.add_argument("--base-url", default="http://127.0.0.1:8211")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--coverage-file", type=Path, default=None)
    parser.add_argument("--root-user", default="root")
    parser.add_argument("--root-password", default="")
    parser.add_argument("--e2e-user", default="")
    parser.add_argument("--e2e-password", default="")
    args = parser.parse_args()

    if not args.root_password:
        raise SystemExit("缺少 --root-password")
    e2e_user = args.e2e_user or f"e2e_{args.run_id.replace('-', '_')}"
    e2e_password = args.e2e_password or "E2ePassw0rd!2026"

    admin = Api(args.base_url, args.coverage_file)
    admin.login(args.root_user, args.root_password)

    # 创建普通用户；409 表示重跑，直接查列表复用
    status, body = admin.request(
        "POST",
        "/api/auth/users",
        {"username": e2e_user, "password": e2e_password, "displayName": "E2E 用户"},
    )
    user_id = ""
    if status == 200 and isinstance(body, dict):
        user_id = str((body.get("user") or {}).get("id") or "")
    elif status == 409:
        status, body = admin.request("GET", "/api/auth/users")
        for item in _items(body, "users"):
            if item.get("username") == e2e_user:
                user_id = str(item.get("id") or "")
                break
        if not user_id:
            raise SystemExit(f"用户已存在但列表中找不到：{e2e_user}")
    else:
        raise SystemExit(f"创建 E2E 用户失败：HTTP {status} {str(body)[:300]}")

    user = Api(args.base_url, args.coverage_file)
    user.login(e2e_user, e2e_password)
    status, me = user.request("GET", "/api/auth/me")
    if status != 200 or not isinstance(me, dict) or not me.get("authenticated"):
        raise SystemExit(f"E2E 普通用户登录态校验失败：HTTP {status} {me}")

    home = Path(os.environ.get("SOTABAND_HOME", str(args.fixtures.parent / "SB-USER"))) / user_id
    fixture_dataset = _create_dataset_fixture(user, home, args.run_id)

    datasets = _items(user.request("GET", "/api/data/list?available_only=true")[1], "datasets")
    datasets = [fixture_dataset] + [
        item for item in datasets if str(item.get("id")) != "e2e-dataset"
    ]
    tools = _items(user.request("GET", "/api/tool/list")[1], "tools")
    agents = _items(user.request("GET", "/api/agent/list")[1], "agents")
    models = _items(user.request("GET", "/api/model/list")[1], "models")

    # 选一个至少有一个文件的 seed 数据集；数据预览用例会按接口清单再挑 CSV/图片。
    dataset_ids = [str(d.get("id")) for d in datasets if d.get("id")]
    if not dataset_ids:
        raise SystemExit("系统资源播种后仍没有可用数据集；请检查 seed_system_resources.py")

    fixtures = {
        "run_id": args.run_id,
        # 不落盘密码：pytest/conftest 从 SOTABAND_E2E_PASSWORD 环境变量取
        "user": {"id": user_id, "username": e2e_user},
        "root": {"username": args.root_user},
        "datasets": [
            {
                "id": str(d.get("id")),
                "name": str(d.get("name") or d.get("id")),
                "file_count": d.get("file_count"),
                "formats": d.get("formats") or [],
            }
            for d in datasets
        ],
        "tools": [
            {"id": str(t.get("id")), "name": str(t.get("name") or t.get("id"))} for t in tools
        ],
        "agents": [
            {"id": str(a.get("id")), "name": str(a.get("name") or a.get("id"))} for a in agents
        ],
        "models": [
            {"id": str(m.get("id")), "name": str(m.get("name") or m.get("id"))} for m in models
        ],
    }
    args.fixtures.parent.mkdir(parents=True, exist_ok=True)
    # 密码是临时测试账号凭据，只写运行目录，不进入报告；编排器清理整个 run-dir。
    args.fixtures.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"[prepare] user={e2e_user} uid={user_id} "
        f"datasets={len(dataset_ids)} tools={len(fixtures['tools'])} "
        f"agents={len(fixtures['agents'])} models={len(fixtures['models'])}"
    )
    # 给调度器/Agent 启动留出稳定窗口；这不是固定 sleep，是显式的就绪后短等待。
    time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
