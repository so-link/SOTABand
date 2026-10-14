"""InteractiveAgentBase — 交互式 Agent 共享基类

自定义交互 Agent 继承本类即可复用「系统默认交互 Agent」的核心能力，
无需重复实现工具匹配、参数引导、工具调用与会话状态管理。

设计要点（见 docs/agent-types-design.md）：
- **工具范围**：仅匹配 Agent 声明的工具集（来自 MD 规范的 【【工具名】】 引用，
  或子类覆写的 declared_tool_ids）。
- **匹配策略**：LLM 语义匹配（读工具 overview/tags）+ 关键词评分兜底。
- **参数引导**：状态机驱动，逐个引导缺失的必填参数，含类型纠正与校验。
- **工具调用**：统一走 BaseAgent.call_tool（ToolExecutor 子进程隔离执行）。
- **多轮会话**：按 session_id 维护对话历史与待办状态。

子类最少只需：

    class MyAgent(InteractiveAgentBase):
        pass

（工具列表、System Prompt 由基类根据 spec 自动构建）。
"""

from __future__ import annotations

import json
import re
from typing import AsyncGenerator

from core.agent.base import BaseAgent, AgentContext, AgentRole


DEFAULT_INTERACTIVE_SYSTEM_PROMPT = """你是「{agent_name}」，一个智能助手，通过对话理解用户需求并调用合适的工具帮助用户完成任务。

## 你的职责
1. 理解用户的自然语言意图
2. 在需要时调用工具完成任务
3. 引导用户提供必要的信息

## 响应格式
- 普通对话：用自然语言简洁回复
- 需要工具：说明将调用哪个工具
- 信息不足：向用户询问缺失的信息

## 注意事项
- 回复简洁清晰，逐步引导用户
- 保持友好、专业的语气"""


