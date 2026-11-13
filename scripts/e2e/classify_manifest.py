#!/usr/bin/env python3
"""根据真实命中更新覆盖台账，并固化「明确排除」清单（wxbit 分支）。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from coverage_report import (
    DEFAULT_HITS,
    DEFAULT_INVENTORY,
    DEFAULT_MANIFEST,
    _compile_route_path,
    _normalize_hit,
    _route_sort_key,
)

UI_ROUTES: dict[str, tuple[str, str]] = {
    # 认证 / 布局
    "GET /api/auth/me": ("ui-action", "test_01_auth.py"),
    "GET /api/auth/prefs": ("ui-consume", "test_01_auth.py"),
    "PUT /api/auth/prefs": ("ui-action", "test_01_auth.py"),
    "POST /api/auth/login": ("ui-action", "test_01_auth.py"),
    "POST /api/auth/logout": ("ui-action", "test_01_auth.py"),
    "GET /api/auth/users": ("ui-consume", "test_07_settings_users_cron.py"),
    "GET /api/auth/superuser": ("ui-consume", "test_07_settings_users_cron.py"),
    # 资源浏览器
    "GET /api/data/list": ("ui-consume", "test_02_resource_browser.py"),
    "GET /api/data/repository": ("ui-consume", "test_06_repository.py"),
    "GET /api/tool/list": ("ui-consume", "test_02_resource_browser.py"),
    "GET /api/tool/workspace": ("ui-consume", "test_02_resource_browser.py"),
    "GET /api/tool/repository": ("ui-consume", "test_06_repository.py"),
    "GET /api/agent/list": ("ui-consume", "test_02_resource_browser.py"),
    "GET /api/model/list": ("ui-consume", "test_02_resource_browser.py"),
    # 数据预览（核心回归）
    "GET /api/data/{dataset_id}/preview": ("ui-consume", "test_03_data_preview.py"),
    "GET /api/data/{dataset_id}/files": ("ui-consume", "test_03_data_preview.py"),
    "GET /api/data/{dataset_id}/table": ("ui-consume", "test_03_data_preview.py"),
    "GET /api/data/{dataset_id}/raw": ("ui-consume", "test_03_data_preview.py"),
    "GET /api/data/{dataset_id}/archive": ("ui-action", "test_03_data_preview.py"),
    # 对话
    "POST /api/chat/send": ("ui-action", "test_04_chat.py"),
    "POST /api/chat/stop": ("ui-action", "test_04_chat.py"),
    "GET /api/chat/sessions": ("ui-action", "test_04_chat.py"),
    "GET /api/chat/sessions/{session_id}": ("ui-action", "test_04_chat.py"),
    "DELETE /api/chat/sessions/{session_id}": ("ui-action", "test_04_chat.py"),
    # 详情
    "GET /api/tool/{tool_id}": ("ui-consume", "test_05_tool_agent_detail.py"),
    "GET /api/tool/{tool_id}/check-deps": ("ui-consume", "test_05_tool_agent_detail.py"),
    "GET /api/agent/{agent_id}": ("ui-consume", "test_05_tool_agent_detail.py"),
    "GET /api/agent/{agent_id}/status": ("ui-consume", "test_05_tool_agent_detail.py"),
    "GET /api/agent/{agent_id}/chat-history": ("ui-consume", "test_05_tool_agent_detail.py"),
    # 设置 / 用户 / cron
    "GET /api/config/llm": ("ui-consume", "test_07_settings_users_cron.py"),
    "GET /api/llm/config": ("ui-consume", "test_07_settings_users_cron.py"),
    "GET /api/cron/options": ("ui-consume", "test_07_settings_users_cron.py"),
    "GET /api/cron/jobs": ("ui-consume", "test_07_settings_users_cron.py"),
    "GET /api/cron/jobs/{job_id}": ("ui-consume", "test_07_settings_users_cron.py"),
    "GET /api/cron/jobs/{job_id}/runs": ("ui-consume", "test_07_settings_users_cron.py"),
    "GET /api/cron/runs/recent": ("ui-consume", "test_07_settings_users_cron.py"),
    # 文件 / 任务
    "POST /api/file/upload": ("ui-action", "test_08_files_tasks.py"),
    "GET /api/file": ("ui-action", "test_08_files_tasks.py"),
    "GET /api/file/{file_id}": ("ui-action", "test_08_files_tasks.py"),
    "DELETE /api/file/{file_id}": ("ui-action", "test_08_files_tasks.py"),
    "GET /api/file/workspace/tree": ("ui-action", "test_08_files_tasks.py"),
    "PUT /api/file/workspace/tree": ("ui-action", "test_08_files_tasks.py"),
    "GET /api/task/list": ("ui-action", "test_08_files_tasks.py"),
    "GET /api/task/recent-agents": ("ui-action", "test_08_files_tasks.py"),
    "DELETE /api/task/{task_id}": ("ui-action", "test_08_files_tasks.py"),
    "GET /api/task-jobs/list": ("ui-action", "test_08_files_tasks.py"),
    "GET /api/task-jobs/{task_id}": ("ui-action", "test_08_files_tasks.py"),
    "POST /api/task-jobs/{task_id}/cancel": ("ui-action", "test_08_files_tasks.py"),
    # 工具编辑器公共只读
    "GET /api/apis/list": ("ui-consume", "test_05_tool_agent_detail.py"),
}


EXCLUDED_REASONS: dict[str, str] = {
    # Agent：LLM 生成质量 / 运行时控制 / 改写内置资产
    "POST /api/agent/generate-code": "代码生成质量需真实 LLM 评估；假 LLM 只能验证连通性",
    "POST /api/agent/generate-spec": "规范生成质量需真实 LLM 评估；假 LLM 只能验证连通性",
    "POST /api/agent/register": "需要完整 Agent MD/code 夹具与安全审查，后续用私有 Agent 夹具补齐",
    "DELETE /api/agent/{agent_id}": "会删除内置 Agent；需私有 Agent 夹具",
    "POST /api/agent/{agent_id}/execute": "会启动真实 Agent 任务，耗时长且改变运行态",
    "POST /api/agent/{agent_id}/restart": "会重启内置 Agent，影响后续用例",
    "POST /api/agent/{agent_id}/save-code": "会改写内置 Agent 代码；需私有夹具",
    "POST /api/agent/{agent_id}/save-spec": "会改写内置 Agent 规范；需私有夹具",
    "POST /api/agent/{agent_id}/save-step1": "会改写内置 Agent 需求文档；需私有夹具",
    "POST /api/agent/{agent_id}/start": "会拉起额外 Agent 子进程，影响运行态",
    "POST /api/agent/{agent_id}/stop": "会停止内置 Agent，影响对话用例",
    "POST /api/agent/{agent_id}/sync-spec-from-code": "会调用 LLM 改写内置 Agent 规范",
    "POST /api/agent/auto-debug": "会启动自动调试循环并反复调用 LLM",
    "POST /api/agent/auto-debug/stop": "与 auto-debug 成对使用",
    "POST /api/agent/bus/call": "需要可控 Agent 运行时与 RPC 夹具；避免影响默认 Agent",
    "POST /api/agent/bus/publish": "会向 Agent 总线发布消息，影响运行态",
    "POST /api/agent/{agent_id}/fork": "会复制内置 Agent 到私有目录，产生额外资产",
    "DELETE /api/agent/{agent_id}/fork": "会删除私有 Agent 副本，依赖 fork 前置",
    "POST /api/agent/{agent_id}/chat-history/migrate": "会迁移历史会话，破坏性写操作",
    # 数据：归属改写 / 任意目录扫描 / LLM 生成
    "POST /api/data/claim-local": "会把历史条目归属改为当前用户，破坏种子归属",
    "POST /api/data/scan-directory": "会扫描部署机任意目录，越权面过大",
    "POST /api/data/generate-spec": "规范生成需真实 LLM 质量评估",
    "POST /api/data/match-tools": "工具匹配依赖真实 LLM 语义质量评估",
    # LLM / 配置写
    "GET /api/llm/test-connection": "会向外部服务发起真实网络请求，不属于离线套件",
    "POST /api/config/llm": "会改写全局 LLM 配置，破坏假 LLM 环境",
    "POST /api/auth/password": "修改当前账号密码会让后续令牌失效",
    "GET /api/agent/{agent_id}/api-doc/download": "导出文件端点依赖具体 Agent 的 API 文档产物；只验证 api-doc 读接口",
    # 迁移包 / 依赖安装
    "POST /api/migrate/agent/import-upload": "会写入私有资源根，需专用迁移包夹具",
    "POST /api/migrate/agent/{agent_id}/install-deps": "会执行 pip 安装，改变环境",
    "POST /api/migrate/tool/import-upload": "会写入私有资源根，需专用迁移包夹具",
    # 模型：需要真实权重
    "POST /api/model/register": "注册模型需要真实权重路径与产物，测试环境不提供",
    "POST /api/model/purge-missing": "会批量删除模型条目，破坏性写操作",
    "DELETE /api/model/{model_id}": "会删除内置模型条目；需私有模型夹具",
    # 工具：LLM 生成 / 自动调试 / 装依赖
    "POST /api/tool/auto-debug": "会启动自动调试循环并反复调用 LLM",
    "POST /api/tool/auto-debug/stop": "与 auto-debug 成对使用",
    "POST /api/tool/generate-code": "代码生成质量需真实 LLM 评估",
    "POST /api/tool/generate-spec": "规范生成质量需真实 LLM 评估",
    "POST /api/tool/import": "会写入私有资源根，需专用迁移包夹具",
    "POST /api/tool/{tool_id}/install-deps": "会执行 pip 安装，改变环境",
    "POST /api/tool/{tool_id}/modify-and-debug": "会调用 LLM 改写并启动自动调试",
    "POST /api/tool/{tool_id}/modify-code": "会调用 LLM 改写工具代码",
    "POST /api/tool/{tool_id}/refine-spec-node": "会调用 LLM 精化规范节点",
    "POST /api/tool/{tool_id}/refine-spec-node-stream": "会调用 LLM 精化规范节点（SSE）",
    "POST /api/tool/{tool_id}/sync-spec-from-code": "会调用 LLM 改写规范",
    "POST /api/tool/test": "沙箱代码执行需要额外输入/依赖，静态冒烟已覆盖注册链路",
    "POST /api/tool/upload-test-file": "仅为沙箱测试上传文件，独立端到端价值低",
    "POST /api/tool/{tool_id}/execute": "执行依赖工具独立 venv/数据；默认只做静态冒烟，按需 --tools 执行",
    "POST /api/cron/jobs/{job_id}/trigger": "会真实触发工具/Agent 执行，依赖工具 venv 与运行态；只验证创建/更新/删除",
    # 其它破坏性
    "DELETE /api/tool/{tool_id}": "会删除私有工具夹具；覆盖写接口后由夹具自己清理，不在通用路由用例重复删除",
}


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _matched_keys(inventory: dict, hits_path: Path) -> set[str]:
    routes = inventory.get("routes") or []
    compiled = [
        {**route, "_regex": _compile_route_path(route["path"])} for route in routes
    ]
    compiled.sort(key=lambda item: _route_sort_key(item["path"]), reverse=True)
    matched: set[str] = set()
    if not hits_path.exists():
        return matched
    for line in hits_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        normalized = _normalize_hit(record)
        if normalized is None:
            continue
        method, path = normalized
        for route in compiled:
            if route["method"] == method and route["_regex"].match(path):
                matched.add(route["key"])
                break
    return matched


def main() -> int:
    parser = argparse.ArgumentParser(description="更新 wxbit 路由覆盖台账")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--hits", type=Path, default=DEFAULT_HITS)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    inventory = _load_json(args.inventory, {})
    manifest = _load_json(args.manifest, {})
    entries: dict = manifest.get("routes") or {}
    if not inventory.get("routes"):
        print(f"[classify] 路由清单不存在或为空：{args.inventory}")
        return 1

    matched = _matched_keys(inventory, args.hits)
    newly_covered = 0
    for route in inventory["routes"]:
        key = route["key"]
        entry = entries.setdefault(key, {})
        entry.setdefault("method", route["method"])
        entry.setdefault("path", route["path"])
        entry.setdefault("tags", route["tags"])

        if key in EXCLUDED_REASONS:
            entry["mode"] = "excluded"
            entry["reason"] = EXCLUDED_REASONS[key]
            entry["spec"] = ""
            continue
        if key in matched and entry.get("mode") in (None, "", "uncovered", "api"):
            mode, spec = UI_ROUTES.get(key, ("api", "test_09_routes_api_read.py"))
            entry["mode"] = mode
            entry["spec"] = spec
            entry["reason"] = ""
            newly_covered += 1

    uncovered = sorted(
        key
        for key, entry in entries.items()
        if (entry.get("mode") or "uncovered") == "uncovered"
    )
    print(
        f"[classify] 命中 {len(matched)}，新标记覆盖 {newly_covered}，"
        f"排除 {len(EXCLUDED_REASONS)}，仍未覆盖 {len(uncovered)}"
    )
    for key in uncovered:
        print(f"  - 未覆盖：{key}")

    if not args.check:
        manifest["routes"] = dict(sorted(entries.items()))
        args.manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"[classify] 已写入 {args.manifest}")
    return 1 if uncovered else 0


if __name__ == "__main__":
    raise SystemExit(main())
