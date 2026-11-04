"""User-visible remediation and explicit retries, confined to academic tools."""

from __future__ import annotations

import re

from core.security.secrets import SENSITIVE_KEY_NAMES, scrub_text

from .common import Store, ToolError, now, result, uid

TOOLS = {
    "github": "GitHub学术代码查找",
    "papers": "顶会顶刊论文跟踪",
    "reproduction": "GPU代码复现",
    "writing": "论文撰写",
}
READ_MODES = {
    "github": {"find", "search", "inspect", "paper_code", "compare", "search_code", "export"},
    "papers": {"search", "sync", "diagnose_sources", "paper_detail", "related", "export", "digest"},
    "reproduction": {"inspect", "preflight", "status", "logs", "report", "diagnose", "benchmark"},
    "writing": {"export", "outline", "draft", "revise", "literature_review"},
}
DEFAULTS = {"github": "search", "papers": "search", "writing": "create", "reproduction": "inspect"}
REQUEST = re.compile(
    r"\s*(?:我已(?:配置|安装|修复)(?:好|完成)?[，,。\s]*)?(?:请)?"
    r"(查看|重试|取消恢复)\s*(recovery-[0-9a-f]{32})"
    r"(?:[，,；;]\s*(?:配置文件|config_file)\s*[:：=]?\s*(.+?))?[。!！]?\s*"
)
IDENTIFIER = re.compile(r"(?:writing|repository|subscription|digest|job|plan|paper)-[0-9a-f]{32}")
NUMERALS = {word: number for number, word in enumerate("一二三四五六七八九十", 1)}


def followup(text):
    if not isinstance(text, str):
        return None
    legacy = REQUEST.fullmatch(text)
    if legacy:
        action, ident, path = legacy.groups()
        return {"action": action, "id": ident, "config_file": path}
    path_match = re.search(
        r"配置文件(?:在|是|为)?\s*[:：]?\s*(?:[\"“](.+?)[\"”]|([A-Za-z]:[\\/][^，；。\n]+|/[^，；。\n]+))", text
    )
    path = None
    if path_match:
        path = path_match.group(1) or path_match.group(2)
        text = text[:path_match.start()] + text[path_match.end():]
    plain = re.sub(r"[\s，,。.!！；;]", "", text)
    plain = re.sub(r"^(?:麻烦你|帮我|麻烦|请)", "", plain)
    plain = re.sub(r"(?:一下|吧)$", "", plain)
    selected = re.fullmatch(r"(重试|查看|我选|选择|选)?第?([一二三四五六七八九十]|\d+)(?:个任务|个|项|任务)?", plain)
    if selected:
        action, number = selected.groups()
        return {"action": action if action in {"重试", "查看"} else None,
                "selection": NUMERALS[number] if number in NUMERALS else int(number), "config_file": path}
    if re.fullmatch(r"(?:(?:我)?(?:已经|已)?(?:配置|安装|修复|处理)(?:好了|好|完成|完毕))?(?:请)?(?:继续(?:刚才的(?:任务|操作))?|重试(?:刚才的(?:任务|操作))?|再试(?:一次)?)", plain):
        return {"action": "重试", "config_file": path}
    if plain in {"查看刚才的问题", "查看问题", "查看进度", "查看状态", "现在什么情况", "为什么失败了"}:
        return {"action": "查看", "config_file": path}
    if plain in {"取消重试", "取消恢复", "不要重试了"}:
        return {"action": "取消恢复", "config_file": path}
    return None


