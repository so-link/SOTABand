"""内置消息路由智能体（todo 4.1）

接收 Web 请求 → 在工具空间中匹配工具 → 调用执行 → 返回 JSON。
让外部系统以标准 API 方式调用 SOTABand 工具能力（而非仅对话界面手动触发）。

匹配三级回退（matched_by 如实标注）：
  1. explicit —— 请求显式给出 tool（id 或 name，大小写不敏感）
  2. llm      —— 意图消息经工具发现器初筛 top-N 候选，LLM 选定工具并抽取参数
  3. keyword  —— LLM 未配置/失败时回退关键词 top1

执行统一走 BaseAgent.call_tool（常驻 Worker 优先、ToolExecutor 回退），
因此工具侧的静态安全审查 choke point（todo 3.2）自动生效。
"""

import difflib
import json
import re
import sys
from pathlib import Path
from typing import AsyncGenerator

from core.agent.base import BaseAgent, AgentContext, AgentRole


class MessageRouterAgent(BaseAgent):
    """消息路由智能体：Web 请求 → 工具空间匹配 → 执行 → JSON 响应"""

    CANDIDATE_LIMIT = 8
    # 显式声明角色（工厂按源码中的 WEB_SERVICE 标记探测 service_ready）
    expected_role = AgentRole.WEB_SERVICE

    def __init__(self, spec=None, config: dict = None):
        super().__init__(spec, config)
        self._llm = None

    async def resident_tool_ids(self) -> list[str]:
        # 动态匹配整个工具空间，不固定常驻任何工具
        return []

    # ── LLM（懒加载；失败一律降级为关键词匹配，不抛出） ──

    def _get_llm(self):
        if self._llm is None:
            from config.settings import settings
            from core.llm.client import create_llm_client
            self._llm = create_llm_client(settings.llm)
        return self._llm

    # ── 工具空间 ──

    async def _active_tools(self) -> list[dict]:
        from core.resource.registry.tool_registry import ToolRegistry
        tools = await ToolRegistry().list_all()
        return [t for t in tools if t.get("status") == "active"]

    @staticmethod
    def _param_defs(tool: dict) -> list[dict]:
        out = []
        for p in tool.get("param_meta", []) or []:
            out.append({
                "name": p.get("name"),
                "type": p.get("type", ""),
                "required": bool(p.get("required")),
                "desc": p.get("desc", ""),
            })
        return out

    def _tool_brief(self, tool: dict) -> dict:
        return {
            "id": tool.get("id"),
            "name": tool.get("name"),
            "description": tool.get("description") or "",
            "tags": tool.get("tags", []) or [],
            "params": self._param_defs(tool),
        }

    @staticmethod
    def _find_explicit(tools: list[dict], ref: str):
        ref_s = str(ref).strip().lower()
        for t in tools:
            if str(t.get("id", "")).lower() == ref_s:
                return t
        for t in tools:
            if str(t.get("name", "")).lower() == ref_s:
                return t
        return None

    # ── 主入口（由 HTTP 宿主按 spec.apis 路由调用） ──

    async def execute(self, ctx: AgentContext, **kwargs) -> AsyncGenerator[dict, None]:
        api_name = kwargs.get("api_name", "")
        req = kwargs.get("params", {}) or {}

        if api_name == "tools":
            yield {"event": "result", "data": {"response": await self._list_tools()}}
        elif api_name == "route":
            yield {"event": "result", "data": {"response": await self._route(req)}}
        else:
            yield {"event": "error", "data": {"message": f"未定义的 API: {api_name}"}}
            return
        yield {"event": "done", "data": {"messageId": ctx.session_id}}

    async def _list_tools(self) -> dict:
        tools = await self._active_tools()
        return {
            "status": "success",
            "count": len(tools),
            "tools": [self._tool_brief(t) for t in tools],
        }

    async def _route(self, req: dict) -> dict:
        message = str(req.get("message") or "").strip()
        tool_ref = str(req.get("tool") or "").strip()
        explicit_params = req.get("params") if isinstance(req.get("params"), dict) else None

        if not message and not tool_ref:
            return {"status": "bad_request",
                    "message": "缺少 tool（工具 id/名称）或 message（意图描述）"}

        tools = await self._active_tools()

        # 1) 显式指定工具
        if tool_ref:
            tool = self._find_explicit(tools, tool_ref)
            if tool is None:
                names = [str(t.get("id", "")) for t in tools]
                near = difflib.get_close_matches(tool_ref, names, n=5, cutoff=0.4)
                return {"status": "not_found",
                        "message": f"工具 '{tool_ref}' 不在工具空间中",
                        "candidates": near}
            params = dict(explicit_params or {})
            matched_by = "explicit"
            # 显式工具 + 意图消息 + 缺参数 → LLM 仅抽参（失败保持原样，由工具自身报缺失）
            if message and params == {} and self._param_defs(tool):
                extracted = await self._llm_extract(tool, message)
                if extracted is not None:
                    params = extracted
            return await self._finalize(tool, params, matched_by)

        # 2) 意图匹配：发现器初筛 → LLM 选定 → 关键词兜底
        from core.resource.discoverer.tool_discoverer import ToolDiscoverer
        candidates = await ToolDiscoverer().match_by_capability(message)
        candidates = [c for c in candidates if c.get("status") == "active"][: self.CANDIDATE_LIMIT]

        if candidates:
            choice = await self._llm_choose(message, candidates)
            if choice is not None:
                by_id = {str(t.get("id")): t for t in candidates}
                chosen = by_id.get(str(choice.get("tool_id")))
                if chosen is not None:
                    params = choice.get("params") if isinstance(choice.get("params"), dict) else {}
                    return await self._finalize(chosen, dict(params), "llm")
            top = candidates[0]
            params = {}
            extracted = await self._llm_extract(top, message)
            if extracted is not None:
                params = extracted
            return await self._finalize(top, params, "keyword")

        return {"status": "not_found",
                "message": f"未匹配到能处理该请求的工具：{message[:120]}",
                "candidates": [self._tool_brief(t) for t in tools[:20]]}

    async def _finalize(self, tool: dict, params: dict, matched_by: str) -> dict:
        result = await self.call_tool(tool.get("id"), params)
        if not isinstance(result, dict):
            result = {"data": result}
        status = result.get("status", "success")
        return {
            "status": status,
            "tool": {"id": tool.get("id"), "name": tool.get("name")},
            "matched_by": matched_by,
            "params": params,
            "result": result,
        }

    # ── LLM 协作（全部容错，失败返回 None 由调用方降级） ──

    async def _llm_choose(self, message: str, candidates: list[dict]):
        """让 LLM 在候选工具中选一个并抽取参数；返回 {tool_id, params} 或 None。"""
        briefs = []
        for t in candidates:
            briefs.append({
                "id": t.get("id"),
                "name": t.get("name"),
                "description": (t.get("description") or "")[:200],
                "params": self._param_defs(t),
            })
        prompt = (
            "你是工具路由器。从候选工具中选出最匹配用户请求的一个，并从请求中抽取该工具所需的参数。\n"
            f"用户请求：{message}\n"
            f"候选工具（JSON）：{json.dumps(briefs, ensure_ascii=False)}\n"
            '仅输出一个 JSON 对象：{"tool_id": "<候选中的 id>", "params": {…}}。'
            "无法确定时输出 {\"tool_id\": null}。不要输出其他文字。"
        )
        data = await self._ask_json(prompt)
        if isinstance(data, dict) and data.get("tool_id"):
            return data
        return None

    async def _llm_extract(self, tool: dict, message: str):
        """针对已选定工具，从消息中抽取参数；失败返回 None。"""
        prompt = (
            "从用户请求中抽取工具参数。只使用下面声明的参数，取不到的省略。\n"
            f"工具：{tool.get('name')}（{tool.get('id')}）\n"
            f"参数（JSON）：{json.dumps(self._param_defs(tool), ensure_ascii=False)}\n"
            f"用户请求：{message}\n"
            '仅输出一个 JSON 对象（参数名到值的映射），不要输出其他文字。'
        )
        data = await self._ask_json(prompt)
        if isinstance(data, dict):
            allowed = {p["name"] for p in self._param_defs(tool) if p.get("name")}
            return {k: v for k, v in data.items() if k in allowed}
        return None

    async def _ask_json(self, prompt: str):
        try:
            reply = await self._get_llm().chat(
                [{"role": "user", "content": prompt}], max_tokens=512, temperature=0)
        except Exception:
            return None
        if not reply:
            return None
        m = re.search(r"\{.*\}", reply, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
