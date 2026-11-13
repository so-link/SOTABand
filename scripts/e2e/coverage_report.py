#!/usr/bin/env python3
"""路由覆盖台账计算与门禁。

输入：
- ``route-inventory.json``：OpenAPI 生成的全量路由（唯一事实源）
- ``coverage-manifest.json``：每条路由的覆盖方式（ui-consume/ui-action/api/excluded）
- ``coverage-hits.jsonl``：浏览器与 API 包装器在运行期记录的真实请求

输出：
- ``coverage.json`` / ``coverage.md`` 到 ``dist/e2e-report/``
- 退出码：存在未覆盖路由、未分类路由，或出现「调用了不存在的后端接口」时为 1

匹配规则：按 OpenAPI 路径模板把 ``{param}`` 编译成 ``[^/]+``，按静态段数量
从多到少匹配，避免 ``/api/tool/{tool_id}`` 抢先吞掉 ``/api/tool/workspace``。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = PROJECT_ROOT / "tests" / "e2e" / "route-inventory.json"
DEFAULT_MANIFEST = PROJECT_ROOT / "tests" / "e2e" / "coverage-manifest.json"
DEFAULT_HITS = PROJECT_ROOT / "dist" / "e2e-report" / "coverage-hits.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "dist" / "e2e-report"

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_IGNORED_METHODS = {"OPTIONS", "HEAD"}


def _compile_route_path(path: str) -> re.Pattern[str]:
    """把 OpenAPI 路径模板编译为完整匹配正则。"""
    parts: list[str] = []
    for chunk in re.split(r"(\{[^}]+\})", path):
        if chunk.startswith("{") and chunk.endswith("}"):
            parts.append(r"[^/]+")
        else:
            parts.append(re.escape(chunk))
    return re.compile("^" + "".join(parts) + "$")


def _route_sort_key(path: str) -> tuple[int, int, int]:
    """匹配优先级：参数越少越优先，其次静态段越多、路径越长越优先。

    否则 ``/api/data/{dataset_id}`` 会抢先吞掉 ``/api/data/list``，
    导致台账把「列表接口」误判为未覆盖。
    """
    static_segments = len([p for p in path.split("/") if p and "{" not in p])
    param_count = path.count("{")
    return -param_count, static_segments, len(path)


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _normalize_hit(record: dict) -> tuple[str, str] | None:
    method = str(record.get("method") or "").upper()
    if method not in _METHODS:
        return None
    raw_url = str(record.get("url") or "")
    parts = urlsplit(raw_url)
    path = parts.path or "/"
    if not (path.startswith("/api/") or path == "/"):
        return None
    return method, path


def build_report(
    inventory: dict,
    manifest: dict,
    hits: list[dict],
) -> dict:
    routes = inventory.get("routes") or []
    manifest_routes = manifest.get("routes") or {}

    compiled = [
        {**route, "_regex": _compile_route_path(route["path"])}
        for route in routes
    ]
    compiled.sort(key=lambda r: _route_sort_key(r["path"]), reverse=True)

    hit_counter: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    for record in hits:
        normalized = _normalize_hit(record)
        if normalized is None:
            continue
        method, path = normalized
        matched = None
        for route in compiled:
            if route["method"] != method:
                continue
            if route["_regex"].match(path):
                matched = route["key"]
                break
        if matched:
            hit_counter[matched] += 1
        else:
            unknown[f"{method} {path}"] += 1

    uncovered: list[dict] = []
    unclassified: list[dict] = []
    excluded: list[dict] = []
    covered: list[dict] = []

    for route in routes:
        key = route["key"]
        entry = manifest_routes.get(key) or {}
        mode = entry.get("mode") or "uncovered"
        item = {
            "key": key,
            "mode": mode,
            "spec": entry.get("spec") or "",
            "reason": entry.get("reason") or "",
            "hits": hit_counter.get(key, 0),
        }
        if mode == "excluded":
            excluded.append(item)
            continue
        if mode == "uncovered":
            unclassified.append(item)
            uncovered.append(item)
            continue
        if item["hits"] == 0:
            uncovered.append(item)
        else:
            covered.append(item)

    total_required = len([r for r in routes if (manifest_routes.get(r["key"]) or {}).get("mode") != "excluded"])
    coverage = (len(covered) / total_required * 100) if total_required else 100.0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_operations": len(routes),
        "required_operations": total_required,
        "covered_operations": len(covered),
        "coverage_percent": round(coverage, 2),
        "uncovered": uncovered,
        "unclassified": unclassified,
        "excluded": excluded,
        "unknown_requests": [{"request": k, "count": v} for k, v in sorted(unknown.items())],
        "hit_total": sum(hit_counter.values()),
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# 路由覆盖报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 总操作数：{report['total_operations']}",
        f"- 应覆盖：{report['required_operations']}",
        f"- 已命中：{report['covered_operations']}（{report['coverage_percent']}%）",
        f"- 未覆盖：{len(report['uncovered'])}",
        f"- 未分类：{len(report['unclassified'])}",
        f"- 未匹配请求：{len(report['unknown_requests'])}",
        f"- 排除项：{len(report['excluded'])}",
        "",
    ]

    if report["uncovered"]:
        lines += ["## 未覆盖路由", "", "| 路由 | 台账状态 | 用例 | 原因 |", "|---|---|---|---|"]
        for item in report["uncovered"]:
            lines.append(
                f"| `{item['key']}` | {item['mode']} | {item['spec'] or '-'} | {item['reason'] or '待补用例'} |"
            )
        lines.append("")

    if report["unknown_requests"]:
        lines += ["## 未匹配到后端路由的请求", "", "| 请求 | 次数 |", "|---|---|"]
        for item in report["unknown_requests"]:
            lines.append(f"| `{item['request']}` | {item['count']} |")
        lines.append("")

    if report["excluded"]:
        lines += ["## 明确排除", "", "| 路由 | 原因 |", "|---|---|"]
        for item in report["excluded"]:
            lines.append(f"| `{item['key']}` | {item['reason'] or '-'} |")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="计算 SOTABand 路由覆盖率")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--hits", type=Path, default=DEFAULT_HITS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    inventory = _load_json(args.inventory, {})
    manifest = _load_json(args.manifest, {})
    if not inventory.get("routes"):
        print(f"[coverage] 路由清单为空或不存在：{args.inventory}")
        return 1

    hits: list[dict] = []
    if args.hits.exists():
        for line in args.hits.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                hits.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    report = build_report(inventory, manifest, hits)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "coverage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "coverage.md").write_text(
        render_markdown(report), encoding="utf-8"
    )

    print(
        f"[coverage] {report['covered_operations']}/{report['required_operations']} "
        f"已命中（{report['coverage_percent']}%），未覆盖 {len(report['uncovered'])}，"
        f"未分类 {len(report['unclassified'])}，未匹配请求 {len(report['unknown_requests'])}"
    )
    for item in report["uncovered"][:20]:
        print(f"  - 未覆盖：{item['key']}（{item['mode']}）")
    for item in report["unknown_requests"][:20]:
        print(f"  - 未匹配请求：{item['request']} × {item['count']}")

    return 1 if (report["uncovered"] or report["unknown_requests"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