def safe_params(value, key="", depth=0):
    """Reject secrets without corrupting tool-generated record IDs or fixed commits."""
    if depth > 8 or key.lower() in SENSITIVE_KEY_NAMES:
        raise ValueError("Replay parameters may contain secrets")
    if isinstance(value, dict):
        return {k: safe_params(v, k, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_params(v, key, depth + 1) for v in value]
    if isinstance(value, str):
        if (key.endswith("_id") and IDENTIFIER.fullmatch(value)) or (
            key == "ref" and re.fullmatch(r"[0-9a-f]{40}", value)
        ):
            return value
        if scrub_text(value) != value or re.search(
            r"(?i)(?:密码|口令|password|api[_ -]?key|token)\s*[:：=]\s*\S+", value
        ):
            raise ValueError("Replay text may contain secrets")
    return value


def is_request(text):
    return followup(text) is not None


def issues(response):
    """Preserve partial results while exposing each actual failed source."""
    data = response.get("data", {})
    rows = data.get("sources", []) or data.get("probes", [])
    if not rows:
        rows = data.get("details", {}).get("sources", [])
    failed = [row for row in rows if row.get("state") == "failed"]
    if failed:
        return failed
    if data.get("ready") is False and data.get("issues"):
        return [{"error_code": "PREFLIGHT_FAILED", "error": "; ".join(data["issues"])}]
    if data.get("state") in {"failed", "timed_out"} and data.get("plan_id"):
        reason = str(data.get("error") or "请查看 logs 和 diagnose 获取实际运行日志。")
        return [{"error_code": "TRAINING_FAILED", "error": f"训练 {data.get('id')} 状态：{data['state']}；{reason}"}]
    if response.get("status") == "failed":
        return [{**data, "error": response.get("message", "操作失败")}]
    return []


def advice(issue, module):
    code = issue.get("error_code", "INTERNAL_ERROR")
    evidence = issue.get("details") or issue.get("evidence") or {}
    service = evidence.get("service") or issue.get("source")
    host = evidence.get("host", "")
    if not service:
        service = "openreview" if "openreview" in host else "github" if "github" in host else None
    if code.startswith("LLM_") or (code == "CONFIG_REQUIRED" and module == "writing"):
        service = "llm"
    text = issue.get("error") or issue.get("text") or code
    action = "请按错误内容修正输入或运行环境，再明确发起重试；尚未确认故障已解决。"
    setup = None
    fields = evidence.get("required_fields", [])
    if code == "INPUT_REQUIRED":
        action = "直接用普通文字回复缺少的信息即可，已提供的描述会保留；无需重新输入整个任务或 JSON。"
    elif code in {"CONFIG_REQUIRED", "AUTH_REQUIRED", "AUTH_FAILED", "LLM_AUTH_ERROR"}:
        if service in {"github", "llm", "openreview"}:
            fields = fields or {
                "github": ["token"], "llm": ["api_key", "base_url", "model"],
                "openreview": ["username", "password"],
            }[service]
            setup = "python -m resources.tools._academic.configuration --service " + service
            action = (
                f"请配置或检查 {service} 的 {', '.join(fields)}。在后端所在机器、同一应用用户下运行：{setup}。"
                "密码和 Key 在终端隐藏输入；也可提供部署机器上的配置文件路径。完成后在对话中重试。"
            )
    elif code == "CHALLENGE_REQUIRED":
        action = (
            "来源返回站点验证，当前没有取得论文结果。请按官方验证要求处理或联系来源服务方；"
            "DBLP 搜索接口没有可在本工具填写的 API Key。访问恢复后再重试同一来源。"
        )
    elif code == "MFA_REQUIRED":
        action = "OpenReview 要求二次验证；当前工具没有后台 MFA 配置入口，需要适配官方认证流程后再重试。"
    elif code == "DEPENDENCY_MISSING":
        if "xelatex" in text.lower():
            action = (
                "请在后端运行环境安装 XeLaTeX、中文宏包和 Noto Serif CJK SC 字体，"
                "重启后端使 PATH 生效，并用 xelatex --version 检查。安装完成后重试原 PDF 导出。"
            )
        else:
            action = "请在后端运行环境安装错误指出的依赖，再重试；依赖安装不会由此消息自动执行。"
    elif code in {"RATE_LIMITED", "LLM_RATE_LIMITED"}:
        action = "请求被限流，请等待服务配额恢复后重试同一请求；不切换账号或来源。"
        reset = evidence.get("x-ratelimit-reset")
        if isinstance(reset, int) and 0 < reset < 253402300800:
            from datetime import UTC, datetime

            action += " 服务返回的额度重置时间为 " + datetime.fromtimestamp(reset, UTC).isoformat() + "（UTC）；届时仍需实际请求确认。"
        if service == "github":
            action += " 如需使用自己的 GitHub 授权额度，可在本机运行 python -m resources.tools._academic.configuration --service github，隐藏输入 Token 后回复“配置好了，继续”；不要将 Token 发到聊天中。"
    elif code == "SOURCE_QUERY_REJECTED":
        reasons = [item.get("message", "") for item in evidence.get("validation_errors", [])]
        action = (
            "GitHub 拒绝了检索条件：" + "; ".join(reasons)
            + "。请确认语法、仓库当前所属组织和名称、账号访问权限，重新提交修正后的检索；HTTP 422 本身不能证明是哪一种原因。"
        )
    elif code in {"NETWORK_TIMEOUT", "SOURCE_UNAVAILABLE", "DNS_ERROR", "PROXY_ERROR", "TLS_ERROR"}:
        action = "请检查后端机器的网络、DNS、代理和证书；恢复连接后重试同一来源，保留原查询条件。"
    elif code == "PREFLIGHT_FAILED":
        action = "复现预检未通过，尚未提交训练。请根据预检明细补齐入口、数据或运行环境，再提交修正后的复现任务。"
    elif code == "TRAINING_FAILED":
        action = "训练未成功，保留原任务及日志。请检查日志和实际检查点，修正计划后显式使用 resume；普通重试只重新查看状态。"
    elif code in {"FILE_NOT_FOUND", "CONFIG_STORAGE_ERROR", "NOT_FOUND"}:
        action = "请确认文件或记录存在于后端机器、属于当前用户；配置文件只提供路径。修正后重试，路径变化时重新提供对应参数。"
    elif code == "SOURCE_HTTP_ERROR" and evidence.get("http_status") == 404:
        action = "来源接口未找到可访问的资源，请核对仓库当前地址、分支/提交与账号权限；404 本身不能证明仓库不存在。"
        if evidence.get("endpoint"):
            action += " 失败接口：" + scrub_text(evidence["endpoint"])
    elif code in {"TIMEOUT", "COMMAND_FAILED"}:
        action = "命令失败或超时。请检查错误与实际日志；若涉及训练，先查看已有任务状态，确认后使用其续跑入口。"
    return {
        "error_code": code, "source": service, "reason": scrub_text(text),
        "action": action, "required_fields": fields, "setup_command": setup,
    }


def complete_input(module, params):
    """Join an explicitly requested source/command reply to its original reproduction request."""
    if module != "reproduction" or not isinstance(params.get("request"), str):
        return params
    text = params["request"].strip()
    source_reply = re.search(r"(?:[A-Za-z]:[\\/]|^/|[\"“]/)", text)
    command_reply = re.match(r"(?:python(?:3)?\b|torchrun\b|bash\b|运行|执行|启动)", text)
    if not source_reply and not command_reply:
        return params
    # A full new task must not be merged into an older unfinished request.
    if re.search(r"复现|重现", text) or (source_reply and re.search(r"运行|执行|启动", text)):
        return params
    store = Store()
    pending = [entry for entry in store.find("recovery", 10000)
               if entry["module"] == module and entry["state"] == "waiting" and entry.get("input_request")]
    if not pending:
        return params
    if len(pending) > 1:
        raise ToolError("有多个未补齐的复现任务，请把目标源码目录和启动命令写在同一句话中，避免补充到错误任务；无需 JSON。", "INPUT_REQUIRED")
    entry = pending[0]
    field = entry["input_field"]
    if (field == "source_path" and not source_reply) or (field == "command" and not command_reply):
        return params
    if field == "command" and not re.match(r"运行|执行|启动", text):
        text = "运行 " + text
    combined = entry["input_request"] + "，" + text
    safe_params({"request": combined})
    store.put("recovery", {**entry, "state": "answered"}, expected=entry["revision"])
    return {**entry.get("input_params", {}), **params,
            "mode": entry.get("input_params", {}).get("mode", "reproduce"), "request": combined}


def retry_params(module, params, response, resume_params=None):
    candidate = dict(params)
    mode = candidate.get("mode", DEFAULTS[module])
    data = response.get("data", {})
    if data.get("error_code") in {"REVISION_CONFLICT", "RECOVERY_STALE"}:
        return None
    # A track call already created a subscription: synchronize it, never create it twice.
    if module == "papers" and mode == "track" and data.get("subscription", {}).get("id"):
        candidate = {"mode": "sync", "subscription_id": data["subscription"]["id"]}
    elif module == "writing" and mode == "compose":
        saved = resume_params
        if not saved:
            return None
        candidate = saved
    if params.get("config_file"):
        candidate["config_file"] = params["config_file"]
    if candidate.get("mode", mode) not in READ_MODES[module]:
        return None
    # Credentials never belong in replay records. Do not replay redacted or truncated inputs.
    try:
        return safe_params(candidate)
    except ValueError:
        return None


def describe(record):
    lines = [f"{TOOLS[record['module']]}：恢复记录状态 {record['state']}。"]
    if record["state"] == "resolved":
        lines.append("重试已完成，以下原因是此前失败时的历史记录。")
    for item in record["issues"]:
        lines.extend([f"[{item['error_code']}] {item['reason']}", item["action"]])
    if record["can_retry"] and record["state"] == "waiting":
        lines.append("修正后可直接回复“配置好了，继续”或“再试一次”；想先了解原因可说“查看刚才的问题”。有多个待处理任务时会先列出供你选择。")
    elif record["state"] == "waiting" and record.get("input_request"):
        lines.append("请直接回复上述缺少的信息，我会接着处理这项任务。")
    elif record["state"] == "waiting":
        lines.append("此操作不能直接重放，请按上述说明重新提交任务；训练提交、部署与删除不会自动再次执行。")
    elif record["state"] == "retrying":
        lines.append("重试正在执行或曾被外部中断；结果尚未确认，请检查原任务状态，不会自动重复执行。")
    elif record["state"] == "stale":
        lines.append("文稿版本已变化，此恢复记录已失效。请检查当前文稿并重新提交任务。")
    lines.append(f"恢复记录：{record['id']}；状态：{record['state']}。")
    return "\n\n".join(lines)


def public(record):
    data = {key: record[key] for key in (
        "id", "module", "state", "can_retry", "attempts", "issues", "created_at",
    )}
    data["can_retry"] = record["can_retry"] and record["state"] == "waiting"
    if record.get("result"):
        data["result"] = record["result"]
    if record.get("params"):
        data["task"] = {key: value for key, value in record["params"].items() if not key.startswith("_")}
    return data


def project_guard(store, module, params):
    if module == "writing" and params and params.get("project_id"):
        project = store.get("writing", params["project_id"])
        return {"id": project["id"], "revision": project["revision"]}
    return None


def attach(module, params, response, record=None, resume_params=None):
    failures = issues(response)
    if not failures:
        return response
    steps = [advice(item, module) for item in failures]
    saved_params = retry_params(module, params, response, resume_params)
    entry = {
        **(record or {}),
        "id": record["id"] if record else uid("recovery"),
        "module": module, "state": "waiting", "can_retry": saved_params is not None,
        "params": saved_params, "issues": steps,
        "attempts": record.get("attempts", 0) if record else 0,
        "created_at": record.get("created_at", now()) if record else now(),
    }
    try:
        store = Store()
        if module == "reproduction" and response["data"].get("error_code") == "INPUT_REQUIRED" and params.get("request"):
            field = response["data"].get("details", {}).get("field")
            if field in {"source_path", "command"}:
                try:
                    input_params = safe_params(params)
                except ValueError:
                    pass  # Never retain a task containing apparent credentials.
                else:
                    entry["input_request"] = params["request"]
                    entry["input_field"] = field
                    entry["input_params"] = input_params
        if record is None:
            try:
                entry["project_guard"] = project_guard(store, module, saved_params)
            except ToolError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                entry["can_retry"] = False
        entry = store.put("recovery", entry, expected=record["revision"] if record else 0)
        response["data"]["recovery"] = public(entry)
        notice = describe(entry)
    except Exception:  # noqa: BLE001 - Preserve the original error if recovery storage itself fails.
        notice = "\n\n".join(f"[{item['error_code']}] {item['reason']}\n{item['action']}" for item in steps)
        notice += "\n\n恢复记录保存失败，不能按编号重试；请保留原任务并在修复存储后重新提交。"
        response["data"]["recovery_storage_error"] = True
    if response.get("status") == "success":
        notice = response.get("message", "") + "\n\n" + notice
    response["message"] = notice
    if response.get("output_format") == "text":
        response["data"]["text"] = notice
    return response


def execute(module, params):
    from .common import invoke

    parsed = followup(params.get("request", ""))
    if not parsed:
        raise ToolError("可以回复“配置好了，继续”“查看刚才的问题”或“取消重试”；有多个任务时回复“第二个”等选择，无需 JSON。")
    action, ident, config_path = parsed.get("action"), parsed.get("id"), parsed.get("config_file")
    store = Store()
    if not ident:
        if "selection" in parsed:
            try:
                menu = store.get("recovery_menu", module)
            except ToolError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                raise ToolError("还没有列出待处理任务。请先说“查看刚才的问题”或“继续”，再选择对应任务。", "RECOVERY_SELECTION_REQUIRED") from None
            if menu.get("kind") == "writing_export_format":
                raise ToolError("现在需要选择导出格式，而不是任务序号。请直接回复“Word”“PDF”“Markdown”等格式。", "INPUT_REQUIRED")
            index = parsed["selection"] - 1
            if not 0 <= index < len(menu["ids"]):
                raise ToolError(f"请从刚才列出的 1–{len(menu['ids'])} 项中选择。", "RECOVERY_SELECTION_REQUIRED")
            if menu.get("kind") == "writing_export":
                if module != "writing" or action is not None:
                    raise ToolError("刚才的列表是选择导出文稿，请直接回复序号；恢复失败任务请先说“继续”。")
                response = invoke("writing", {"mode": "export", "project_id": menu["ids"][index], "format": menu["format"]})
                store.delete("recovery_menu", module)
                return response
            ident = menu["ids"][index]
            action = action or menu["action"]
            config_path = config_path or menu.get("config_file")
        else:
            candidates = [entry for entry in store.find("recovery", 10000)
                          if entry["module"] == module and entry["state"] in {"waiting", "retrying", "stale"}]
            if not candidates:
                raise ToolError("当前工具没有待处理的失败任务。请说明新的任务；查看已完成记录时可提供原恢复编号。", "NO_PENDING_RECOVERY")
            if len(candidates) == 1:
                ident = candidates[0]["id"]
            else:
                visible = candidates[:20]
                # The displayed numbering is persisted so later arrivals cannot change its meaning.
                store.put("recovery_menu", {"id": module, "ids": [entry["id"] for entry in visible],
                                           "action": action, "config_file": params.get("config_file") or config_path})
                lines = [f"{TOOLS[module]}有 {len(candidates)} 个待处理任务，请选择要{action}哪一个："]
                for index, entry in enumerate(visible, 1):
                    task = entry.get("params") or {}
                    label = task.get("request") or task.get("query") or task.get("project_id") or task.get("job_id") or task.get("mode", "任务")
                    reason = entry["issues"][0]["reason"] if entry["issues"] else entry["state"]
                    lines.append(f"{index}. {str(label)[:100]}：{reason[:180]}")
                lines.append("直接回复“第一个”“第二个”或序号即可；尚未执行任何重试。")
                if len(candidates) > len(visible):
                    lines.append("当前显示最近 20 项，较早任务可使用原恢复编号指定。")
                message = "\n\n".join(lines)
                return result({"text": message, "needs_input": True, "choices": [public(entry) for entry in visible]}, message)
    # A status read must remain available while another process holds the execution lease.
    if action == "查看":
        entry = store.get("recovery", ident)
        if entry["module"] != module:
            raise ToolError("恢复编号属于另一个工具，请使用最初报错的工具", "RECOVERY_TOOL_MISMATCH")
        return result(public(entry), describe(entry))
    with store.lock("recovery:" + ident, wait=0):
        entry = store.get("recovery", ident)
        if entry["module"] != module:
            raise ToolError("恢复编号属于另一个工具，请使用最初报错的工具", "RECOVERY_TOOL_MISMATCH")
        if action == "取消恢复":
            entry = store.put("recovery", {**entry, "state": "cancelled"}, expected=entry["revision"])
            return result(public(entry), "已取消此恢复记录；不会取消已有训练或论文订阅。")
        if entry["state"] != "waiting" or not entry["can_retry"]:
            raise ToolError(
                f"恢复记录状态为 {entry['state']}，不允许再次执行。请先查看原任务状态。",
                "RECOVERY_NOT_RETRYABLE",
            )
        replay = dict(entry["params"])
        if module == "writing" and replay.get("project_id"):
            guard = entry.get("project_guard")
            current = project_guard(store, module, replay)
            if guard is None or current != guard:
                store.put("recovery", {**entry, "state": "stale", "can_retry": False}, expected=entry["revision"])
                raise ToolError(
                    "文稿版本已变化，或旧恢复记录缺少版本证据。请检查当前文稿并重新提交任务；本次未执行生成或导出。",
                    "RECOVERY_STALE",
                )
            # Also check inside writing.execute, closing the gap between this read and execution.
            replay["_recovery_project_revision"] = guard["revision"]
        if params.get("config_file") or config_path:
            replay["config_file"] = params.get("config_file") or config_path.strip().strip('"\'“”')
        entry = store.put(
            "recovery", {**entry, "state": "retrying", "attempts": entry["attempts"] + 1},
            expected=entry["revision"],
        )
        response = invoke(module, replay, _recovery=entry)
        if not issues(response):
            data = response.get("data", {})
            summary = {key: data[key] for key in (
                "file_path", "project_id", "revision", "id", "total", "total_matches", "displayed_matches",
            ) if key in data}
            summary["output_format"] = response.get("output_format")
            entry = store.put("recovery", {**entry, "state": "resolved", "result": summary}, expected=entry["revision"])
            response["data"]["recovery"] = public(entry)
            response["message"] += f"\n恢复记录 {ident} 已完成本次真实重试。"
        return response


def main():
    import argparse
    import json

    from .common import invoke

    parser = argparse.ArgumentParser(description="查看或明确重试当前用户的学术工具失败任务")
    parser.add_argument("--tool", choices=list(TOOLS), required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--config-file")
    args = parser.parse_args()
    response = invoke(args.tool, {
        "mode": "recover", "request": args.request, "config_file": args.config_file,
    })
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 1 if issues(response) else 0


if __name__ == "__main__":
    raise SystemExit(main())
