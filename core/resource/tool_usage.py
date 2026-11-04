"""工具被谁引用 —— 引用体检（纯只读，不调 LLM）。

## 为什么需要

把某个参数从「可选」改成「必填」时，真正的风险不在工具自身，而在**调用方**：
Agent 的 flow/apis 步骤、其它工具的内部调用。它们不会因为必填化而中断
（执行层不做校验），但可能整条链路拿不到值。改之前先告诉使用者"谁在用、
谁没传这个参数"，比事后排查便宜得多。

## 扫描面（两类）

- **Agent**：``agents/definitions/*.md`` 的 ``flow[].tool`` / ``apis[].flow[].tool``
  （``AgentSpec.parse_structured_fields`` 已把它们归一成真实工具 id），
  外加正文里的 ``【【显示名】】``
- **工具**：``tools/definitions/*.md`` 的 ``【【名】】``，以及 ``tool.py`` 里
  **真实调用**的 ``_call_tool("名", ...)``

⚠️ 每个生成的 ``tool.py`` 都带一个 ``def _call_tool(tool_name, **params)`` 定义，
扫描必须只认调用、不认定义，否则会把所有工具误判成"引用了别人"。

读取顺序一律私有优先（``paths.read_roots()``），与注册表「私有覆盖内置」一致。
"""

from __future__ import annotations

import json
import re

from core.resource import paths
from core.resource.registry.tool_registry import (
    find_tool_entry,
    normalize_ref,
)

_TOOL_MARK_RE = re.compile(r"【【(.+?)】】")
# 真实调用：_call_tool("名", k=v) / .call_tool("名", ...)
_CALL_TOOL_RE = re.compile(r"(?<!def )_call_tool\(\s*[\"']([^\"']+)[\"']")
_AGENT_CALL_RE = re.compile(r"\.call_tool\(\s*[\"']([^\"']+)[\"']")

_SKIP_SUFFIX = ("-demand.md", "-reference.md")


def _definition_files(space: str):
    """遍历某空间的规范文档（私有优先，同名内置副本跳过）。"""
    seen: set[str] = set()
    for root in paths.read_roots():
        d = root / space / "definitions"
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            if f.name.endswith(_SKIP_SUFFIX) or f.name in seen:
                continue
            seen.add(f.name)
            yield f


def _implementation_files(space: str, filename: str):
    """遍历某空间的实现文件（私有优先，同名跳过）。"""
    seen: set[str] = set()
    for root in paths.read_roots():
        d = root / space / "implementations"
        if not d.is_dir():
            continue
        for f in sorted(d.glob(f"*/{filename}")):
            key = f.parent.name
            if key in seen:
                continue
            seen.add(key)
            yield f


def _read(path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except Exception:
        return ""


def _mentions_param(step: dict, param: str) -> bool | None:
    """该步骤是否显式传了某参数；无法判断返回 None。"""
    if not param:
        return None
    try:
        text = json.dumps(step, ensure_ascii=False)
    except Exception:
        return None
    return param in text


def _agent_references(md: str, param: str) -> list[tuple[str, str, bool | None]]:
    """从 Agent MD 提取对工具的引用：[(ref, via, passes_param)]。"""
    refs: list[tuple[str, str, bool | None]] = []
    try:
        from core.agent.base import AgentSpec

        spec = AgentSpec(id="_scan_", name="_scan_", raw_md=md)
        spec.parse_structured_fields()
        steps = list(spec.flow or [])
        for api in spec.apis or []:
            steps.extend(api.get("flow") or [])
        for st in steps:
            if not isinstance(st, dict):
                continue
            tool = str(st.get("tool") or "").strip()
            if tool:
                refs.append((tool, "flow", _mentions_param(st, param)))
    except Exception:
        pass

    for m in _TOOL_MARK_RE.finditer(md):
        refs.append((m.group(1), "text", None))
    return refs


def _tool_references(md: str, code: str, param: str) -> list[tuple[str, str, bool | None]]:
    """从工具 MD + 实现代码提取对其它工具的引用。"""
    refs: list[tuple[str, str, bool | None]] = []
    for m in _TOOL_MARK_RE.finditer(md):
        refs.append((m.group(1), "text", None))

    for m in _CALL_TOOL_RE.finditer(code):
        line_end = code.find("\n", m.start())
        line = code[m.start(): line_end if line_end != -1 else len(code)]
        passes = (f"{param}=" in line) if param else None
        refs.append((m.group(1), "code", passes))
    return refs


def _resolve_id(ref: str) -> str:
    """把引用（id 或显示名）归一成工具 id；解析不到返回原值。"""
    rn = normalize_ref(ref)
    entry = find_tool_entry(rn)
    if entry is not None:
        return str(entry.get("id") or "").strip() or rn
    return rn


def scan_references(tool_id: str, tool_name: str = "", param: str = "") -> dict:
    """扫描「谁引用了该工具」。

    Args:
        tool_id: 目标工具 id
        tool_name: 目标工具显示名（正文里常写显示名）
        param: 关心的参数名；用于判断调用方是否显式传了它（None 表示无法判断）

    Returns:
        ``{"agents": [...], "tools": [...], "total": int}``
    """
    target_id = normalize_ref(tool_id)
    target_name = normalize_ref(tool_name or "")
    targets = {t for t in (target_id, target_name) if t}

    def _is_target(ref: str) -> bool:
        rn = normalize_ref(ref)
        if not rn:
            return False
        if rn in targets:
            return True
        return _resolve_id(rn) == target_id

    agents: dict[str, dict] = {}
    for f in _definition_files("agents"):
        agent_id = f.stem
        md = _read(f)
        if not md:
            continue
        hits = [r for r in _agent_references(md, param) if _is_target(r[0])]
        if not hits:
            continue
        m = re.search(r"^name:\s*(.+)$", md, re.M)
        agent_name = m.group(1).strip() if m else agent_id
        m_role = re.search(r"^\s*role\s*:\s*([a-zA-Z_-]+)", md, re.M)
        passes = [p for _, _, p in hits if p is not None]
        agents[agent_id] = {
            "id": agent_id,
            "name": agent_name,
            "role": m_role.group(1) if m_role else "",
            "via": sorted({v for _, v, _ in hits}),
            # None = 正文引用、无法判断是否传参；False = 明确没传
            "passes_param": (all(passes) if passes else None),
        }

    tools: dict[str, dict] = {}
    # 先建 id → 代码 的映射，避免在循环里反复 glob（工具数 50+）
    code_by_id = {f.parent.name: _read(f) for f in _implementation_files("tools", "tool.py")}
    for f in _definition_files("tools"):
        other_id = f.stem
        if other_id == target_id:
            continue
        md = _read(f)
        code = code_by_id.get(other_id, "")
        hits = [r for r in _tool_references(md, code, param) if _is_target(r[0])]
        if not hits:
            continue
        m = re.search(r"^name:\s*(.+)$", md, re.M)
        passes = [p for _, _, p in hits if p is not None]
        tools[other_id] = {
            "id": other_id,
            "name": m.group(1).strip() if m else other_id,
            "via": sorted({v for _, v, _ in hits}),
            "passes_param": (all(passes) if passes else None),
        }

    return {
        "tool_id": target_id,
        "param": param,
        "agents": list(agents.values()),
        "tools": list(tools.values()),
        "total": len(agents) + len(tools),
    }
