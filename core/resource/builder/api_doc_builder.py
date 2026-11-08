"""Web 服务式 Agent 的 API 说明文档生成器

根据 Agent 的 spec（server + apis 声明）生成 Markdown 格式的接口说明文档，
供用户在 Agent 界面浏览或下载。
"""

from __future__ import annotations


def build_api_doc_markdown(agent_id: str, agent_name: str, spec) -> str:
    """生成 Web 服务式 Agent 的 API 说明文档（Markdown）。

    Args:
        agent_id: Agent 唯一标识
        agent_name: Agent 显示名称
        spec: AgentSpec 实例（含 server / apis 字段）
    """
    server = spec.server or {}
    host = server.get("host", "127.0.0.1")
    port = server.get("port") or "<运行时分配>"
    apis = spec.apis or []

    lines: list[str] = []
    lines.append(f"# {agent_name} — API 接口文档\n")
    lines.append(f"> Agent ID：`{agent_id}`　类型：Web 服务式\n")

    lines.append("## 服务信息\n")
    lines.append("| 项 | 值 |")
    lines.append("|----|----|")
    lines.append(f"| 监听地址 | `{host}` |")
    lines.append(f"| 端口 | `{port}`（服务启动后由引擎分配） |")
    lines.append(f"| 接口数量 | {len(apis)} |")
    lines.append("")

    if not apis:
        lines.append("_该 Agent 尚未声明任何 API。_\n")
        return "\n".join(lines)

    lines.append("## 接口列表\n")
    for i, api in enumerate(apis, start=1):
        name = api.get("name", f"api{i}")
        method = (api.get("method") or "POST").upper()
        path = api.get("path", "/")
        desc = api.get("description") or api.get("desc") or ""

        lines.append(f"### {i}. {name}\n")
        if desc:
            lines.append(f"{desc}\n")
        lines.append(f"- **请求方法**：`{method}`")
        lines.append(f"- **请求路径**：`{path}`")
        lines.append("")

        # 请求参数
        params = api.get("params", []) or []
        if params:
            lines.append("**请求参数**\n")
            lines.append("| 参数名 | 类型 | 必填 | 说明 |")
            lines.append("|--------|------|------|------|")
            for p in params:
                pname = p.get("name", "")
                ptype = p.get("type", "string")
                required = "是" if p.get("required") else "否"
                pdesc = p.get("desc", "") or ""
                lines.append(f"| `{pname}` | {ptype} | {required} | {pdesc} |")
            lines.append("")
        else:
            lines.append("_无请求参数_\n")

        # 请求示例
        example = {p.get("name"): _example_value(p.get("type", "string")) for p in params}
        lines.append("**请求示例**\n")
        lines.append("```bash")
        lines.append(f"curl -X {method} http://{host}:{port}{path} \\")
        lines.append("  -H \"Content-Type: application/json\" \\")
        import json as _json
        lines.append(f"  -d '{_json.dumps(example, ensure_ascii=False)}'")
        lines.append("```\n")

        # 响应字段
        response = api.get("response", []) or []
        if response:
            lines.append("**响应字段**\n")
            lines.append("| 字段 | 说明 |")
            lines.append("|------|------|")
            for r in response:
                rname = r.get("name", "")
                rfrom = r.get("from", "")
                hint = _response_hint(rname)
                lines.append(f"| `{rname}` | {hint or ('来源：`' + rfrom + '`')} |")
            lines.append("")

    lines.append("## 使用说明\n")
    lines.append("1. 在 Agent 界面点击「启动」，服务将监听上述端口。")
    lines.append("2. 使用界面「API 测试」区选择接口并填写参数进行测试。")
    lines.append("3. 或按上述 `curl` 示例从外部直接调用。")
    lines.append("")

    return "\n".join(lines)


def _example_value(ptype: str) -> object:
    t = (ptype or "string").lower()
    if "int" in t:
        return 10
    if "float" in t or "number" in t:
        return 0.5
    if "bool" in t:
        return True
    return "/path/to/file"


def _response_hint(name: str) -> str:
    hints = {
        "status": "执行状态（success / failed）",
        "message": "结果说明",
        "model_path": "使用的模型文件路径",
        "count": "检测到的目标数量",
        "detections": "检测结果列表，每项含目标名称（class）、置信度（confidence）与 bounding-box（bbox），格式 [x1, y1, x2, y2]",
    }
    return hints.get(name, "")
