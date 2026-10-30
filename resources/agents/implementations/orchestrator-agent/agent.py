"""内置任务编排智能体（todo 4.2 首版：跨 Agent 委派编排）

接收复杂任务 → 在 Agent 空间中选定协作伙伴 → 按声明式步骤（DAG 的线性
特例）把每一步委派给对应的运行中 Agent → 步骤间以 {stepN.xxx} 引用传递
数据 → 聚合全部步骤结果返回。

与 4.1 message-router-agent 的分工：
  - message-router-agent：单步、面向**工具空间**（Web 请求 → 一个工具）；
  - orchestrator-agent：多步、面向**Agent 空间**（任务 → 多个 Agent 协作），
    委派通道为 4.5 本地总线 `self._bus.call_agent`（子进程 → 主进程路由 →
    目标子进程，被调侧以 execute 语义聚合响应）。

步骤形态（plan.steps 数组，按声明顺序串行执行）：
  {"agent": "<agent_id>", "method": "<方法名>", "params": {...},
   "timeout": <秒，可选>, "required": <bool，默认 true>}
- params 支持 {stepN.a.b} 引用前序步骤输出、{param.x} 引用请求级参数
  （复用 BaseAgent.resolve_refs）。
- 某步 ok=false 且 required 时中止（status: "failed"，failed_step 标注）；
  required=false 的步骤失败记录后继续。
- 目标 Agent 未运行/超时/崩溃均得结构化错误（总线保证不抛异常）。
"""

from typing import AsyncGenerator
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from core.agent.base import BaseAgent, AgentContext, AgentRole


class OrchestratorAgent(BaseAgent):
    """任务编排智能体：声明式步骤 → 经总线委派给运行中 Agent → 聚合结果"""

    DEFAULT_TIMEOUT = 60
    MAX_TIMEOUT = 300
    expected_role = AgentRole.WEB_SERVICE

    async def resident_tool_ids(self) -> list[str]:
        # 编排不直接调工具，委派给子 Agent
        return []

    # ── 主入口（由 HTTP 宿主按 spec.apis 路由调用） ──

    async def execute(self, ctx: AgentContext, **kwargs) -> AsyncGenerator[dict, None]:
        api_name = kwargs.get("api_name", "")
        req = kwargs.get("params", {}) or {}

        if api_name == "agents":
            resp = await self._list_agents()
        elif api_name == "plan":
            resp = await self._run_plan(req)
        elif api_name == "call":
            resp = await self._run_call(req)
        else:
            yield {"event": "error", "data": {"message": f"未定义的 API: {api_name}"}}
            return
        yield {"event": "result", "data": {"response": resp}}
        yield {"event": "done", "data": {"messageId": ctx.session_id}}

    # ── Agent 空间清单 ──

    async def _list_agents(self) -> dict:
        # 注意：本 Agent 运行在独立子进程，看不到主进程 factory 的进程表，
        # 因此这里只给注册信息，不标注 running——目标是否在跑由委派时的
        # 总线结构化错误（「目标 Agent 未运行」）如实反馈。
        from core.resource.registry.agent_registry import AgentRegistry

        entries = await AgentRegistry().list_all()
        entries = [e for e in entries if e.get("status") == "active"]
        out = []
        for e in entries:
            aid = str(e.get("id", ""))
            out.append({
                "id": aid,
                "name": e.get("name") or aid,
                "role": e.get("role", ""),
                "description": (e.get("description") or "")[:200],
            })
        return {"status": "success", "count": len(out), "agents": out}

    # ── 单跳委派（调试/直通用） ──

    async def _run_call(self, req: dict) -> dict:
        target = str(req.get("agent") or "").strip()
        method = str(req.get("method") or "").strip()
        if not target or not method:
            return {"status": "bad_request",
                    "message": "缺少 agent（目标 Agent id）或 method（方法名）"}
        params = req.get("params") if isinstance(req.get("params"), dict) else {}
        timeout = self._clamp_timeout(req.get("timeout"))
        outcome = await self._dispatch(target, method, params, timeout)
        return {"status": "success" if outcome.get("ok") else "failed",
                "agent": target, "method": method, "step": outcome}

    # ── 多步编排 ──

    async def _run_plan(self, req: dict) -> dict:
        steps = req.get("steps")
        if not isinstance(steps, list) or not steps:
            return {"status": "bad_request",
                    "message": '缺少 steps 数组，形如 '
                               '[{"agent": "…", "method": "…", "params": {…}}]'}
        if len(steps) > 20:
            return {"status": "bad_request", "message": "steps 超过 20 步上限"}

        request_params = req.get("params") if isinstance(req.get("params"), dict) else {}
        scope: dict = {"param": dict(request_params)}
        results: list[dict] = []
        failed = None

        for idx, raw in enumerate(steps, start=1):
            if not isinstance(raw, dict):
                failed = {"step": idx, "error": f"第 {idx} 步不是对象"}
                break
            target = str(raw.get("agent") or "").strip()
            method = str(raw.get("method") or "").strip()
            required = raw.get("required", True) is not False
            if not target or not method:
                outcome = {"ok": False, "error": "缺少 agent 或 method",
                           "resolved_params": {}}
            else:
                params = BaseAgent.resolve_refs(
                    raw.get("params") if isinstance(raw.get("params"), dict) else {},
                    scope)
                timeout = self._clamp_timeout(raw.get("timeout"))
                outcome = await self._dispatch(target, method, params, timeout)

            entry = {
                "step": idx,
                "agent": target,
                "method": method,
                "required": required,
                "ok": bool(outcome.get("ok")),
                "result": outcome.get("result"),
                "error": outcome.get("error"),
            }
            results.append(entry)
            scope[f"step{idx}"] = outcome.get("result") if isinstance(
                outcome.get("result"), dict) else {"data": outcome.get("result")}

            if not outcome.get("ok"):
                if required:
                    failed = {"step": idx, "agent": target,
                              "error": outcome.get("error")}
                    break
                # required=false：记录失败继续

        status = "failed" if failed else "success"
        return {
            "status": status,
            "executed": len(results),
            "total": len(steps),
            "failed_step": failed,
            "results": results,
        }

    # ── 总线委派（本 Agent 唯一的副作用出口；结构化错误不抛出） ──

    async def _dispatch(self, target: str, method: str,
                        params: dict, timeout: float) -> dict:
        bus = getattr(self, "_bus", None)
        if bus is None:
            return {"ok": False, "error": "通信总线不可用（Agent 未经 entrypoint 启动）",
                    "resolved_params": params}
        try:
            outcome = await bus.call_agent(target, method, params, timeout=timeout)
        except Exception as e:  # 总线契约上不应抛；兜底保持编排不炸
            outcome = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if not isinstance(outcome, dict):
            outcome = {"ok": False, "error": f"总线返回非对象: {outcome!r}"}
        outcome.setdefault("ok", False)
        return outcome

    @staticmethod
    def _clamp_timeout(value) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = OrchestratorAgent.DEFAULT_TIMEOUT
        if v != v:  # NaN
            v = OrchestratorAgent.DEFAULT_TIMEOUT
        return max(1.0, min(v, OrchestratorAgent.MAX_TIMEOUT))