class InteractiveAgentBase(BaseAgent):
    """交互式 Agent 基类 — 对话 + 工具匹配 + 参数引导 + 调用。"""

    def __init__(self, spec=None, config: dict = None):
        super().__init__(spec, config)
        # 会话状态：{session_key: {"history": [...], "pending": {...}}}
        self._sessions: dict[str, dict] = {}
        self._llm = None

    # ══════════════════════════════════════════════════════════════
    #  LLM 客户端
    # ══════════════════════════════════════════════════════════════

    def get_llm(self):
        """懒加载统一 LLM 客户端（跟随系统配置）。"""
        if self._llm is None:
            from core.llm.client import create_llm_client

            self._llm = create_llm_client()
        return self._llm

    # ══════════════════════════════════════════════════════════════
    #  工具范围：Agent 声明的工具集
    # ══════════════════════════════════════════════════════════════

    def declared_tool_ids(self) -> list[str]:
        """返回本 Agent 可用的工具 id 列表。

        默认从 MD 规范（spec.raw_md）中的 【【工具名】】 引用解析，
        再映射为 registry 中的真实工具 id。子类可覆写以自定义范围。

        解析失败或无声明时返回空列表（表示不限定，使用全部可用工具）。
        """
        raw = getattr(self.spec, "raw_md", "") or ""
        names = re.findall(r"【【([^】]+)】】", raw)
        if not names:
            return []
        try:
            from core.resource.registry.tool_registry import ToolRegistry

            reg = ToolRegistry()
            all_tools = reg._read()
        except Exception:
            return []

        by_name = {t.get("name", ""): t.get("id", "") for t in all_tools}
        by_id = {t.get("id", ""): t.get("id", "") for t in all_tools}
        ids: list[str] = []
        for nm in names:
            nm = nm.strip()
            tid = by_name.get(nm) or by_id.get(nm)
            if tid and tid not in ids:
                ids.append(tid)
        return ids

    def _get_active_tools(self) -> list[dict]:
        """获取本 Agent 可用的工具（限定在声明的工具集内）。"""
        try:
            from core.resource.registry.tool_registry import ToolRegistry

            reg = ToolRegistry()
            tools = [t for t in reg._read() if t.get("status") == "active"]
        except Exception:
            return []

        declared = self.declared_tool_ids()
        if declared:
            ds = set(declared)
            tools = [t for t in tools if t.get("id") in ds]
        return tools

    # ══════════════════════════════════════════════════════════════
    #  工具匹配：LLM 语义 + 关键词兜底
    # ══════════════════════════════════════════════════════════════

    @staticmethod
    def _read_tool_overview(tool: dict) -> str:
        """读取工具的功能概述（用于匹配时的语义描述）。"""
        try:
            spec_path = tool.get("spec_path", "")
            if not spec_path:
                return ""
            from pathlib import Path

            root = Path(__file__).resolve().parent.parent.parent
            p = None
            try:
                from core.resource import paths as _paths
                _def_dirs = [r / "tools" / "definitions" for r in _paths.resource_roots()]
            except Exception:
                _def_dirs = [root / "resources" / "tools" / "definitions"]
            for _d in reversed(_def_dirs):
                for _name in (Path(spec_path).name, f"{tool.get('id')}.md"):
                    _cand = _d / _name
                    if _cand.exists():
                        p = _cand
                        break
                if p:
                    break
            if p and p.exists():
                text = p.read_text(encoding="utf-8")
                m = re.search(r"##\s*1\.?\s*功能概述\s*\n+(.+?)(?=\n##|\Z)", text, re.DOTALL)
                if m:
                    return m.group(1).strip().replace("\n", " ")[:200]
        except Exception:
            pass
        return ""

    async def match_tools(self, query: str) -> list[dict]:
        """匹配用户需求对应的工具，返回按匹配度排序的候选列表。"""
        active = self._get_active_tools()
        if not active:
            return []

        candidates = await self._llm_semantic_match(query, active)
        if candidates:
            return candidates

        return self._keyword_match(query, active)

    async def _llm_semantic_match(self, query: str, tools: list[dict]) -> list[dict]:
        """LLM 语义匹配：让模型从工具列表中选出最相关的（最多 3 个）。"""
        lines = []
        for t in tools:
            overview = self._read_tool_overview(t)
            info = f"- {t.get('id')}: {t.get('name', '')}"
            if overview:
                info += f" — {overview}"
            else:
                tags = t.get("tags", [])
                if tags:
                    info += f" [标签: {', '.join(tags)}]"
            lines.append(info)
        if not lines:
            return []

        prompt = (
            f"你是工具匹配器。根据用户需求，从下列工具中选出最相关的（最多3个，按匹配度从高到低）。\n\n"
            f"用户需求：\"{query}\"\n\n可用工具：\n" + "\n".join(lines) +
            "\n\n若无任何工具匹配，返回 {\"matched_tool_ids\": []}。"
            "\n只返回 JSON，格式：{\"matched_tool_ids\": [\"id1\", \"id2\"]}"
        )
        try:
            resp = await self.get_llm().chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=200,
            )
            text = (resp or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(text)
            ids = data.get("matched_tool_ids") or []
            by_id = {t.get("id"): t for t in tools}
            return [by_id[i] for i in ids if i in by_id]
        except Exception:
            return []

    def _keyword_match(self, query: str, tools: list[dict]) -> list[dict]:
        """关键词评分兜底匹配（参考系统交互 Agent 的评分策略）。"""
        q = query.lower()
        scored: list[tuple[int, dict]] = []
        for t in tools:
            score = 0
            tid = (t.get("id") or "").lower()
            tname = (t.get("name") or "").lower()
            tags = " ".join(t.get("tags", []) or []).lower()
            all_text = f"{tid} {tname} {tags}"
            if tid and tid in q:
                score += 10
            if tname and tname in q:
                score += 10
            # 中文名称的关键字命中
            cn_chars = [c for c in (t.get("name", "") + tags) if "\u4e00" <= c <= "\u9fff"]
            matched = sum(1 for c in set(cn_chars) if c in q)
            if matched >= 2:
                score += min(matched, 5)
            for tag in t.get("tags", []) or []:
                if tag.lower() in q:
                    score += 3
            for word in q.split():
                if len(word) >= 2 and word in all_text:
                    score += 1
            if score > 0:
                scored.append((score, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for s, t in scored if s >= 2][:3]

    # ══════════════════════════════════════════════════════════════
    #  参数：缺失检查 + 回答提取（含类型纠正与校验）
    # ══════════════════════════════════════════════════════════════

    async def get_missing_params(self, tool_id: str, current: dict) -> list[dict]:
        """返回该工具尚未提供的必填参数（排除 mode 分发参数）。"""
        try:
            from core.resource.registry.tool_registry import ToolRegistry

            entry = await ToolRegistry().get(tool_id)
            if not entry:
                return []
            missing = []
            for p in entry.get("param_meta", []) or []:
                if p.get("name") == "mode":
                    continue
                if p.get("required") and p.get("name") not in current:
                    missing.append(p)
            return missing
        except Exception:
            return []

    @staticmethod
    def coerce_number(text, want_float: bool = False):
        """从文本中尽力提取数值（类型纠正），失败返回 None。"""
        if text is None:
            return None
        s = str(text).strip().replace(",", "")
        try:
            return float(s) if want_float else int(s)
        except (ValueError, TypeError):
            pass
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        if not m:
            return None
        try:
            return float(m.group()) if want_float else int(float(m.group()))
        except (ValueError, TypeError):
            return None

    def extract_answer_param(self, answer: str, param: dict) -> dict:
        """把用户回答提取为指定参数的值（含类型纠正与校验）。

        校验失败抛 ValueError（调用方捕获后重新引导）。
        """
        val = (answer or "").strip()
        ptype = (param.get("type", "string") or "").lower()
        name = param.get("name", "")
        if "int" in ptype or "float" in ptype:
            num = self.coerce_number(val, want_float="float" in ptype)
            if num is None:
                want = "整数" if "int" in ptype else "数字"
                raise ValueError(f"参数 **{name}** 需要{want}，但收到「{val}」。请重新输入 {name} 的值。")
            return {name: num}
        return {name: val}

    # ══════════════════════════════════════════════════════════════
    #  会话状态
    # ══════════════════════════════════════════════════════════════

    def _session(self, ctx: AgentContext) -> dict:
        key = ctx.session_id or "default"
        st = self._sessions.get(key)
        if st is None:
            st = {"history": [], "pending": None}
            self._sessions[key] = st
        return st

    def build_system_prompt(self) -> str:
        """构建 System Prompt（含本 Agent 可用工具列表）。子类可覆写。"""
        base = DEFAULT_INTERACTIVE_SYSTEM_PROMPT.format(agent_name=self.spec.name or self.agent_id)
        tools = self._get_active_tools()
        if tools:
            lines = "\n".join(f"- {t['id']}: {t.get('name', '')}" for t in tools)
            base += f"\n\n## 当前可用工具（{len(tools)} 个）\n{lines}"
        return base

    # ══════════════════════════════════════════════════════════════
    #  工具执行
    # ══════════════════════════════════════════════════════════════

    async def run_tool(self, tool_id: str, params: dict) -> dict:
        """统一执行工具（经 ToolExecutor）。失败返回带 status 的 dict。"""
        try:
            return await self.call_tool(tool_id, params)
        except Exception as e:
            return {"status": "failed", "message": f"工具执行异常: {str(e)[:200]}"}

    # ══════════════════════════════════════════════════════════════
    #  主执行流程（状态机）
    # ══════════════════════════════════════════════════════════════

    async def execute(self, ctx: AgentContext, **kwargs) -> AsyncGenerator[dict, None]:
        """交互式主流程：会话历史 → 参数补全 → 工具匹配 → 调用 → 回复。"""
        content = (kwargs.get("content") or "").strip()
        if not content:
            yield {"event": "content", "data": {"text": "请输入您的问题。"}}
            yield {"event": "done", "data": {"messageId": ctx.session_id}}
            return

        st = self._session(ctx)

        # ① 正在收集参数 → 用户输入作为当前缺失参数的值
        pending = st.get("pending")
        if pending:
            missing = pending["missing"]
            target = missing[0]
            try:
                got = self.extract_answer_param(content, target)
            except ValueError as e:
                yield {"event": "content", "data": {"text": str(e)}}
                yield {"event": "done", "data": {"messageId": ctx.session_id}}
                return
            pending["params"].update(got)
            missing = await self.get_missing_params(pending["tool_id"], pending["params"])
            pending["missing"] = missing
            if missing:
                p = missing[0]
                yield {"event": "content",
                       "data": {"text": f"请提供 **{p['name']}**（{p.get('desc', '')}）的值。"}}
                yield {"event": "done", "data": {"messageId": ctx.session_id}}
                return
            # 参数齐全 → 执行
            st["pending"] = None
            async for ev in self._execute_and_reply(ctx, pending["tool"], pending["params"], st):
                yield ev
            return

        # ② 正常流程：LLM 对话（含工具匹配）
        # 先做工具匹配
        matched = await self.match_tools(content)
        if not matched:
            # 无匹配工具 → 纯对话回复
            async for ev in self._chat_reply(ctx, content, st):
                yield ev
            return

        tool = matched[0]
        tool_id = tool.get("id")

        # 从用户本次输入中尝试提取参数（仅填充能明确提取的）
        params = await self._llm_extract_params(content, tool_id)

        missing = await self.get_missing_params(tool_id, params)
        if missing:
            p = missing[0]
            st["pending"] = {"tool_id": tool_id, "tool": tool, "params": params, "missing": missing}
            yield {"event": "content",
                   "data": {"text": f"要使用 **{tool.get('name', tool_id)}**，请提供 **{p['name']}**（{p.get('desc', '')}）的值。"}}
            yield {"event": "done", "data": {"messageId": ctx.session_id}}
            return

        async for ev in self._execute_and_reply(ctx, tool, params, st):
            yield ev

    async def _llm_extract_params(self, query: str, tool_id: str) -> dict:
        """用 LLM 从用户输入中尽力提取该工具的参数（缺省的不填）。"""
        try:
            from core.resource.registry.tool_registry import ToolRegistry

            entry = await ToolRegistry().get(tool_id)
            if not entry:
                return {}
            param_defs = [
                {"name": p.get("name"), "type": p.get("type"), "desc": p.get("desc", "")}
                for p in entry.get("param_meta", []) or []
                if p.get("name") != "mode"
            ]
            if not param_defs:
                return {}
            prompt = (
                "从用户输入中提取工具参数，只返回能从输入中明确得到的参数。\n"
                f"参数定义: {json.dumps(param_defs, ensure_ascii=False)}\n"
                f"用户输入: \"{query}\"\n"
                "只返回 JSON 对象（无则返回 {}）："
            )
            resp = await self.get_llm().chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=300,
            )
            text = (resp or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(text)
            # 按类型纠正
            out = {}
            for p in param_defs:
                nm = p["name"]
                if nm in data and data[nm] not in (None, ""):
                    ptype = (p.get("type") or "").lower()
                    if "int" in ptype or "float" in ptype:
                        num = self.coerce_number(data[nm], want_float="float" in ptype)
                        if num is not None:
                            out[nm] = num
                    else:
                        out[nm] = data[nm]
            return out
        except Exception:
            return {}

    async def _execute_and_reply(self, ctx: AgentContext, tool: dict, params: dict, st: dict):
        """执行工具并用 LLM 汇总结果回复。"""
        tool_id = tool.get("id")
        # 执行提示：工具可能耗时较长，先反馈，避免界面无响应被误认为卡死。
        yield {"event": "content",
               "data": {"text": f"正在执行 **{tool.get('name', tool_id)}**，请稍候……"}}
        result = await self.run_tool(tool_id, params)

        if not isinstance(result, dict) or result.get("status") != "success":
            msg = result.get("message") if isinstance(result, dict) else "未知错误"
            yield {"event": "error",
                   "data": {"code": "TOOL_FAILED", "message": f"执行失败：{msg}"}}
            yield {"event": "done", "data": {"messageId": ctx.session_id}}
            return

        # 结果卡片：承载完整的工具返回数据（前端据此完整渲染表格/图片/文件等）
        yield {
            "event": "card",
            "data": {
                "type": "result-summary",
                "title": f"工具执行: {tool.get('name', tool_id)}",
                "summary": result.get("message", ""),
                "data": {"tool_id": tool_id, "result": result},
            },
        }

        # 与系统交互 Agent 保持一致：表格 / 图片 / 文件 类结果直接由前端卡片
        # 完整渲染，不再经 LLM 生成文本——否则 LLM 会截断/摘要，导致信息不完整，
        # 且与卡片内容重复展示。仅文本类结果才调 LLM 生成自然语言回复。
        if result.get("output_format") in ("image", "table", "file"):
            yield {"event": "done", "data": {"messageId": ctx.session_id}}
            return

        # 文本类结果 → LLM 生成自然语言回复
        try:
            reply = await self.get_llm().chat(
                messages=[{"role": "user", "content": (
                    f"用户需求已通过工具「{tool.get('name', tool_id)}」完成。"
                    f"请用简洁自然的中文向用户说明结果。\n"
                    f"工具返回：{json.dumps(result, ensure_ascii=False, default=str)[:4000]}"
                )}],
                max_tokens=2000,
            )
            yield {"event": "content", "data": {"text": reply or result.get("message", "")}}
        except Exception:
            yield {"event": "content", "data": {"text": result.get("message", "")}}

        yield {"event": "done", "data": {"messageId": ctx.session_id}}

    async def _chat_reply(self, ctx: AgentContext, content: str, st: dict):
        """无匹配工具时的纯对话回复（流式）。"""
        history = st.setdefault("history", [])
        if not history:
            history.append({"role": "system", "content": self.build_system_prompt()})
        history.append({"role": "user", "content": content})
        try:
            resp = await self.get_llm().chat(messages=history, max_tokens=800)
        except Exception as e:
            resp = f"抱歉，处理您的请求时出错：{str(e)[:120]}"
        history.append({"role": "assistant", "content": resp or ""})
        # 保持历史不无限增长
        if len(history) > 21:
            del history[1:3]
        yield {"event": "content", "data": {"text": resp or ""}}
        yield {"event": "done", "data": {"messageId": ctx.session_id}}
