#!/usr/bin/env python3
"""wxbit 工具回归：静态冒烟（默认、阻断）+ 按需执行（不阻断）。

静态冒烟走 API，不直接依赖本地目录布局：
- `/api/tool/list` 条目字段；
- `/api/tool/{id}` 的 code/spec_md 非空。

执行回归默认关闭；`--tools a,b` / `--tool-profile fast` 才运行；
失败只写工具报告，不改变主套件退出码。
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "tests" / "e2e" / "fixtures" / "tool-regression.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "dist" / "e2e-report"


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


class Api:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.session.headers["Accept"] = "application/json"

    def request(self, method: str, path: str, payload=None, timeout: int = 60):
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            return 0, str(exc)
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return response.status_code, body


def static_smoke(api: Api) -> dict:
    status, body = api.request("GET", "/api/tool/list", timeout=30)
    entries = body.get("tools") if isinstance(body, dict) else []
    if status != 200 or not isinstance(entries, list):
        return {
            "total": 0,
            "passed": 0,
            "failed": 1,
            "failures": [
                {
                    "tool_id": "-",
                    "errors": [f"工具列表失败：HTTP {status} {str(body)[:300]}"],
                }
            ],
            "results": [],
        }

    results = []
    for entry in sorted(entries, key=lambda item: str(item.get("id"))):
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        tool_id = str(entry["id"])
        errors = []
        for field in ("id", "name", "version"):
            if not entry.get(field):
                errors.append(f"缺少字段 {field}")
        detail_status, detail = api.request("GET", f"/api/tool/{tool_id}", timeout=30)
        if detail_status != 200 or not isinstance(detail, dict):
            errors.append(f"详情接口失败：HTTP {detail_status}")
        else:
            if not str(detail.get("code") or "").strip():
                errors.append("详情接口 code 为空")
            if not str(detail.get("spec_md") or "").strip():
                errors.append("详情接口 spec_md 为空")
        results.append(
            {
                "tool_id": tool_id,
                "name": entry.get("name") or tool_id,
                "status": "passed" if not errors else "failed",
                "errors": errors,
            }
        )

    failed = [item for item in results if item["status"] == "failed"]
    return {
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "failures": failed[:30],
        "results": results,
    }


def _get_path(payload, path: str):
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


_MISSING = object()


def _substitute(value, variables: dict[str, str]):
    if isinstance(value, str):
        for key, replacement in variables.items():
            value = value.replace(f"{{{{{key}}}}}", replacement)
        return value
    if isinstance(value, list):
        return [_substitute(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, variables) for key, item in value.items()}
    return value


def _select_entries(manifest: list[dict], tools: list[str], profile: str | None, tag: str | None):
    by_id = {str(item.get("id")): item for item in manifest if item.get("id")}
    if tools:
        return [by_id.get(tool_id) or {"id": tool_id, "profile": "fast", "cases": []} for tool_id in tools]
    if tag:
        return [item for item in manifest if tag in (item.get("tags") or [])]
    if profile:
        return [item for item in manifest if item.get("profile") == profile]
    return []


def execute_cases(api: Api, entries: list[dict], variables: dict[str, str]) -> dict:
    results = []
    for entry in entries:
        tool_id = str(entry.get("id") or "")
        profile = entry.get("profile") or "fast"
        reason = entry.get("reason") or ""
        if profile == "excluded":
            results.append(
                {"tool_id": tool_id, "case": "-", "status": "skipped", "reason": reason or "excluded"}
            )
            continue
        cases = entry.get("cases") or []
        if not cases:
            results.append(
                {
                    "tool_id": tool_id,
                    "case": "-",
                    "status": "skipped",
                    "reason": "no-case（在 tool-regression.json 补用例后才会执行）",
                }
            )
            continue
        for case in cases:
            name = str(case.get("name") or "case")
            params = _substitute(case.get("params") or {}, variables)
            timeout = int(case.get("timeout_s") or 60)
            started = time.perf_counter()
            status, payload = api.request(
                "POST", f"/api/tool/{tool_id}/execute", {"params": params}, timeout=timeout
            )
            duration_ms = int((time.perf_counter() - started) * 1000)
            errors = []
            output_tail = ""
            if status != 200 or not isinstance(payload, dict):
                errors.append(f"执行接口失败：HTTP {status} {str(payload)[:300]}")
            else:
                result = payload.get("result") or {}
                output_tail = json.dumps(result, ensure_ascii=False, default=str)[:800]
                expected = case.get("expect") or {}
                actual_success = result.get("status") == "success"
                if bool(expected.get("success", True)) != actual_success:
                    errors.append(
                        f"期望 success={bool(expected.get('success', True))}，实际 {result.get('status')}：{result.get('message')}"
                    )
                for path, expected_value in (expected.get("json_paths") or {}).items():
                    actual = _get_path(result, path)
                    if actual is _MISSING or actual != expected_value:
                        errors.append(f"字段 {path} 期望 {expected_value!r}，实际 {actual!r}")
                for needle in expected.get("contains") or []:
                    if str(needle) not in output_tail:
                        errors.append(f"输出中缺少文本 {needle!r}")
            results.append(
                {
                    "tool_id": tool_id,
                    "case": name,
                    "status": "passed" if not errors else "failed",
                    "duration_ms": duration_ms,
                    "errors": errors,
                    "output_tail": output_tail,
                }
            )
    passed = len([r for r in results if r["status"] == "passed"])
    failed = len([r for r in results if r["status"] == "failed"])
    skipped = len([r for r in results if r["status"] == "skipped"])
    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }


def render_markdown(report: dict) -> str:
    lines = ["# 工具回归报告", "", f"- 生成时间：{report['generated_at']}", ""]
    static = report.get("static") or {}
    if static:
        lines += [
            "## 静态冒烟（阻断）",
            "",
            f"- 工具数：{static['total']}",
            f"- 通过：{static['passed']}",
            f"- 失败：{static['failed']}",
            "",
        ]
        if static.get("failures"):
            lines += ["| 工具 | 问题 |", "|---|---|"]
            for item in static["failures"]:
                lines.append(f"| `{item['tool_id']}` | {'；'.join(item['errors'])} |")
            lines.append("")
    execution = report.get("execution")
    if execution:
        lines += [
            "## 执行回归（不阻断）",
            "",
            f"- 结果：{execution['passed']} 通过 / {execution['failed']} 失败 / {execution['skipped']} 跳过",
            "",
            "| 工具 | 用例 | 状态 | 耗时(ms) | 说明 |",
            "|---|---|---|---|---|",
        ]
        for item in execution["results"]:
            detail = "；".join(item.get("errors") or []) or item.get("reason") or ""
            lines.append(
                f"| `{item['tool_id']}` | {item['case']} | {item['status']} | {item.get('duration_ms', '-')} | {detail} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="wxbit 工具回归")
    parser.add_argument("--base-url", default="http://127.0.0.1:8211")
    parser.add_argument("--token", default="")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--static-smoke", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--tools", action="append", default=[])
    parser.add_argument("--tool-profile", default=None)
    parser.add_argument("--tool-tag", default=None)
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--dataset-dir", default="")
    parser.add_argument("--dataset-id", default="")
    args = parser.parse_args()

    api = Api(args.base_url, args.token)
    existing = _load_json(args.output_dir / "tools.json", {})
    report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for key in ("static", "execution"):
        if isinstance(existing.get(key), dict):
            report[key] = existing[key]
    # 只跑静态冒烟时不能沿用上一次的执行结果，否则摘要会把旧结果当成本次结果。
    if args.static_smoke and not args.execute:
        report.pop("execution", None)
    exit_code = 0

    if args.static_smoke:
        static = static_smoke(api)
        report["static"] = static
        if static["failed"]:
            exit_code = 1
        print(
            f"[tools] 静态冒烟：{static['passed']}/{static['total']} 通过，"
            f"{static['failed']} 失败"
        )
        for item in static["failures"][:10]:
            print(f"  - {item['tool_id']}：{'；'.join(item['errors'])}")

    if args.execute:
        manifest = _load_json(args.manifest, [])
        if isinstance(manifest, dict):
            manifest = manifest.get("tools") or []
        tools = [item.strip() for chunk in args.tools for item in chunk.split(",") if item.strip()]
        entries = _select_entries([item for item in manifest if isinstance(item, dict)], tools, args.tool_profile, args.tool_tag)
        if not entries:
            print("[tools] 没有选中任何执行用例（检查 --tools / --tool-profile）")
        else:
            variables = {
                "run_dir": args.run_dir,
                "dataset_dir": args.dataset_dir,
                "dataset_id": args.dataset_id,
            }
            execution = execute_cases(api, entries, variables)
            report["execution"] = execution
            print(
                f"[tools] 执行回归：{execution['passed']} 通过 / {execution['failed']} 失败 / {execution['skipped']} 跳过（不阻断）"
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "tools.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "tools.md").write_text(render_markdown(report), encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
