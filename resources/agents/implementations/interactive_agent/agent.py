"""交互 Agent — 系统默认入口，开机自启，处理用户对话"""

import json
import re
import time
import asyncio
from typing import AsyncGenerator
import os
import subprocess
import sys
import tempfile
import uuid

from core.agent.base import BaseAgent, AgentContext, AgentSpec, AgentRole
from core.llm.client import create_llm_client, LLMClient

try:  # 工作空间工具确认 TTL 与网关确认协议（core/chat_control/confirm.py）对齐
    from core.chat_control.confirm import CONFIRM_TTL_SECONDS as _CONFIRM_TTL_SECONDS
except Exception:  # pragma: no cover - 导入异常时退回同源常量
    _CONFIRM_TTL_SECONDS = 900


SYSTEM_PROMPT = """你是 SOTABand Engine 的交互 Agent，一个多智能体多模态智能处理引擎的对话入口。

## 你的职责
1. 理解用户的自然语言意图
2. 分析用户的需求类型（简单数据处理 / 复杂多步任务编排）
3. 引导用户完成任务

## 当前系统能力
- 工具空间: 用户可以创建和使用各种数据处理工具
- Agent 空间: 数据加载、异常检测、编排等专业 Agent。当用户明确指定某个 Agent（如「用 XX Agent 帮我…」）时，系统会自动把请求转交给该 Agent 执行，你会收到它的执行结果
- 数据空间: 支持 EEG (EDF)、图像 (PNG/TIFF)、表格 (CSV)、文本等多模态数据
- 模型空间: 支持 LLM、ViT、3D-CNN、时序模型
- 探索式能力增长: 当没有匹配工具时，可自动生成代码，经用户核验后注册为本地工具

## 响应格式
- 普通对话: 用自然语言回复
- 简单数据处理需求: 分析需求 → 匹配工具 → 建议执行方案
- 复杂任务: 分析步骤 → 建议使用编排模式

## 平台工具（重要）
- 你可以直接调用平台工具查询系统真实状态（数据集/工具/模型/Agent 清单、定时任务、目录浏览、数据预览等）。需要这类信息时**必须调用工具**，禁止凭空猜测或编造
- 用户要求「创建 / 新建 / 做一个 / 开发一个」工具或 Agent 时，**必须调用 create_tool / create_agent 平台工具**完成创建（先给确认卡，用户确认后自动生成、审查并注册）。
  禁止让用户自己去别的界面手工创建，禁止声称已创建
- 用户要求「创建」时，**禁止先用 run_tool 跑一个同名/相似的老工具来"看看它是否满足要求"**。用户要的是新建，先调研再跑老工具会打断意图、多一张无意义的确认卡。
  只有用户说的是「执行 / 跑一下 / 用已有的」时才去匹配并执行老工具；创建完成后要验证，用新工具 id 跑一次
- 创建 Agent 前先用 list_tools 确认要引用的工具名称，描述里用【【工具名】】引用；创建后可直接 start_agent 启动
- Agent 创建回执里若带 warning（自检未通过），必须如实告诉用户哪里没跑通、准备怎么改，禁止说「已创建并可用」
- 创建工具后可立刻 run_tool 用真实参数执行验证，并把关键结果告诉用户
- 涉及修改平台资源的工具会先向用户发出确认卡片，工具返回 status=pending 时，你只需告知用户在卡片上确认，**绝不能声称操作已执行**
- 工具返回的 status=error 表示未执行，把错误原因用自然语言告诉用户或修正参数后重试

## 注意事项
- 回复简洁清晰，逐步引导用户：结论先行，不罗列内部步骤、工具 id、JSON、日志
- 指代工具 / Agent 一律用中文名称，不要报内部 id（如 etf-90d-ranking-review-agent）
- 不要用「# 标题」「## 小节」开头（对话气泡里标题层级喧宾夺主），直接给结论与要点
- 数据缺失时说「未获取到 X」，不要用「一切正常 / 无异常 / 无预警」这类未校验的肯定表述
- 如用户提及具体数据文件，引用文件名
- 保持友好、专业的语气"""


class InteractiveAgent(BaseAgent):
    """交互 Agent — 开机自启，处理所有用户对话输入"""

    def __init__(self, llm_client: LLMClient = None):
        spec = AgentSpec(
            id="interactive-agent",
            name="交互Agent",
            version="1.0.0",
            role=AgentRole.INTERACTIVE,
            description="SOTABand Engine 主交互入口，处理用户对话",
            inputs={
                "content": {"type": "string", "required": True},
                "attachments": {"type": "list", "required": False},
                "session_id": {"type": "string", "required": True},
                "user_id": {"type": "string", "required": True},
            },
            outputs={
                "content": "文本增量（流式）",
                "card": "内联卡片",
                "done": "响应结束",
                "error": "错误信息",
            },
            required_tools=["tool-llm-client"],
            optional_tools=[
                "tool-resource-discoverer",
                "tool-code-builder",
                "tool-orchestrator",
            ],
            config={
                "max_history": 20,
                "temperature": 0.7,
                "max_tokens": 100000,
            },
        )
        super().__init__(spec, config=spec.config)
        self.llm = llm_client or create_llm_client()
        self._sessions: dict[str, list[dict]] = {}
        self._pending_calls: dict[str, dict] = {}
        self._pending_confirm: dict[str, dict] = {}
        self._pending_modes: dict[str, dict] = {}
        self._pending_agents: dict[str, dict] = {}
        # 待续跑的 Agent 请求：{session_key: {"agent_id","request"}}，
        # 用户确认 start_agent 后在回执轮继续派发（不回“已启动”就结束）。
        self._pending_agent_request: dict[str, dict] = {}

    async def execute(
        self, ctx: AgentContext, **kwargs
    ) -> AsyncGenerator[dict, None]:
        """处理用户输入，调用系统统一 LLM（跟随全局 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL），流式返回"""
        content = kwargs.get("content", "")
        attachments = kwargs.get("attachments", [])
        workspace_tool_ids = kwargs.get("workspace_tool_ids", [])

        # 隐藏轮次：用户在确认卡片上点「确认执行」后，前端把网关回执回传。
        # 助手必须用自然语言把执行结果解读进对话（结果会进入会话历史，
        # 供用户后续针对结果继续讨论），而不是让 JSON 停在卡片里。
        if kwargs.get("kind") == "platform_receipt":
            try:
                async for event in self._explain_platform_receipt(
                    ctx, kwargs.get("receipt") or {},
                    ctx.session_id or "default"):
                    yield event
            except Exception as e:
                yield {"event": "error",
                       "data": {"code": "fatal_error", "message": str(e)}}
            finally:
                yield {"event": "done",
                       "data": {"messageId": f"msg-{int(time.time() * 1000)}"}}
            return

        if not content.strip():
            yield {"event": "done", "data": {"messageId": ctx.session_id}}
            return

        try:
            async for event in self._execute_impl(ctx, content, attachments, workspace_tool_ids):
                yield event
        except Exception as e:
            yield {
                "event": "error",
                "data": {"code": "fatal_error", "message": str(e)},
            }
        finally:
            yield {
                "event": "done",
                "data": {"messageId": f"msg-{int(time.time() * 1000)}"},
            }

    async def _execute_impl(
        self, ctx: AgentContext, content: str, attachments: list, workspace_tool_ids: list[str] = None
    ) -> AsyncGenerator[dict, None]:

        # 解析附件中的数据集路径
        dataset_paths = self._resolve_dataset_paths(attachments)

        session_key = ctx.session_id or "default"

        # 前置状态机：模式选择 / 工具确认 / 参数收集 三者互斥，同一时刻最多一个生效
        selected_mode_id = None
        matched_tool = None
        skip_param_extract = {}
        # 工具是否来自「多候选确认」流程：此时用户回复是选工具而非选模式，
        # 不能拿它去推断模式（见 ② 分支与模式提示处的处理）
        tool_from_confirm = False
        # 本轮是否已通过统一执行确认门（② 分支解析执行确认时置真）
        exec_confirmed = False
        # 本轮执行对应的审计 op_id（确认门签发，贯穿 pending→confirmed→executed）
        exec_op_id = ""

        # 进入新的用户轮次：先丢弃超时的工作空间工具确认（TTL 与网关 15
        # 分钟对齐）。旧 pending 不过期的话，其缓存的 LLM 参数可能被很久
        # 之后一条恰好模糊匹配上的消息触发执行。
        await self._purge_expired_confirm(session_key, ctx.user_id or "default")

        # ⓪ Agent 待确认 / 待重试：上一轮匹配到多个 Agent 让用户选，
        #    或锁定了未运行的 Agent。本轮短回复视为对该请求的回应/补充。
        pending_agent = self._pending_agents.get(session_key)
        if pending_agent:
            self._pending_agents.pop(session_key, None)
            candidates = pending_agent.get("candidates")
            if candidates:
                selected = self._resolve_agent_selection(content, candidates)
                if selected is None:
                    self._pending_agents[session_key] = pending_agent
                    yield {"event": "content", "data": {"text": "请回复编号或 Agent 名称来选择要使用的 Agent。"}}
                    return
                # 用户回复仅用于选 Agent，原请求文本才是给目标 Agent 的输入
                async for ev in self._dispatch_to_agent(selected, pending_agent["request"], session_key,
                                                        ctx.user_id or ""):
                    yield ev
                return
            if len(content) <= 60:
                # 短回复（「继续」/用户补充说明）合并到原请求上重投；
                # 但若这条回复本身就是原请求（例如前端在启动确认成功后
                # 自动重发原话），不能拼成「问题，问题」重复一遍。
                if content.strip() == str(pending_agent["request"]).strip():
                    merged = content
                else:
                    merged = (
                        content if self._resolve_agent_selection(content, [pending_agent["agent"]])
                        else f"{pending_agent['request']}，{content}"
                    )
                async for ev in self._dispatch_to_agent(pending_agent["agent"], merged, session_key,
                                                        ctx.user_id or ""):
                    yield ev
                return

        # ① 正在等待用户选择模式
        pending_mode = self._pending_modes.get(session_key)
        if pending_mode:
            selected_mode = self._resolve_mode_selection(content, pending_mode["modes"])
            if selected_mode is None:
                self._pending_modes[session_key] = pending_mode
                yield {"event": "content", "data": {"text": "请回复编号或模式名称来选择要使用的模式。"}}
                return
            self._pending_modes.pop(session_key, None)
            matched_tool = {"id": pending_mode["tool_id"], "name": pending_mode["tool_name"]}
            selected_mode_id = selected_mode["id"]
            selected_mode_name = selected_mode["name"] or selected_mode_id
            params = await self._extract_params_for_mode(
                content, matched_tool["id"], selected_mode_id, dataset_paths
            )
            missing = await self._check_missing_params(matched_tool["id"], params, selected_mode_id)
            if missing:
                self._pending_calls[session_key] = {
                    "tool_id": matched_tool["id"],
                    "tool_name": matched_tool["name"],
                    "mode": selected_mode_id,
                    "mode_name": selected_mode_name,
                    "params": params,
                    "missing": missing,
                }
                p = missing[0]
                yield {"event": "content", "data": {"text": f"要使用 **{matched_tool['name']} · {selected_mode_name}**，请提供以下参数:\n\n**{p['name']}** — {p.get('desc', '')}"}}
                return
            # 参数已齐 → 直接进入执行（selected_mode_id 已设置，matched_tool 已设置）
            skip_param_extract = params

        # ② 正在等待用户确认工具选择
        elif self._pending_confirm.get(session_key):
            pending_confirm = self._pending_confirm[session_key]
            if pending_confirm.get("kind") == "workspace-exec":
                # 统一执行确认门：带缓存参数的执行确认只接受**显式**的确认/取消
                # 回复（auto 模式由前端自动发送同格式的确认消息，走同一扇门）。
                # 不允许模糊命中——否则任何一条恰好含候选名字片段的消息都会
                # 拿旧 pending 里的 LLM 参数直接执行。
                candidate = (pending_confirm.get("candidates") or [{}])[0]
                verdict = self._resolve_exec_confirmation(content)
                owner = ctx.user_id or "default"
                cached_args = pending_confirm.get("arguments") or {}
                if verdict == "confirm":
                    self._pending_confirm.pop(session_key, None)
                    exec_op_id = pending_confirm.get("op_id", "")
                    await self._audit_workspace_op(
                        op_id=exec_op_id, session_key=session_key, owner=owner,
                        tool=candidate.get("name", candidate.get("id", "")),
                        args=cached_args, status="confirmed")
                    matched_tool = {"id": candidate.get("id", ""),
                                    "name": candidate.get("name", candidate.get("id", ""))}
                    # LLM/预匹配阶段已给全参数，确认后直接作为 pre_params 执行
                    skip_param_extract = cached_args
                    # 确认时锁定的模式一并恢复，避免下一轮重新追问模式
                    selected_mode_id = pending_confirm.get("mode")
                    exec_confirmed = True
                    # 本次回复用于确认执行，不可再用作模式提示依据
                    tool_from_confirm = True
                elif verdict == "cancel":
                    self._pending_confirm.pop(session_key, None)
                    await self._audit_workspace_op(
                        op_id=pending_confirm.get("op_id", ""), session_key=session_key,
                        owner=owner,
                        tool=candidate.get("name", candidate.get("id", "")),
                        args=cached_args, status="cancelled")
                    yield {"event": "content", "data": {"text": "已取消本次工具执行。还需要我做什么？"}}
                    return
                else:
                    # 保持 pending（TTL 继续倒计时），等待明确的确认/取消
                    yield {"event": "content", "data": {"text": "请在确认卡上回复「确认」执行，或「取消」放弃。"}}
                    return
            else:
                # 多候选工具选择（无缓存参数，选完仍会走统一确认门后才执行）
                self._pending_confirm.pop(session_key, None)
                selected = self._resolve_tool_selection(content, pending_confirm["candidates"])
                if selected is None:
                    self._pending_confirm[session_key] = pending_confirm
                    yield {"event": "content", "data": {"text": "请回复编号或工具名称来选择要使用的工具。"}}
                    return
                matched_tool = selected
                # 本次回复用于「选择工具」，不可再用作模式提示依据，
                # 否则用户回复的编号（如 "1"）会被误判为模式编号，导致跳过模式选择。
                tool_from_confirm = True
                # 参数提取必须用**原始用户输入**而非当前这条选择回复（例如
                # "预测 ETF 510500 的价格" 选完工具后当前 content 变成
                # "我选择第2个工具：..."，从后者当然提不出 510500）。
                original_query = pending_confirm.get("original_query")
                if original_query:
                    content = original_query

        # ③ 正在收集参数
        elif self._pending_calls.get(session_key):
            pending = self._pending_calls[session_key]
            if content.strip() in ("取消", "取消执行", "算了", "cancel"):
                self._pending_calls.pop(session_key, None)
                yield {"event": "content", "data": {"text": f"已取消 **{pending['tool_name']}** 的执行。还需要我做什么？"}}
                return
            target_param = pending["missing"][0]
            try:
                answer_params = await self._extract_answer_params(
                    content, pending["tool_id"], target_param
                )
            except ValueError as e:
                # 类型校验失败（如 int 参数收到无法解析的文本）：
                # 不写入参数、保持 pending 状态，提示用户重新输入该参数。
                yield {"event": "content", "data": {"text": str(e)}}
                return
            pending["params"].update(answer_params)
            pending["missing"] = await self._check_missing_params(
                pending["tool_id"], pending["params"], pending.get("mode")
            )
            if pending["missing"]:
                p = pending["missing"][0]
                yield {"event": "content", "data": {"text": f"请提供 **{p['name']}** ({p.get('desc', '')}) 的值。"}}
                return
            self._pending_calls.pop(session_key, None)
            matched_tool = {"id": pending["tool_id"], "name": pending["tool_name"]}
            selected_mode_id = pending.get("mode")
            skip_param_extract = pending["params"]

        # ④ 无前置状态 → 先尝试「指定 Agent」派发，再回退到工具匹配
        need_create_tool_desc = None
        matched_tools: list[dict] = []
        repo_tools: list[dict] = []
        if not matched_tool:
            # Agent 优先匹配：先发 step，让用户看到「正在匹配 Agent」阶段
            yield {"event": "step", "data": {"id": "match_agent", "text": "正在匹配 Agent", "status": "running"}}
            agent_hit = await self._match_agent(content)
            if agent_hit:
                candidates = agent_hit if isinstance(agent_hit, list) else [agent_hit]
                if len(candidates) > 1:
                    # 多个 Agent 名称都可能匹配 → 请用户确认，不擅自选一个
                    infos = [{"id": a.get("id", ""), "name": a.get("name", ""), "index": i}
                             for i, a in enumerate(candidates, 1)]
                    yield {"event": "step", "data": {"id": "match_agent", "text": "找到多个候选 Agent，等待选择", "status": "done"}}
                    yield {
                        "event": "card",
                        "data": {
                            "type": "tool-confirm",
                            "title": "请确认要使用的 Agent",
                            "summary": f"找到 {len(candidates)} 个可能匹配的 Agent，请选择其中一个",
                            "data": {"candidates": infos},
                        },
                    }
                    self._pending_agents[session_key] = {"candidates": candidates, "request": content}
                    return
                a_name = candidates[0].get("name", candidates[0].get("id", ""))
                yield {"event": "step", "data": {"id": "match_agent", "text": f"匹配到 Agent「{a_name}」", "status": "done"}}
                async for ev in self._dispatch_to_agent(candidates[0], content, session_key,
                                                        ctx.user_id or ""):
                    yield ev
                return
            # 未匹配到 Agent → 继续工具匹配
            yield {"event": "step", "data": {"id": "match_agent", "text": "未匹配到 Agent，继续匹配工具", "status": "done"}}
            yield {"event": "step", "data": {"id": "match_tool", "text": "正在匹配工具", "status": "running"}}
            matched_tools, need_create_tool_desc, repo_tools = await self._pre_match_tool(content, workspace_tool_ids or [])

        history = self._sessions.get(session_key, [])
        if not history:
            system_content = self._build_system_with_context(workspace_tool_ids or [])
            history = [{"role": "system", "content": system_content}]

        # 如果预匹配到工具 → 先执行工具，用真实结果驱动 LLM 回复
        tool_result = None
        tool_failed = False

        # 多个候选工具 → 请用户确认选择哪一个
        if not matched_tool and len(matched_tools) > 1:
            self._pending_confirm[session_key] = {"candidates": matched_tools, "original_query": content}
            # 构建候选列表展示
            candidates_info = []
            for i, t in enumerate(matched_tools, 1):
                name = t.get("name", t.get("id", ""))
                candidates_info.append({"id": t.get("id", ""), "name": name, "index": i})
            # 发确认卡片（卡片本身已提供候选列表与选择交互，不再额外发重复文本引导）
            yield {
                "event": "card",
                "data": {
                    "type": "tool-confirm",
                    "title": "请确认要使用的工具",
                    "summary": f"找到 {len(matched_tools)} 个可能匹配的工具，请选择其中一个",
                    "data": {"candidates": candidates_info},
                },
            }
            yield {"event": "step", "data": {"id": "match_tool", "text": "找到多个候选工具，等待选择", "status": "done"}}
            return

        # 唯一候选 → 直接使用
        if not matched_tool and len(matched_tools) == 1:
            matched_tool = matched_tools[0]

        # 工具空间没有，但工具仓库里有现成的 → 提示一键加入工具空间后继续。
        # 不直接执行：工具空间是用户圈定的能力边界，跨边界执行要有明确授权。
        if not matched_tool and repo_tools:
            infos = [{
                "id": t.get("id", ""), "name": t.get("name", ""),
                "tags": t.get("tags", []) or [], "index": i,
            } for i, t in enumerate(repo_tools, 1)]
            yield {
                "event": "card",
                "data": {
                    "type": "add-workspace-tool",
                    "title": "工具仓库里有现成的工具",
                    "summary": f"当前工具空间没有匹配的工具，但工具仓库里找到 "
                               f"{len(repo_tools)} 个可用工具，加入工具空间后即可使用",
                    "data": {"candidates": infos, "request": content},
                },
            }
            yield {"event": "step", "data": {"id": "match_tool", "text": "未匹配到工具，已推荐仓库工具", "status": "done"}}
            return

        # 需要创建新工具 → 直接发卡片，提供跳转入口
        if not matched_tool and need_create_tool_desc:
            yield {
                "event": "card",
                "data": {
                    "type": "create-tool",
                    "title": "需要创建新工具",
                    "summary": need_create_tool_desc,
                    "data": {"description": need_create_tool_desc},
                },
            }
            yield {"event": "step", "data": {"id": "match_tool", "text": "未匹配到工具，需要新建", "status": "done"}}
            return

        # 兜底：所有工具匹配分支都未命中（matched_tools / repo_tools / need_create_tool_desc 全空）
        # 必须发 step done，否则前端「正在匹配工具」永远停在 running 状态。
        if not matched_tool:
            yield {"event": "step", "data": {"id": "match_tool", "text": "未匹配到工具，进入自由对话", "status": "done"}}

        if matched_tool:
            _tool_name = matched_tool.get("name", matched_tool.get("id", ""))
            yield {"event": "step", "data": {"id": "match_tool", "text": f"匹配到工具「{_tool_name}」", "status": "done"}}
            # 多模式工具且尚未选定模式 → 先引导用户选择模式
            if selected_mode_id is None:
                modes = self._get_tool_modes(matched_tool["id"])
                if modes:
                    # 检查用户当前消息是否已包含模式意图（如「训练」「测试模式」）
                    # 若工具来自多候选确认流程，则本次回复是选工具用的，
                    # 不能用来推断模式（否则编号会被误判为模式），一律走模式提示。
                    hint_mode = None if tool_from_confirm else self._resolve_mode_selection(content, modes)
                    if hint_mode is None:
                        self._pending_modes[session_key] = {
                            "tool_id": matched_tool["id"],
                            "tool_name": matched_tool["name"],
                            "modes": modes,
                        }
                        # 与工具选择一致：发可点击的模式选择卡片 + 文本引导，
                        # 用户既可在输入框回复编号/名称，也可直接点击卡片选择。
                        modes_info = [
                            {
                                "id": m.get("id", ""),
                                "name": m.get("name", ""),
                                "desc": m.get("desc", "") or "",
                                "index": i,
                            }
                            for i, m in enumerate(modes, 1)
                        ]
                        yield {
                            "event": "card",
                            "data": {
                                "type": "mode-selection",
                                "title": f"「{matched_tool['name']}」支持以下模式",
                                "summary": "请选择要使用的模式，可点击选择或回复编号",
                                "data": {"modes": modes_info, "tool_name": matched_tool["name"]},
                            },
                        }
                        options = "、".join(
                            f"{i}.{m['name']}" + (f"（{m['desc']}）" if m.get('desc') else "")
                            for i, m in enumerate(modes, 1)
                        )
                        yield {"event": "content", "data": {"text": f"请回复编号或模式名称选择，也可直接点击上方卡片：{options}"}}
                        return
                    selected_mode_id = hint_mode["id"]

            # 参数（pending 已收集或重新提取）
            yield {"event": "step", "data": {"id": "extract_params", "text": "正在分析参数", "status": "running"}}
            if skip_param_extract:
                params = skip_param_extract
            else:
                params = await self._extract_params_for_mode(
                    content, matched_tool["id"], selected_mode_id, dataset_paths
                )
            missing = await self._check_missing_params(matched_tool["id"], params, selected_mode_id)
            if not missing:
                yield {"event": "step", "data": {"id": "extract_params", "text": "参数分析完成", "status": "done"}}
            if missing:
                # 参数不足 → 逐一引导，存储 pending 状态
                self._pending_calls[session_key] = {
                    "tool_id": matched_tool["id"],
                    "tool_name": matched_tool["name"],
                    "mode": selected_mode_id,
                    "params": params,
                    "missing": missing,
                }
                p = missing[0]
                yield {"event": "content", "data": {"text": f"要使用 **{matched_tool['name']}** 工具，请提供以下参数:\n\n**{p['name']}** — {p.get('desc', '')}"}}
                yield {"event": "step", "data": {"id": "extract_params", "text": "参数不足，等待补充", "status": "error"}}
                return
            # 记录执行前的数据集 ID 列表，用于检测新注册的数据集
            datasets_before = set()
            try:
                from core.resource.registry.data_registry import DataRegistry
                dr = DataRegistry()
                datasets_before = {d.get("id") for d in dr._read()}
            except Exception:
                pass

            # 多模式：把选定的 mode 合并进参数
            if selected_mode_id:
                params = {**params, "mode": selected_mode_id}

            # 统一执行确认门：所有工作空间工具执行路径（预匹配直执行、
            # 候选选择后、参数收集后、LLM function calling）都必须先经
            # 用户确认才真正执行；auto 模式由前端对本卡自动回发确认消息，
            # 走同一扇门（确认语义见 ② 分支）。
            owner = ctx.user_id or "default"
            if not exec_confirmed:
                entry = await self._create_workspace_exec_pending(
                    session_key, owner, matched_tool, params, selected_mode_id)
                yield self._workspace_exec_card(entry)
                yield {"event": "step", "data": {"id": "call_tool", "text": f"等待确认执行「{_tool_name}」", "status": "done"}}
                return
            if not exec_op_id:
                # ② 确认轮未携带 op_id（旧格式 pending）→ 补发审计 op，保证执行有流水
                exec_op_id = self._new_workspace_op_id()
                await self._audit_workspace_op(op_id=exec_op_id, session_key=session_key,
                                               owner=owner, tool=_tool_name,
                                               args=params, status="pending")
                await self._audit_workspace_op(op_id=exec_op_id, session_key=session_key,
                                               owner=owner, tool=_tool_name,
                                               args=params, status="confirmed")

            # 执行步骤提示：工具可能耗时较长，用 step 事件告知用户正在执行
            yield {"event": "step", "data": {"id": "call_tool", "text": f"正在执行工具「{_tool_name}」", "status": "running"}}
            _exec_started = time.time()
            tool_result = await self._exec_via_gateway(
                matched_tool["id"], params, ctx, session_key, confirmed=exec_confirmed)
            _exec_ms = int((time.time() - _exec_started) * 1000)
            await self._audit_workspace_op(
                op_id=exec_op_id, session_key=session_key, owner=owner,
                tool=_tool_name, args=params,
                status="executed" if (tool_result or {}).get("status") == "success" else "failed",
                result=tool_result,
                error="" if (tool_result or {}).get("status") == "success"
                     else str((tool_result or {}).get("message", "execution_failed"))[:500],
                duration_ms=_exec_ms)
            # 注意：不能用 `if tool_result:` 判断——工具可能返回空 dict {}（未遵循
            # 返回规范或无数据），空 dict 在 Python 中是 falsy，会被误判为"执行失败"
            # 而丢弃真实返回。只要 _execute_tool 没返回 None，就当作有结果返回。
            if tool_result is not None:
                # 结果只出一张卡（result-summary）：它已内联渲染结果数据 / 图片 /
                # 表格 / 文件并带成功·失败徽标，不再叠加 platform-result，避免同一次
                # 执行出现两张内容重复的结果卡。结果卡先于 LLM 解析下发。
                _ok = tool_result.get("status") == "success"
                _name = matched_tool.get("name", matched_tool.get("id", ""))
                yield {"event": "step", "data": {"id": "call_tool", "text": f"工具「{_name}」执行{'完成' if _ok else '失败'}", "status": "done" if _ok else "error"}}
                # 检测工具执行过程中是否注册了新数据集
                registered_dataset_id = None
                try:
                    from core.resource.registry.data_registry import DataRegistry
                    dr = DataRegistry()
                    datasets_after = {d.get("id") for d in dr._read()}
                    new_ids = datasets_after - datasets_before
                    if new_ids:
                        registered_dataset_id = list(new_ids)[0]
                except Exception:
                    pass
                # 如果对比没发现，也尝试从返回结果中提取
                if not registered_dataset_id:
                    tool_data = tool_result.get("data", {})
                    if isinstance(tool_data, dict):
                        registered_dataset_id = tool_data.get("dataset_id") or tool_data.get("registered_dataset_id")
                if not registered_dataset_id:
                    registered_dataset_id = tool_result.get("dataset_id") or tool_result.get("registered_dataset_id")

                # title 与 summary 按成功/失败区分，避免失败时仍显示"工具执行"
                _result_title = f"工具执行: {matched_tool['name']}" if _ok else f"工具执行失败: {matched_tool['name']}"
                _result_summary = tool_result.get("message") or (
                    "工具未返回有效结果" if not tool_result
                    else json.dumps(tool_result, ensure_ascii=False, default=str)[:200]
                )
                yield {
                    "event": "card",
                    "data": {
                        "type": "result-summary",
                        "title": _result_title,
                        "summary": _result_summary,
                        "data": {
                            "tool_id": matched_tool["id"],
                            "result": tool_result,
                            "registered_dataset_id": registered_dataset_id,
                            # 回灌目标：确认门执行卡（card_id=op_id）。前端把本卡
                            # 合并进执行卡展示，不再单独渲染一张结果卡。
                            "target_card_id": exec_op_id,
                        },
                    },
                }
                # 图片/表格/文件 → 直接结束，不调 LLM（前端已有渲染器）。
                # 失败不能静默：卡片上的报错是给机器看的，用户还需要一句
                # 「哪里不对、下一步怎么办」，否则对话停在一张红灯卡片上。
                if _ok and tool_result.get("output_format") in ("image", "table", "file"):
                    return
                if not _ok:
                    yield {"event": "content", "data": {
                        "text": self._failure_reply(matched_tool, tool_result)}}
                    return
            else:
                tool_failed = True
                yield {"event": "step", "data": {"id": "call_tool", "text": f"工具「{_tool_name}」执行失败", "status": "error"}}
                yield {
                    "event": "card",
                    "data": {
                        "type": "result-summary",
                        "title": f"工具执行失败: {matched_tool['name']}",
                        "summary": "工具调用失败，请检查工具代码或参数",
                        "data": {"tool_id": matched_tool["id"], "error": "execution_failed",
                                "target_card_id": exec_op_id},
                    },
                }

        # 工具执行失败 → 不调用 LLM，但必须给用户一句可操作的话
        if tool_failed:
            yield {"event": "content", "data": {
                "text": self._failure_reply(matched_tool, tool_result or {})}}
            return

        user_msg, image_urls = self._build_user_message(content, attachments, dataset_paths)
        # 工具结果作为上下文注入（包含 data 中的详细内容）
        if tool_result:
            # 提取 summary：优先 message → data.text → 其他关键字段
            summary = tool_result.get("message", "") or tool_result.get("summary", "")
            # 把 data 中的详细内容也合并进去
            data = tool_result.get("data", {})
            if isinstance(data, dict):
                # 先提取 text/output 文本字段
                data_text = data.get("text", "") or data.get("output", "")
                if data_text:
                    if summary:
                        summary = f"{summary}\n\n{data_text}"
                    else:
                        summary = data_text
                # 把其他结构化字段（area, volume 等）也追加到 summary
                other_fields = {k: v for k, v in data.items() if k not in ("text", "output")}
                if other_fields:
                    fields_str = json.dumps(other_fields, ensure_ascii=False)
                    if summary:
                        summary = f"{summary}\n\n详细数据: {fields_str}"
                    else:
                        summary = fields_str
            if not summary:
                # 兜底：提取不含 image_path/data 的关键信息
                info = {k: v for k, v in tool_result.items() if k not in ("data", "image_path", "output_format")}
                summary = json.dumps(info, ensure_ascii=False, default=str)[:300]
            user_msg += (
                f"\n\n[系统提示: 工具 {matched_tool['id']} 已自动执行。"
                f"结果: {summary}。"
                f"请基于该真实结果，用自然语言分析其结构与含义（关键字段、关键发现、结论），"
                f"回复用户；不要逐字复述 JSON 数据，也不要自行推测或计算。]"
            )
        # 小图片作为多模态内容随消息发送（大图片/二进制只给元数据+file_id）
        if image_urls:
            history.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": user_msg},
                    *[{"type": "image_url", "image_url": {"url": u}} for u in image_urls],
                ],
            })
        else:
            history.append({"role": "user", "content": user_msg})

        # 裁剪历史
        max_history = self.config.get("max_history", 20)
        if len(history) > max_history * 2 + 1:
            history = [history[0]] + history[-(max_history * 2):]

        try:
            yield {"event": "step", "data": {"id": "reply", "text": "正在整理回复", "status": "running"}}
            full_response = ""
            platform_tools = self._platform_tool_schemas()
            # 工作空间工具并入 function calling：LLM 想调用时能真正发起
            # tool_call（否则只能写成文本，被当普通回复透传）。
            # name 已清洗为合法字符集，真实工具 id 经 ws_name_map 反查。
            ws_name_map: dict[str, str] = {}
            workspace_tools = self._workspace_tool_schemas(workspace_tool_ids, ws_name_map)
            all_tools = platform_tools + workspace_tools
            workspace_names = set(ws_name_map)

            if not all_tools:
                # 平台控制不可用（熔断/导入失败）→ 纯文本流式，行为与旧版完全一致
                async for token in self.llm.chat_stream(
                    messages=history,
                    temperature=0.1,
                    max_tokens=self.config.get("max_tokens", 100000),
                ):
                    full_response += token
                    yield {"event": "content", "data": {"text": token}}
            else:
                # tool_call 循环：模型请求平台工具 → 经主进程网关执行 → 结果回填继续生成
                max_tool_rounds = 5
                for _ in range(max_tool_rounds):
                    round_text = ""
                    tool_calls = None
                    async for kind, payload in self.llm.chat_stream_with_tools(
                        messages=history,
                        tools=all_tools,
                        temperature=0.1,
                        max_tokens=self.config.get("max_tokens", 100000),
                    ):
                        if kind == "content":
                            round_text += payload
                            yield {"event": "content", "data": {"text": payload}}
                        elif kind == "tool_calls":
                            tool_calls = payload

                    if not tool_calls:
                        full_response = round_text
                        break

                    # 回填 assistant 的 tool_calls 消息（协议要求与后续 tool 结果配对）
                    history.append({
                        "role": "assistant",
                        "content": round_text,
                        "tool_calls": [
                            {"id": tc.get("id") or f"call_{i}", "type": "function",
                             "function": {
                                 "name": tc.get("name", ""),
                                 "arguments": json.dumps(
                                     tc.get("arguments") if isinstance(tc.get("arguments"), dict) else {},
                                     ensure_ascii=False),
                             }}
                            for i, tc in enumerate(tool_calls)
                        ],
                    })
                    for i, tc in enumerate(tool_calls):
                        call_id = tc.get("id") or f"call_{i}"
                        if tc.get("name") in workspace_names:
                            # 统一执行确认门：先对**本轮全部未配对**的 tool_calls
                            # 补占位 tool 消息（OpenAI 协议要求 assistant 的
                            # tool_calls 与 tool 消息严格配对，history 跨轮持久化
                            # 后缺配对会在下一轮被 API 拒 400），再发确认卡结束
                            # 本轮。用户在下一轮经 ② 分支显式确认后执行。
                            for j, tc2 in enumerate(tool_calls[i:], start=i):
                                history.append({
                                    "role": "tool",
                                    "tool_call_id": tc2.get("id") or f"call_{j}",
                                    "content": "(等待用户确认)",
                                })
                            fname = str(tc.get("name") or "")
                            ws_info = ws_name_map.get(fname) or {"id": fname, "name": fname}
                            args = tc.get("arguments")
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except Exception:
                                    args = {}
                            if not isinstance(args, dict):
                                args = {}
                            entry = await self._create_workspace_exec_pending(
                                session_key, ctx.user_id or "default",
                                ws_info, args, None)
                            yield self._workspace_exec_card(entry)
                            return  # 结束本轮，等用户下一轮确认
                        else:
                            result = await self._run_platform_tool(tc, ctx, session_key)
                        if result.get("status") == "pending" and result.get("confirm"):
                            # 写操作 → 下发确认卡片，本轮结束（不再继续 LLM，
                            # 避免生成多余的"请确认"回复；结果卡在用户确认后出现）。
                            # history 是本会话持久列表的原地引用：结束前必须为
                            # 本条与其余未处理的 tool_calls 补占位 tool 消息，
                            # 否则持久化后 tool_calls 缺配对，下一轮 API 报 400。
                            for j, tc2 in enumerate(tool_calls[i:], start=i):
                                history.append({
                                    "role": "tool",
                                    "tool_call_id": tc2.get("id") or f"call_{j}",
                                    "content": "(等待用户确认)",
                                })
                            yield {
                                "event": "card",
                                "data": {
                                    "type": "platform-confirm",
                                    "title": "请确认平台操作",
                                    "summary": result["confirm"].get("summary", ""),
                                    "data": result["confirm"],
                                },
                            }
                            return
                        history.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": self._clip_tool_result(result),
                        })
                else:
                    # 轮次用尽仍未给出最终回答 → 不带工具强制收尾，防止无限循环
                    async for token in self.llm.chat_stream(
                        messages=history,
                        temperature=0.1,
                        max_tokens=self.config.get("max_tokens", 100000),
                    ):
                        full_response += token
                        yield {"event": "content", "data": {"text": token}}

            # LLM 流式输出已全部完成
            yield {"event": "step", "data": {"id": "reply", "text": "回复整理完成", "status": "done"}}

            # 创建工具/Agent 一律走平台工具 create_tool / create_agent，
            # 不再识别 TOOL_NEEDED 文本标记（模型偶尔仍会照旧习惯输出，
            # 直接剥掉标记文字，避免把内部协议泄漏给用户）。
            full_response = re.sub(r"TOOL_NEEDED[:：]\s*", "", full_response).strip()

            # 回复整洁化：模型习惯在「即将调用平台工具」时甩出空代码块围栏
            # （``` 后内容为空）或纯空白——它们在前端渲染成一个空代码框，
            # 是很明显的界面噪音。这里统一剥掉，同时把结尾的
            # 「我来创建 / 我先调用…」这类过程台词去掉，结果卡/确认卡
            # 才是真正的信息载体。
            full_response = self._tidy_reply(full_response)

            # 保存对话历史：只保留纯文本轮次。
            # tool_calls/tool 中间消息跨轮保留会因历史裁剪拆散配对而报协议错误，
            # 且工具结果只对当轮决策有意义，无需跨轮记忆。
            history.append({"role": "assistant", "content": full_response})
            self._sessions[session_key] = [
                m for m in history
                if m.get("role") in ("system", "user", "assistant") and not m.get("tool_calls")
            ]

        except Exception as e:
            yield {
                "event": "error",
                "data": {"code": "llm_error", "message": str(e)},
            }

    # ── 工作空间工具统一执行确认门 ──────────────────────────────
    # 所有工作空间工具执行路径（预匹配直执行、多候选选择后、LLM function
    # calling）都必须先经确认门拿到用户确认才能执行；auto 模式由前端对本
    # 确认卡自动回发确认消息（同一扇门、同一协议），语义与设计一致。

    _EXEC_CANCEL_WORDS = ("取消", "取消执行", "算了", "不执行", "不用了", "cancel")
    _EXEC_CONFIRM_WORDS = ("确认", "确认执行", "确定", "同意", "执行", "confirm", "yes")

    async def _purge_expired_confirm(self, session_key: str, owner: str):
        """丢弃超时的工作空间工具确认（TTL 与网关 confirm.py 的 15 分钟对齐）。

        旧 pending 不过期的话，其缓存的 LLM 参数可能被很久之后一条恰好
        模糊匹配上的消息触发执行——对齐网关不变量：超时即作废并落审计。
        """
        entry = self._pending_confirm.get(session_key)
        if not entry:
            return
        if time.time() <= entry.get("expires_at", float("inf")):
            return
        self._pending_confirm.pop(session_key, None)
        if entry.get("kind") == "workspace-exec":
            cand = (entry.get("candidates") or [{}])[0]
            await self._audit_workspace_op(
                op_id=entry.get("op_id", ""), session_key=session_key, owner=owner,
                tool=cand.get("name", cand.get("id", "")),
                args=entry.get("arguments") or {}, status="expired")

    def _resolve_exec_confirmation(self, answer: str) -> str | None:
        """判定用户对执行确认卡的回复："confirm" / "cancel" / None。

        只接受**显式**回复：确认/取消关键词，或确认卡的点选消息模板
        （前端按钮与 auto 模式都发「我选择第N个工具：名称」）。
        绝不复用 _resolve_tool_selection 的模糊匹配——那会让任意一条含
        候选名字片段的消息拿旧参数直接执行。
        """
        a = (answer or "").strip()
        if not a:
            return None
        al = a.lower()
        if a in self._EXEC_CANCEL_WORDS or al in self._EXEC_CANCEL_WORDS:
            return "cancel"
        # 确认卡按钮 / auto 模式自动回发的模板消息
        if re.match(r"^我选择第\d+个工具[:：]", a):
            return "confirm"
        if a in self._EXEC_CONFIRM_WORDS or al in self._EXEC_CONFIRM_WORDS:
            return "confirm"
        # 「确认执行xx」「确认使用xx」明确表态；或极短口头确认（确认吧/执行吧）。
        # 长句以「确认」开头的不算（如「确认一下这个数据集的详情」是新需求）。
        if a.startswith(("确认执行", "确认使用")):
            return "confirm"
        if len(a) <= 4 and (a.startswith("确认") or a.startswith("执行")):
            return "confirm"
        return None

    async def _audit_workspace_op(self, *, op_id: str, session_key: str, owner: str,
                                  tool: str, args: dict, status: str,
                                  result: dict | None = None, error: str = "",
                                  duration_ms: int | None = None) -> None:
        """工作空间工具生命周期写 chat_ops 审计（pending/confirmed/executed/...）。

        审计唯一入口是主进程的 core.chat_control.audit（见其模块注释）。
        交互 Agent 是子进程，经总线单向帧交主进程落库（不等回执，帧
        本身按序写入 stdout）；无总线（进程内测试/调试）时才直接落库
        （uid 显式指定，保证落对用户的库）。
        """
        try:
            if getattr(self, "_bus", None) is not None:
                await self._bus.workspace_audit(
                    op_id=op_id or "", session_id=session_key, owner=owner,
                    tool=tool, args=args or {}, risk="write", status=status,
                    result=result, error=error, duration_ms=duration_ms)
                return
            from core.chat_control import audit
            audit.record(op_id=op_id or "", session_id=session_key, owner=owner,
                         tool=tool, args=args or {}, risk="write", status=status,
                         result=result, error=error, duration_ms=duration_ms,
                         uid=owner)
        except Exception:
            pass  # 审计失败不得影响对话

    def _new_workspace_op_id(self) -> str:
        return f"wop_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"

    async def _create_workspace_exec_pending(self, session_key: str, owner: str,
                                             tool_info: dict, params: dict,
                                             mode: str | None) -> dict:
        """登记一个待确认的工作空间工具执行（确认门的 pending 状态 + 审计 pending）。"""
        op_id = self._new_workspace_op_id()
        name = tool_info.get("name") or tool_info.get("id", "")
        now = time.time()
        self._pending_confirm[session_key] = {
            "kind": "workspace-exec",
            "candidates": [{"id": tool_info.get("id", ""), "name": name, "index": 1}],
            "arguments": params,
            "mode": mode,
            "op_id": op_id,
            "created_at": now,
            "expires_at": now + _CONFIRM_TTL_SECONDS,
        }
        await self._audit_workspace_op(op_id=op_id, session_key=session_key, owner=owner,
                                       tool=name, args=params, status="pending")
        return self._pending_confirm[session_key]

    def _workspace_exec_card(self, entry: dict) -> dict:
        """确认门下发的确认卡片事件（前端 auto 模式按 kind 自动确认）。

        card_id/op_id 用审计 op_id 作稳定标识：执行结果以「回灌卡」事件
        （kind=workspace-exec-result, target_card_id=op_id）回写这张卡，
        前端按 id 把结果合并进执行卡，不再单独展示结果卡。
        """
        cand = entry["candidates"][0]
        return {
            "event": "card",
            "data": {
                "type": "tool-confirm",
                "card_id": entry.get("op_id", ""),
                "title": "请确认执行工具",
                "summary": f"即将执行工具 {cand['name']}，参数: "
                           f"{json.dumps(entry.get('arguments') or {}, ensure_ascii=False)[:200]}。"
                           f"回复「确认」执行，「取消」放弃（{int(_CONFIRM_TTL_SECONDS // 60)} 分钟内有效）。",
                "data": {
                    "candidates": entry["candidates"],
                    "kind": "workspace-exec",
                    "op_id": entry.get("op_id", ""),
                },
            },
        }

    @staticmethod
    def _sanitize_function_name(raw: str) -> str:
        """工具 id → OpenAI function name 合法字符集（^[a-zA-Z0-9_-]{1,64}$）。

        含点号/中文的 id 直接作 name 会让整个 tools 请求被 OpenAI 兼容 API
        拒绝（连平台工具一起不能调），必须清洗；真实 id 经映射表反查。
        """
        cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", str(raw or ""))[:64]
        return cleaned or "tool"

    def _platform_tool_schemas(self) -> list[dict]:
        """平台工具声明（function calling）。

        与网关共用同一注册表，保证「模型能调的」永远等于「网关认的」。
        导入失败或熔断关闭时返回空列表，主流程自动回退纯文本流式。
        """
        try:
            from core.chat_control import tool_schemas
            return tool_schemas()
        except Exception:
            return []

    def _workspace_tool_schemas(self, workspace_tool_ids: list[str] = None,
                                name_map: dict | None = None) -> list[dict]:
        """工作空间工具的 function calling schema（与平台工具并存）。

        工具注册只有 param_meta（无 JSON Schema），这里转成 OpenAI function
        calling 格式。name 必须是 `^[a-zA-Z0-9_-]{1,64}$`——含点号/中文的
        工具 id 直接作 name 会让整个 tools 请求被 OpenAI 兼容 API 拒绝，
        因此清洗后作 name，真实 id 经 name_map（name → tool_id）反查；
        清洗后重名的追加数字后缀保证唯一。
        之前工作空间工具只写在 system prompt 文本里，LLM 想调用只能输出文本
        —— 被当普通回复透传。并入 schema 后才能真正发起 tool_call。
        """
        try:
            from core.resource.registry.tool_registry import ToolRegistry
            reg = ToolRegistry()
            tools = reg._read()
            active = [t for t in tools if t.get("status") == "active"]
            if workspace_tool_ids:
                ws_set = set(workspace_tool_ids)
                active = [t for t in active if t.get("id") in ws_set]

            type_map = {
                "string": "string", "str": "string",
                "int": "integer", "integer": "integer",
                "float": "number", "number": "number",
                "bool": "boolean", "boolean": "boolean",
                "list": "array", "array": "array",
                "dict": "object", "object": "object", "json": "object",
            }
            schemas = []
            used_names: set[str] = set()
            for t in active:
                properties = {}
                required = []
                for p in t.get("param_meta") or []:
                    jtype = type_map.get((p.get("type") or "string").lower(), "string")
                    properties[p["name"]] = {"type": jtype, "description": p.get("desc", "")}
                    if p.get("required"):
                        required.append(p["name"])
                desc = t.get("description") or t.get("name", "")
                if t.get("tags"):
                    desc = f"{desc}（标签: {', '.join(t['tags'][:3])}）"
                fname = self._sanitize_function_name(t["id"])
                if fname in used_names:
                    # 清洗后重名（如 "a.b" 与 "a_b"）→ 数字后缀保证唯一
                    n = 2
                    while f"{fname}_{n}" in used_names:
                        n += 1
                    fname = f"{fname}_{n}"
                used_names.add(fname)
                if name_map is not None:
                    name_map[fname] = {"id": t["id"], "name": t.get("name") or t["id"]}
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": fname,
                        "description": desc,
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                })
            return schemas
        except Exception:
            return []

    async def _run_workspace_tool(self, tc: dict, ctx: AgentContext, session_key: str,
                                  confirmed: bool = False) -> dict:
        """执行 LLM function calling 指定的工作空间工具。

        LLM 已给出 arguments（含 file_id）。确认卡之后（confirmed=True）
        统一经平台网关 run_tool 执行——与工具匹配路径同一条执行/审计通道，
        本 Agent 不再自己直连 ToolExecutor。
        """
        tool_id = str(tc.get("name") or "")
        args = tc.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                return {"status": "error", "error": "工具参数不是合法 JSON，请生成正确参数后重试"}
        if not isinstance(args, dict):
            args = {}
        return await self._exec_via_gateway(tool_id, args, ctx, session_key,
                                            confirmed=confirmed)

    async def _exec_via_gateway(self, tool_id: str, params: dict,
                                ctx: AgentContext, session_key: str,
                                confirmed: bool = False) -> dict:
        """经平台网关 run_tool 执行工具，返回 ToolExecutor 同构结果。

        所有工作空间工具执行（工具匹配直执行 / 候选确认 / 参数补全 /
        LLM tool_call）最终都收敛到这里：一张确认卡、一条审计流水、
        一个执行入口。
        """
        if self._bus is None:
            # 无总线（进程内调试/单测）时退回直接执行
            try:
                from core.executor.tool_executor import ToolExecutor
                return await ToolExecutor.execute(tool_id=tool_id, params=params, timeout=None)
            except Exception as e:  # noqa: BLE001
                return {"status": "failed", "message": f"工具执行异常: {str(e)[:300]}"}
        receipt = await self._bus.platform_call(
            "run_tool", {"tool_id": tool_id, "params": params, "confirmed": bool(confirmed)},
            session_id=ctx.session_id or session_key,
            user_id=ctx.user_id or "default",
        )
        if receipt.get("status") == "pending":
            # 未确认却要求直执行：交回确认卡，绝不静默执行
            return {"status": "error", "error": "等待用户确认后执行"}
        if receipt.get("status") == "ok":
            return receipt.get("result") or {}
        return {"status": "failed", "message": str(receipt.get("error") or "工具执行失败"),
                "data": {}}

    async def _run_platform_tool(self, tc: dict, ctx: AgentContext, session_key: str) -> dict:
        """执行单个平台工具调用，返回结构化结果（不抛异常）。

        子进程不直接碰 registry/文件系统——一律经总线帧交主进程网关执行，
        由网关做身份注入、严格校验、风险分级与审计。
        """
        name = str(tc.get("name") or "")
        args = tc.get("arguments")
        if isinstance(args, str):
            # 参数 JSON 解析失败（client 层保留原始字符串）→ 回错误让 LLM 自纠
            return {"status": "error",
                    "error": f"工具参数不是合法 JSON，无法执行: {args[:200]}。请生成正确的 JSON 参数后重试。"}
        if not isinstance(args, dict):
            args = {}
        if self._bus is None:
            return {"status": "error", "error": "平台通道不可用（Agent 未运行在子进程模式下）"}
        return await self._bus.platform_call(
            name, args,
            session_id=ctx.session_id or session_key,
            user_id=ctx.user_id or "default",
        )

    @staticmethod
    def _clip_tool_result(result) -> str:
        """工具结果序列化并截断，防止大结果撑爆上下文。"""
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            text = str(result)
        if len(text) > 6000:
            text = text[:6000] + "…（结果已截断）"
        return text

    @staticmethod
    def _failure_reply(tool: dict, result: dict) -> str:
        """工具执行失败时给用户一句可操作的话（不是把 traceback 丢给用户）。

        卡片里已有完整失败详情，正文只回答「错在哪、下一步怎么做」：
        找不到数据集/列时给出可改的方向，其它错误提示重试或修改需求。
        """
        name = (tool or {}).get("name") or (tool or {}).get("id") or "工具"
        msg = str((result or {}).get("message") or "").strip()
        # 只取第一行：多行的是「错误 + Traceback」，用户不需要栈信息；
        # 开头的「执行失败:」是网关加的标签，正文里已经有失败语气了
        head = msg.split("\n", 1)[0][:160]
        head = re.sub(r"^(执行失败|工具执行失败)\s*[:：]\s*", "", head).strip()
        if "数据集" in msg and ("不存在" in msg or "无法解析" in msg):
            hint = "请确认要用的数据集名称，我可以用正确的数据集重新执行。"
        elif "列" in msg and ("不存在" in msg or "缺少" in msg):
            hint = "请确认要统计的列名（如 OT），或告诉我数据里实际有哪些列。"
        elif "recent_days" in msg or "参数" in msg:
            hint = "参数取值需要调整，告诉我正确的取值我再跑一次。"
        else:
            hint = "可以换个数据集或参数再试，也可以告诉我期望的输出形式。"
        return f"「{name}」没有跑成功：{head}\n\n{hint}"

    @staticmethod
    def _tidy_reply(text: str) -> str:
        """去掉回复里的空代码围栏与多余空白行（纯展示层清洗）。

        模型在「准备调用平台工具」时经常先甩一段 ``` 开围栏却没有任何
        内容，前端会渲染成空代码框；连续多个空行也会把卡片挤得很散。
        这里只做确定性的文本清洗，不改语义、不删有内容的代码块。
        """
        s = text or ""
        # 成对的空代码块：```...```（中间只有空白）整体删除
        s = re.sub(r"```[a-zA-Z]*\s*```", "", s)
        # 落单的空围栏行（只有 ``` 或 ```python）
        s = re.sub(r"^[ \t]*```[a-zA-Z]*[ \t]*$", "", s, flags=re.MULTILINE)
        # 三行以上连续空行压成一行
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()

    async def _explain_platform_receipt(
        self, ctx: AgentContext, receipt: dict, session_key: str
    ) -> AsyncGenerator[dict, None]:
        """把平台操作的网关回执解读成自然语言发给用户。

        回执轮是前端代发的隐藏轮次（UI 无用户消息），因此这里以内部
        user 消息驱动 LLM；解读文本会进入会话历史与落库，用户后续可以
        直接针对结果继续追问讨论。
        """
        tool = str(receipt.get("tool") or "平台操作")
        ok = receipt.get("status") in ("ok", "executed")
        payload = {
            "tool": tool,
            "summary": receipt.get("summary") or "",
            "result": receipt.get("result") if ok else None,
            "error": receipt.get("error") if not ok else None,
        }
        note = (
            f"（系统回执，非用户输入）用户刚在确认卡片上确认了平台操作，"
            f"{'已成功执行' if ok else '执行失败'}。网关回执："
            f"{self._clip_tool_result(payload)}\n"
            "请用简洁的中文向用户解读这次操作的结果：做了什么、关键数据或结论、"
            "下一步建议。数据类结果提炼要点，不要照抄 JSON。"
            "不要向用户提及本条系统回执本身。"
            "【禁止暴露内部标识】不要向用户报工具 id、Agent id、数据集 id 这类内部标识"
            "（如 etf-90d-ranking-review-agent），需要指代时只说中文名称；"
            "确实需要用户填写的参数值（如 dataset_id）才可原样出现。"
            "【状态必须与回执一致】回执里 self_check=failed 或带 warning/error 时，"
            "必须原样说明「已创建，但自检/运行未通过」及具体原因，"
            "严禁写成「自检已通过」「已可用」「一切正常」；只有 self_check=passed 且没有"
            "warning/error 才能说自检通过。"
        )
        history = self._sessions.get(session_key) or []
        if not history:
            history = [{"role": "system", "content": self._build_system_with_context([])}]
        history = history + [{"role": "user", "content": note}]
        full = ""
        async for token in self.llm.chat_stream(
            messages=history,
            temperature=0.2,
            max_tokens=self.config.get("max_tokens", 100000),
        ):
            full += token
            yield {"event": "content", "data": {"text": token}}
        history.append({"role": "assistant", "content": full})
        self._sessions[session_key] = history

        # start_agent 回执的延续：用户确认启动 Agent 的原始动机是「让它处理我的
        # 问题」，只回一句「已启动」等于把请求丢在半路。这里把此前待处理的
        # Agent 请求接着派发出去（派发失败或已完成则不再重复）。
        pending = self._pending_agent_request.pop(session_key, None)
        if tool == "start_agent" and ok and pending:
            entry = await self._registry_entry(pending.get("agent_id", ""))
            if entry is not None:
                yield {"event": "step", "data": {"id": "dispatch_agent",
                                                 "text": f"继续交给「{entry.get('name', '')}」处理",
                                                 "status": "running"}}
                async for ev in self._dispatch_to_agent(entry, pending.get("request", ""),
                                                        session_key, None, start_wait_s=90.0):
                    yield ev

    async def _registry_entry(self, agent_id: str) -> dict | None:
        """按 id 取当前用户可见的 Agent 条目（回执续跑派发用）。"""
        if not agent_id:
            return None
        try:
            from core.resource.registry.agent_registry import AgentRegistry
            from core.user_context import get_current_user_id
            reg = AgentRegistry()
            me = get_current_user_id()
            for e in reg._read():
                if e.get("id") == agent_id and reg.is_visible(e, me):
                    return e
        except Exception:
            return None
        return None

    async def _match_agent(self, query: str):
        """判断用户是否指定了某个已注册 Agent，返回单个条目 / 多候选 list / None。
        两级匹配：
        ① 名称级强匹配（名称/id 显式出现在句中出现）—— 精确、无歧义，优先；
        ② 未命中时做 LLM 语义匹配（用功能概述判断用户需求是否正好由某个
           Agent 完成，例如「预测 ETF 510500 的价格」→ ETF目标价预测Agent）。
           语义匹配置信度要求高，宁缺毋滥：错派 Agent 的代价高于回退去匹配工具。

        「创建新 Agent」的诉求不是派发：用户说「创建一个叫 XX 的 Agent」时，
        名称/语义都可能命中同名老 Agent，派发过去只会执行老 Agent，创建意图
        丢失。此类请求一律返回 None，交给主流程的平台工具 create_agent。
        """
        if self._detect_create_intent(query):
            return None
        # 「用这个工具跑一次」「用『ETF 近90日涨跌幅排行』跑一下」指向的是工具，
        # 不是 Agent：Agent 名常常是工具名的加长版（…榜点评 Agent），只要名字里
        # 包含工具名就会被拽进派发，多一张启动确认卡、执行的还是别的对象。
        # 因此句子里提到「工具」且没有 Agent/智能体 字样时，一律不做 Agent 派发。
        mentions_tool_word = "工具" in query
        mentions_agent_word = ("Agent" in query or "agent" in query.lower()
                               or "智能体" in query)
        if mentions_tool_word and not mentions_agent_word:
            return None
        try:
            from core.resource.registry.agent_registry import AgentRegistry
            from core.user_context import get_current_user_id
            reg = AgentRegistry()
            me = get_current_user_id()
            # 只匹配对用户可见的 Agent：普通用户创建的私有 Agent 不参与派发
            entries = [e for e in reg._read() if reg.is_visible(e, me)]
        except Exception:
            return None
        q = query.strip()
        ql = q.lower()
        hits = []
        for e in entries:
            if e.get("status") != "active" or e.get("id") == self.spec.id:
                continue
            name = (e.get("name") or "").strip()
            aid = (e.get("id") or "").strip()
            matched = False
            if name and name in q:
                matched = True
            elif aid and aid.lower() in ql:
                matched = True
            elif name:
                # 「ETF次日方向分析Agent」写作「ETF 次日方向分析 agent」等带空格/大小写变体
                import re as _re
                pat = _re.escape(name).replace("\\ ", "\\s*")
                if len(name) >= 4 and _re.search(pat, q, _re.IGNORECASE):
                    matched = True
            if matched:
                hits.append(e)
        if hits:
            return hits[0] if len(hits) == 1 else hits

        # 名称未命中 → 语义匹配（排除自己）
        candidates = [e for e in entries
                      if e.get("status") == "active" and e.get("id") != self.spec.id]
        return await self._llm_semantic_match_agent(q, candidates)

    async def _llm_semantic_match_agent(self, query: str, agents: list[dict]) -> dict | list | None:
        """用 LLM 判断用户需求是否正好对应某个 Agent（高置信度才命中）。

        返回单个 Agent 条目、多个候选 list，或 None。
        """
        try:
            lines = []
            for a in agents:
                overview = self._read_agent_overview(a)
                info = f"- {a.get('id')}: {a.get('name', '')}"
                if overview:
                    info += f" — {overview}"
                lines.append(info)
            if not lines:
                return None

            prompt = f"""你是 Agent 匹配器。判断用户的需求是否正好应由下列某个 Agent 完成。

用户需求：
"{query}"

可用 Agent 列表：
{chr(10).join(lines)}

请判断：
- 只有当某个 Agent 的职责确实就是用户要做的事时，才把它的 id 放进 matched_agent_ids（按匹配度从高到低，最多 3 个）
- 宁缺毋滥：用户需求与 Agent 职责不完全一致、或只是普通闲聊/咨询时，返回空列表
- 用户明确要求「新建/创建/开发 Agent」时返回空列表
- 用户想直接处理数据但列表里没有职责匹配的 Agent 时返回空列表（交给后续工具匹配）

只返回 JSON，格式：{{"matched_agent_ids": ["agent-id1", "agent-id2"]}}
不要返回其他内容。"""
            import asyncio
            response = await asyncio.wait_for(
                self.llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=300,
                ),
                timeout=10,
            )
            import json
            m = re.search(r"\{[^{}]*\}", response.strip())
            if not m:
                return None
            data = json.loads(m.group(0))
            ids = data.get("matched_agent_ids") or []
            result = []
            for aid in ids:
                for a in agents:
                    if a.get("id") == aid:
                        result.append(a)
                        break
            if not result:
                return None
            # 单个直接派发；多个交给上层弹确认卡片
            return result[0] if len(result) == 1 else result
        except Exception:
            return None

    def _read_agent_overview(self, agent: dict) -> str:
        """读取 Agent 规范 MD 的「功能概述」章节（对齐工具侧 _read_tool_overview）"""
        try:
            spec_path = agent.get("spec_path", "")
            if not spec_path:
                return ""
            from core.resource import paths as _paths
            for root in _paths.resource_roots():
                md_file = root / "agents" / spec_path
                if md_file.exists():
                    content = md_file.read_text(encoding="utf-8")
                    m = re.search(r"功能概述\s*\n\s*(.+?)(?=\n##|\Z)", content, re.DOTALL)
                    if m:
                        return m.group(1).strip()[:200]
            return ""
        except Exception:
            return ""

    def _resolve_agent_selection(self, answer: str, candidates: list[dict]) -> dict | None:
        """识别用户对候选 Agent 的选择（编号或名称），与 _resolve_tool_selection 同构"""
        if not candidates:
            return None
        a = answer.strip()
        a_lower = a.lower()
        num_matches = re.findall(r"\d+", a)
        if num_matches:
            idx = int(num_matches[0])
            if 1 <= idx <= len(candidates) and len(a) <= 8:
                return candidates[idx - 1]
        for t in candidates:
            name = t.get("name", "")
            tid = t.get("id", "")
            if name and name in a:
                return t
            if tid and tid.lower() in a_lower:
                return t
        return None

    async def _retry_agent_after_start(self, aid: str, request: str,
                                       wait_s: float = 60.0) -> dict:
        """Agent 启动后重试原请求：进程拉起需要时间，先轮询到可执行再提交。

        返回最后一次 call_agent 的响应（成功或失败都由调用方统一处理）。
        """
        deadline = time.monotonic() + wait_s
        last: dict = {"error": "启动超时"}
        delay = 1.0
        while time.monotonic() < deadline:
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 5.0)
            resp = await self._bus.call_agent(aid, "execute", {"content": request}, timeout=300)
            last = resp if isinstance(resp, dict) else {"error": str(resp)}
            err = str(last.get("error", ""))
            # 只有「未运行/正在启动」这类可恢复状态才继续等，其他失败立刻返回
            if not err or ("未运行" not in err and "启动中" not in err and "未就绪" not in err):
                return last
        return last

    async def _dispatch_to_agent(self, agent_entry: dict, request: str, session_key: str,
                                 user_id: str = "", start_wait_s: float = 5.0):
        """把用户请求转发给指定 Agent（经主进程 RPC），聚合结果后返回。

        RPC 语义（见 core/agent/entrypoint.py）：参数平铺为 execute kwargs，
        content 即用户原话；成功聚合为 {"text": ...}。

        start_wait_s：目标 Agent 未运行、且启动已获授权（自动确认策略）时，
        最多等待多久让它就绪。回执轮续跑（用户已在确认卡上确认过启动）必须
        给足等待时间——此前沿用默认 60s 且第一轮命中尚未就绪，未做重试，
        用户看到的就是「0 个工具调用」这类空结果。
        """
        aid = agent_entry.get("id", "")
        name = agent_entry.get("name", aid)
        role = agent_entry.get("role", "task")
        if role == "web-service":
            yield {"event": "content", "data": {"text": f"**{name}** 是 Web 服务式 Agent，请在其详情页调用服务接口。"}}
            return
        if not self._bus:
            yield {"event": "content", "data": {"text": f"当前环境不支持 Agent 间调用，请打开 **{name}** 的详情页直接交互。"}}
            return

        # 先把「转交中」这句中间态说清楚，但如果后面立刻要用户确认启动，
        # 这句话会变成让人困惑的「半句话」（用户还没确认，已经在转交了）。
        # 因此只在确定要直接执行时才说；需要确认的分支由卡片文案负责。
        yield {"event": "step", "data": {"id": "dispatch_agent",
                                         "text": f"转交「{name}」处理", "status": "running"}}
        resp = await self._bus.call_agent(aid, "execute", {"content": request}, timeout=300)

        if isinstance(resp, dict) and "未运行" in str(resp.get("error", "")):
            # Agent 未运行 → 启动后重试原请求。
            # 手工确认策略下网关只下发确认卡（status=pending），此时必须把
            # 选择权交给用户：保留原请求待用户确认，不要擅自把启动这一步
            # 说成「已执行」。确认后的续跑由前端在 confirm 成功后补发原话
            # 触发（_pending_agents 重投），因此这里不要求用户重新提问。
            if self._bus is None:
                yield {"event": "content",
                       "data": {"text": f"**{name}** 当前未运行。请到 Agent 空间启动它；启动后直接回复我（如「继续」），我会重新提交任务。"}}
                return

            yield {"event": "step",
                   "data": {"id": "start_agent", "text": f"「{name}」未运行，发起启动", "status": "running"}}
            r = await self._bus.platform_call(
                "start_agent", {"agent_id": aid},
                session_id=session_key, user_id=user_id or "default")
            if isinstance(r, dict) and r.get("status") == "pending" and r.get("confirm"):
                # 启动是写操作，仍需用户确认；保留原请求以便确认后补提交
                self._pending_agents[session_key] = {"agent": agent_entry, "request": request}
                # 待处理请求按 agent_id 登记：回执轮（start_agent 成功）据此续跑，
                # 与服务端「启动完成继续处理原始提问」保持同一语义。
                self._pending_agent_request[session_key] = {
                    "agent_id": aid, "agent_name": name, "request": request}
                yield {"event": "step",
                       "data": {"id": "start_agent",
                                "text": f"「{name}」未运行，需确认后启动", "status": "done"}}
                yield {"event": "step",
                       "data": {"id": "dispatch_agent", "text": "等待用户确认启动", "status": "done"}}
                confirm_data = dict(r["confirm"])
                # 原请求随卡片下发：确认执行在前端异步完成，确认成功后前端要
                # 用它自动续跑，否则启动完成时已没有上下文知道用户问了什么。
                confirm_data["request"] = request
                yield {
                    "event": "card",
                    "data": {
                        "type": "platform-confirm",
                        "title": "请确认平台操作",
                        "summary": r["confirm"].get("summary", ""),
                        "data": confirm_data,
                    },
                }
                yield {"event": "content",
                       "data": {"text": f"**{name}** 当前未运行，需要先启动它才能处理你刚才的问题。\n\n"
                                         "请在上方卡片点「确认执行」——确认后会自动启动该 Agent，"
                                         "并接着处理你刚才的提问，不需要重新描述需求。"}}
                return

            # 网关直接放行（自动确认策略）→ 界面上把「启动」这一步显式展示出来
            yield {"event": "step",
                   "data": {"id": "start_agent", "text": f"「{name}」已启动，等待就绪", "status": "running"}}
            resp = await self._retry_agent_after_start(aid, request, wait_s=start_wait_s)
            if isinstance(resp, dict) and "未运行" in str(resp.get("error", "")):
                self._pending_agents[session_key] = {"agent": agent_entry, "request": request}
                yield {"event": "step",
                       "data": {"id": "start_agent", "text": f"「{name}」启动超时", "status": "error"}}
                yield {"event": "content",
                       "data": {"text": f"**{name}** 启动后仍不可用（{str(resp.get('error'))[:120]}）。"
                                         "请到 Agent 空间检查，启动后回复「继续」我会重新提交任务。"}}
                return
            yield {"event": "step",
                   "data": {"id": "start_agent", "text": f"「{name}」已就绪，开始处理原请求", "status": "done"}}

        session = self._sessions.setdefault(session_key, [])
        if not session:
            # 与其他轮次一致：历史首条恒为 system，裁剪逻辑依赖该约定
            session.append({"role": "system", "content": self._build_system_with_context([])})
        if isinstance(resp, dict) and resp.get("ok"):
            # 本次已拿到 Agent 结果：待办请求（启动后待重投）不再需要，
            # 否则用户下一条消息会被 pending 逻辑再次重投一遍原提问。
            self._pending_agents.pop(session_key, None)
            result = resp.get("result") or {}
            text = result.get("text") if isinstance(result, dict) else json.dumps(result, ensure_ascii=False, default=str)
            text = (text or "").strip() or "（该 Agent 未返回文本结果）"
            # Agent 侧若产出了结果卡（含表格/图片/文件数据），原样转给前端：
            # 表格结果本身就在卡里，丢卡只回一句「成功处理 N 只」用户看不到数据。
            inner = result.get("card") if isinstance(result, dict) else None
            if isinstance(inner, dict) and inner.get("data"):
                yield {
                    "event": "card",
                    "data": {
                        "type": "result-summary",
                        "title": f"Agent 执行: {name}",
                        "summary": (inner.get("summary") or text)[:200],
                        "data": inner["data"],
                    },
                }
            else:
                yield {
                    "event": "card",
                    "data": {
                        "type": "result-summary",
                        "title": f"Agent 执行: {name}",
                        "summary": text[:200],
                        "data": {"agent_id": aid, "result": text[:4000]},
                    },
                }
            # 注入历史，让后续对话能引用本次结果
            session.append({"role": "user", "content": f"[转交 {name}] {request}"})
            session.append({"role": "assistant", "content": f"[{name} 的执行结果]\n{text[:3000]}"})
            # Agent 的结果本身就是给用户的答复（含摘要与结论），直接正文输出。
            # 之前这里再过一轮 LLM「小结」，效果是同一段结论说两遍，且小结提示词
            # 被模型当成正文回显（气泡里出现 <system>…</system>）。
            yield {"event": "content", "data": {"text": text}}
            return
        else:
            err = str((resp or {}).get("error", "未知错误"))[:300]
            yield {
                "event": "card",
                "data": {
                    "type": "result-summary",
                    "title": f"Agent 执行失败: {name}",
                    "summary": err,
                    "data": {"agent_id": aid, "error": err},
                },
            }
            session.append({"role": "user", "content": f"[转交 {name}] {request}"})
            session.append({"role": "assistant", "content": f"[{name} 执行失败] {err}"})
            yield {"event": "content",
                   "data": {"text": f"「{name}」执行失败：{err}"}}
            return

    async def _pre_match_tool(
        self, query: str, workspace_tool_ids: list[str] = None
    ) -> tuple[list[dict], str | None, list[dict]]:
        """预匹配：先用 LLM 语义匹配，失败时回退到关键词匹配。

        返回 (工具空间内的匹配候选, 需要创建的工具描述 或 None,
              工具仓库里的匹配候选（不在工具空间）)

        匹配范围仍以工具空间为准（工具空间是用户显式圈定的能力边界，
        不能因为仓库里有个名字相近的工具就擅自执行）。但仓库里确实有货时
        不能装作没看见——此前正是这里让「查询广州的天气」对着仓库里的
        城市天气预报静默失败。第三项就是给调用方发「加入工具空间」卡片的。
        """
        # 明确「创建工具 / 创建 Agent」意图 → 不做工具匹配，直接进主流程，
        # 由 LLM 调平台工具 create_tool / create_agent（网关确认卡 + 生成 + 审查 +
        # 注册一条龙）。之前这里发 create-tool 卡片提前 return，导致对话既没有
        # 确认卡、也没有任何文字回复，用户只能被引导去手工打开编辑器。
        create_intent = self._detect_create_intent(query)
        if create_intent:
            return [], None, []

        # 0.5 平台元信息查询（"我有哪些数据集"等）→ 不做工具匹配，
        # 交给主流程的平台工具（function calling）用真实数据回答。
        if self._is_platform_meta_query(query):
            return [], None, []

        # 收集活跃工具（限定工具空间范围），其余的留作「仓库候选」
        try:
            from core.resource.registry.tool_registry import ToolRegistry
            reg = ToolRegistry()
            tools = reg._read()
            active = [t for t in tools if t.get("status") == "active"]
            if workspace_tool_ids:
                ws_set = set(workspace_tool_ids)
                outside = [t for t in active if t.get("id") not in ws_set]
                active = [t for t in active if t.get("id") in ws_set]
            else:
                outside = []
        except Exception:
            return [], None, []

        # 1. 优先：工具空间内 LLM 语义匹配（返回多个候选，同时判断是否需创建新工具）
        llm_candidates, llm_create = await self._llm_semantic_match(query, active)
        if llm_candidates:
            return llm_candidates, None, []
        # 用户点名的工具不在工具空间时，按 id/名称直达（不猜语义）
        named_outside = self._explicit_tool_match(query, outside)
        if named_outside:
            return named_outside, None, []
        # 关键词匹配兜底仅在工具空间内做（规则宽松，跨范围容易误伤）
        kw_candidates = self._keyword_match(query, active)
        if kw_candidates:
            return kw_candidates, None, []

        # 2. 工具空间没命中 → 查工具仓库（用户只是还没把它加进工具空间）
        repo_candidates, _ = await self._llm_semantic_match(query, outside)
        if not repo_candidates:
            repo_candidates = self._keyword_match(query, outside)
        if repo_candidates:
            # 仓库有现成工具 → 不要再去建议新建
            return [], None, repo_candidates

        # 3. 两边都没有 → 判断是否需要创建工具
        if llm_create:
            return [], llm_create, []
        if not active:
            need_create = await self._llm_check_create_needed(query)
            return [], need_create, []
        return [], None, []

    def _detect_create_intent(self, query: str) -> str | None:
        """检测用户是否明确表达「创建工具 / 创建 Agent」意图，返回描述（或 None）。

        两类创建意图都识别：工具类（创建工具/写个工具…）与 Agent 类
        （创建 Agent/做一个智能体…）。它们都要走平台工具 create_tool /
        create_agent，既不能去做工具匹配，也不能做 Agent 派发。
        """
        CREATE_KEYWORDS = [
            "创建工具", "新建工具", "生成工具", "做一个工具", "做个工具",
            "创建新工具", "新建一个工具", "开发一个工具", "开发个工具",
            "帮我做工具", "帮我写工具", "我要工具", "写个工具", "写一个工具",
            "create tool", "create a tool", "new tool", "make a tool",
            "我要创建", "我想创建", "帮我创建",
            "创建agent", "创建 agent", "新建agent", "新建 agent",
            "新建一个agent", "新建一个 agent", "生成agent", "生成 agent",
            "创建一个agent", "创建一个 agent", "做一个agent", "做一个 agent",
            "开发一个agent", "开发一个 agent", "创建智能体", "新建智能体",
            "创建一个智能体", "做一个智能体", "帮我创建一个", "帮我做一个",
            "create agent", "create an agent", "new agent", "build an agent",
            # 名称在动词前的语序：「ETTh1 巡检 Agent 帮我实现」这类需求里
            # 「创建/需要一个」与「Agent」之间常隔着一串修饰词，直接按
            # 「创建…Agent」相邻匹配会漏。这里用「动词出现在句中 + 句中出现
            # Agent/智能体」兜底，宁可判成创建意图（创建后仍可复用同名工具）。
            "创建一个", "帮我实现", "实现一个", "搭一个", "搭一个agent",
        ]
        q = query.strip()
        q_lower = q.lower()
        # 英文关键词按小写比对；中文原样比对，避免 casefold 破坏中英混写匹配
        has_create_intent = any(kw in q for kw in CREATE_KEYWORDS if not kw.isascii()) \
            or any(kw in q_lower for kw in CREATE_KEYWORDS if kw.isascii())

        if not has_create_intent:
            return None

        # 提取描述：去掉「创建工具」等意图词，剩下的作为描述
        desc = q
        for kw in CREATE_KEYWORDS:
            if kw.isascii():
                desc = re.sub(re.escape(kw), " ", desc, flags=re.IGNORECASE)
            else:
                desc = desc.replace(kw, " ")
        # 清理标点和多余空格
        desc = re.sub(r"[，。！？,.!?\s]+", " ", desc).strip()
        # 如果去掉意图词后没有实质描述，用原始 query
        if not desc or len(desc) < 2:
            desc = q

        return desc

    # 平台元信息查询的识别模式：列举平台资源（而非处理数据）
    _META_PATTERNS = [
        r"(有哪些|有多少(个)?|列出|列一下|清单|列表|几个)[^。？?!！]{0,8}(数据集|数据|工具|模型|agent|智能体|定时任务|任务)",
        r"(数据集|工具|模型|agent|智能体|定时任务|任务)[^。？?!！]{0,6}(有哪些|有多少|几个|列表|清单)",
    ]
    # 出现这些动词说明用户要处理数据，不是查平台状态
    _DATA_VERBS = ("编辑", "转换", "清洗", "训练", "预测", "绘图", "画图",
                   "导出", "合并", "筛选", "标注", "处理", "分析", "计算")

    def _is_platform_meta_query(self, query: str) -> bool:
        """判断是否为「查询平台本身状态」的元信息问题。

        如「我有哪些数据集」「定时任务有几个」「列一下模型」。
        这类问题必须交给平台工具（function calling）查真实数据回答，
        绝不能被工具预匹配误判成数据处理请求。判定保守：短问句 +
        列举模式命中 + 不含数据处理动词。
        """
        q = query.strip().lower()
        if not q or len(q) > 50:
            return False
        if any(v in q for v in self._DATA_VERBS):
            return False
        return any(re.search(p, q) for p in self._META_PATTERNS)

    def _resolve_tool_selection(self, answer: str, candidates: list[dict]) -> dict | None:
        """识别用户对候选工具的选择（编号或名称），返回选中的工具或 None"""
        if not candidates:
            return None
        a = answer.strip()
        a_lower = a.lower()

        # 1. 按编号选择：1 / 第1个 / 第一个 / 选项1
        num_matches = re.findall(r"\d+", a)
        if num_matches:
            idx = int(num_matches[0])
            if 1 <= idx <= len(candidates):
                return candidates[idx - 1]

        # 2. 按工具名 / id 匹配
        for t in candidates:
            name = t.get("name", "")
            tid = t.get("id", "")
            if name and name in a:
                return t
            if tid and tid.lower() in a_lower:
                return t

        # 3. 模糊：工具名的关键词出现在回答中
        for t in candidates:
            name = t.get("name", "")
            for word in name:
                if word and len(word) >= 1 and word in a and ('一' <= word <= '鿿' or word.isalnum()):
                    # 单个字符太弱，跳过；用 2+ 字符匹配
                    pass
            # 提取 2+ 字符的片段做匹配
            for i in range(len(name) - 1):
                seg = name[i:i + 2]
                if seg in a:
                    return t

        return None

    @staticmethod
    def _explicit_tool_match(query: str, tools: list[dict]) -> list[dict]:
        """用户句子里点名了某个工具（id 或中文名）→ 直接选中它。

        语义匹配只看功能概述，遇到功能相近的多个工具必然选错；用户已经把
        工具 id / 名称说出来时不应再猜。名称取最长命中，避免短名吃长名。
        """
        q_low = (query or "").lower()
        for t in tools:
            tid = str(t.get("id") or "").lower()
            if tid and tid in q_low:
                return [t]
        named = [t for t in tools
                 if (t.get("name") or "") and str(t["name"]) in (query or "")]
        if named:
            named.sort(key=lambda t: len(str(t.get("name"))), reverse=True)
            return named[:1]
        return []

    async def _llm_semantic_match(self, query: str, tools: list[dict]) -> tuple[list[dict], str | None]:
        """用 LLM 做语义匹配。

        返回 (匹配到的工具候选列表, 需要创建的工具描述 或 None)
        """
        try:
            # 用户句子里直接写了工具 id（「用工具 etf-xxx_1794 跑一次」）或
            # 工具中文名时，以显式指名优先，不做语义猜测。
            explicit = self._explicit_tool_match(query, tools)
            if explicit:
                return explicit, None
            # 构建工具候选列表（id + name + tags + 功能概述）
            candidates = []
            for t in tools:
                overview = self._read_tool_overview(t)
                info = f"- {t.get('id')}: {t.get('name', '')}"
                if overview:
                    info += f" — {overview}"
                else:
                    tags = t.get("tags", [])
                    if tags:
                        info += f" [标签: {', '.join(tags)}]"
                candidates.append(info)
            if not candidates:
                return [], None

            tools_text = "\n".join(candidates)
            prompt = f"""你是工具匹配器。根据用户的需求，从以下工具列表中选择可能匹配的工具。

用户需求：
"{query}"

可用工具列表：
{tools_text}

请判断：
- 如果用户明确表示要「创建工具/新建工具/开发工具」，直接返回 need_new_tool=true，并给出新工具的简短中文描述（即使现有工具列表中可能有名称相近的工具，也不要去匹配）
- 如果有一个或多个工具能**高置信度**匹配用户需求（工具的功能确实就是用户要的），返回 matched_tool_ids 列表（按匹配度从高到低，最多 3 个）
- 只要用户提到的对象、数据或动作在列表中找不到对应工具，就返回空列表——**宁缺毋滥，绝不返回功能不符的凑数候选**
- 如果没有任何工具匹配，且用户的需求是「数据处理/文件处理/计算/分析」类（需要用工具完成），返回 need_new_tool=true，并给出新工具的简短中文描述
- 如果用户只是普通闲聊/咨询，不需要工具，返回 need_new_tool=false
- 如果用户是在**查询平台本身的状态**（如「我有哪些数据集/工具/模型/Agent」「定时任务有哪些」「某个数据集的详情」），这是元信息问题、不是要处理数据，返回 {{"matched_tool_ids": [], "need_new_tool": false}}
- 如果用户是在指定/询问某个 Agent（如「用 XX Agent 处理」「YY Agent 是干什么的」），不属于工具需求，返回 {{"matched_tool_ids": [], "need_new_tool": false}}

只返回 JSON，格式：
- 匹配到工具：{{"matched_tool_ids": ["工具id1", "工具id2"]}}
- 需要创建：{{"matched_tool_ids": [], "need_new_tool": true, "description": "新工具描述"}}
- 不需要：{{"matched_tool_ids": [], "need_new_tool": false}}
不要返回其他内容。"""

            import asyncio
            response = await asyncio.wait_for(
                self.llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=400,
                ),
                timeout=10,
            )
            text = response.strip()
            import json
            m = re.search(r"\{[^{}]*\}", text)
            if not m:
                return [], None
            data = json.loads(m.group(0))

            # 多个候选
            matched_ids = data.get("matched_tool_ids") or []
            if matched_ids:
                result = []
                for tid in matched_ids:
                    for t in tools:
                        if t.get("id") == tid:
                            result.append(t)
                            break
                if result:
                    return result, None

            # 兼容旧格式 tool_id 单值
            tool_id = data.get("tool_id")
            if tool_id:
                for t in tools:
                    if t.get("id") == tool_id:
                        return [t], None
                return [], None

            # 无匹配 → 判断是否需要创建
            if data.get("need_new_tool"):
                desc = (data.get("description") or "").strip()
                if desc:
                    return [], desc
            return [], None
        except Exception:
            return [], None

    async def _llm_check_create_needed(self, query: str) -> str | None:
        """工具空间为空时，判断用户需求是否需要创建工具，并生成工具描述"""
        try:
            import asyncio
            prompt = f"""判断用户的需求是否需要创建一个数据处理工具。

用户需求：
"{query}"

规则：
- 如果用户的需求是「数据处理/文件处理/计算/分析/转换」类，需要用工具完成，返回 need_new_tool=true，并给出新工具的简短中文描述
- 如果用户只是普通闲聊/咨询，返回 need_new_tool=false

只返回 JSON：{{"need_new_tool": true, "description": "工具描述"}} 或 {{"need_new_tool": false}}
不要返回其他内容。"""
            response = await asyncio.wait_for(
                self.llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=200,
                ),
                timeout=10,
            )
            text = response.strip()
            import json
            m = re.search(r"\{[^{}]*\}", text)
            if not m:
                return None
            data = json.loads(m.group(0))
            if data.get("need_new_tool"):
                desc = (data.get("description") or "").strip()
                if desc:
                    return desc
            return None
        except Exception:
            return None

    def _read_tool_overview(self, tool: dict) -> str:
        """读取工具 MD 定义文档的「功能概述」章节"""
        try:
            from pathlib import Path
            spec_path = tool.get("spec_path", "")
            if not spec_path:
                return ""
            project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
            md_file = project_root / "resources" / "tools" / spec_path
            if not md_file.exists():
                return ""
            content = md_file.read_text(encoding="utf-8")
            # 提取「功能概述」章节
            m = re.search(r"功能概述\s*\n\s*(.+?)(?=\n##|\Z)", content, re.DOTALL)
            if m:
                overview = m.group(1).strip()
                # 去掉首尾 markdown 围栏残留
                return overview[:200]
        except Exception:
            pass
        return ""

    def _keyword_match(self, query: str, active: list[dict]) -> list[dict]:
        """关键词匹配兜底，返回多个候选（top 3）"""
        try:
            query_lower = query.lower()
            scored = []
            for t in active:
                score = 0
                tid = t.get("id", "").lower()
                tname = t.get("name", "").lower()
                tags = " ".join(t.get("tags", [])).lower()
                tid_tokens = set(tid.replace("-", " ").replace("_", " ").split())
                all_text = f"{tid} {tname} {tags}"

                if tid in query_lower or tname in query_lower:
                    score += 10
                for qword in query_lower.split():
                    if len(qword) >= 2 and qword in tid_tokens:
                        score += 3
                chinese_chars = [c for c in tname + tags if '一' <= c <= '鿿']
                if chinese_chars:
                    matched = sum(1 for c in set(chinese_chars) if c in query_lower)
                    if matched >= 2:
                        score += min(matched, 5)
                for tag in t.get("tags", []):
                    if tag.lower() in query_lower:
                        score += 3
                for word in query_lower.split():
                    if len(word) >= 2 and word in all_text:
                        score += 1
                # 门槛 >=4：中文单字重叠（+2）加单个词片段（+1）这类弱信号
                # 不足以证明匹配，此前 >0 会把无关工具塞进候选。
                if score >= 4:
                    scored.append((score, t))

            scored.sort(key=lambda x: x[0], reverse=True)
            result = [t for s, t in scored if s >= 4][:3]
            return result
        except Exception:
            pass
        return []

    def _build_system_with_context(self, workspace_tool_ids: list[str] = None) -> str:
        """构建 system prompt，只注入工具空间中的工具"""
        base = SYSTEM_PROMPT

        # 加载工具空间中的工具列表
        try:
            from core.resource.registry.tool_registry import ToolRegistry
            reg = ToolRegistry()
            tools = reg._read()
            active_tools = [t for t in tools if t.get("status") == "active"]
            if workspace_tool_ids:
                ws_set = set(workspace_tool_ids)
                active_tools = [t for t in active_tools if t.get("id") in ws_set]
            if active_tools:
                tool_lines = "\n".join(
                    f"- {t['id']}: {t['name']} (type: {t.get('type', 'function')})"
                    for t in active_tools
                )
                base += f"\n\n## 当前可用工具 ({len(active_tools)} 个)\n{tool_lines}"
            else:
                base += "\n\n## 当前可用工具\n（工具空间为空，请先在工具仓库中添加工具）"
        except Exception:
            pass

        return base

    async def _execute_tool(self, tool_info: dict, user_query: str, dataset_paths: list[str] = None, pre_params: dict = None) -> dict | None:
        """执行工具 — 通过 ToolExecutor 统一入口，与自动调试环境完全一致"""
        try:
            from core.executor.tool_executor import ToolExecutor

            tool_id = tool_info["id"]
            self._current_process = None  # 用于外部终止

            # 提取参数（优先用已提取的）
            params = pre_params if pre_params else await self._extract_params(user_query, tool_id, dataset_paths)

            return await ToolExecutor.execute(
                tool_id=tool_id,
                params=params,
                timeout=None,  # 无超时限制，直到用户手动停止
            )
        except Exception as e:
            return {"status": "failed", "message": f"工具执行异常: {str(e)[:300]}"}

    async def _extract_answer_params(self, answer: str, tool_id: str, param: dict) -> dict:
        """从用户回答中提取单个参数的值（带类型纠正与校验）。

        规则：
        - Agent 当前在问哪个参数，用户输入就作为该参数的值（以提问为准，不做意图猜测）。
        - 数字类参数（int/float）先从输入中「纠正提取」数值：
          如 "10" / "10个" / "返回10条" 均提取为 10；
        - 若无法提取到合法数值，抛 ValueError（由调用方捕获后重新引导用户），
          绝不能把非法值（如 int 参数塞入 "北京"）静默写入参数，
          否则会出现参数错位与后续工具报错。
        """
        val = answer.strip()
        ptype = (param.get("type", "string") or "").lower()
        name = param.get("name", "")

        if "int" in ptype or "float" in ptype:
            num = self._coerce_number(val, want_float="float" in ptype)
            if num is None:
                want = "整数" if "int" in ptype else "数字"
                raise ValueError(
                    f"参数 **{name}** 需要{ want }，但收到「{val}」。请重新输入 {name} 的值。"
                )
            return {name: num}

        # 文件/路径类参数：必须解析到真实存在的文件，绝不把整句话当路径填进去
        # （曾因把「用 XX Agent 帮我处理当前任务」整句当 file_path 传给工具而报错）
        if self._is_path_param(param):
            resolved = self._resolve_path_answer(val)
            if resolved is None:
                # 用户回答的是数据集名称/ id 而不是路径（工具把数据集目录做成
                # 路径参数时的常见答法）：按注册表解析成数据集目录
                ds_id = self._pick_dataset_id(val)
                if ds_id:
                    ds_dir = self._dataset_data_path(ds_id)
                    if ds_dir:
                        return {name: ds_dir}
                raise ValueError(
                    f"参数 **{name}** 需要一个文件路径，但在「{val}」里没有找到可用的文件。"
                    f"请提供完整路径或文件名（可先把文件拖入工作区间），或回复「取消」放弃本次执行。"
                )
            return {name: resolved}

        # 数据集 id：是逻辑标识，必须先从回答里剥掉「数据集就用…」这类前缀，
        # 再对着真实注册表校验。曾把整句「数据集就用 etf-90d-ohlcv」写进
        # dataset_id，工具随后报「数据集不存在」。
        if (name or "").lower() in ("dataset_id", "dataset", "data_id", "dir_id"):
            cand = self._pick_dataset_id(val)
            if cand:
                return {name: cand}
            known = self._known_dataset_ids()
            raise ValueError(
                f"没有在「{val}」里认出数据集。当前可用的数据集有："
                f"{'、'.join(known[:8]) or '（暂无）'}。请回复其中一个名称，或回复「取消」放弃本次执行。"
            )

        # 其他类型（string 等）：整句只作为值，但要剥掉明显的口语句式
        return {name: self._pick_scalar_value(val)}

    @staticmethod
    def _dataset_data_path(dataset_id: str) -> str | None:
        """数据集 id → 注册表里的真实目录（存在才返回，否则 None）。"""
        from pathlib import Path
        if not dataset_id:
            return None
        try:
            from core.resource.registry.data_registry import DataRegistry
            for e in DataRegistry()._read():
                if str(e.get("id")) == str(dataset_id) and e.get("status") == "active":
                    dp = str(e.get("data_path") or "")
                    if dp and Path(dp).is_dir():
                        return dp
        except Exception:
            pass
        return None

    @staticmethod
    def _pick_dataset_id(text: str) -> str | None:
        """从回答里挑出真实存在的数据集 id（先按 id 精确匹配，再按名称/id 包含关系）。"""
        s = (text or "").strip().strip("「」“”\"'")
        if not s:
            return None
        try:
            from core.resource.registry.data_registry import DataRegistry
            entries = DataRegistry()._read()
        except Exception:
            entries = []
        entries = [e for e in entries if e.get("id")]
        if not entries:
            return None
        ids = [str(e.get("id")) for e in entries]
        low = s.lower()
        # 1. id 精确匹配
        if low in (i.lower() for i in ids):
            return next(i for i in ids if i.lower() == low)
        # 2. 数据集名称精确匹配（用户只会说中文名，不会报 id）
        names = [(str(e.get("name") or ""), str(e.get("id"))) for e in entries if e.get("name")]
        if names:
            nl = s.lower()
            exact = [i for n, i in names if n.lower() == nl]
            if exact:
                return exact[0]
        # 3. 名称/id 的较长片段与回答互为包含（去掉空格与书名号后比较，
        #    兼容「ETF近90日 日线行情数据集」这类带空格写法）
        norm = re.sub(r"[\s「」《》“”\"'（）()]", "", s)
        cands = [i for n, i in names if len(re.sub(r"\s", "", n)) >= 3
                 and (re.sub(r"\s", "", n).lower() in norm.lower()
                      or norm.lower() in re.sub(r"\s", "", n).lower())]
        # id 也纳入包含匹配（长 id 才认，短 id 易误命中普通词）
        cands += [i for i in ids if len(i) >= 4 and i.lower() in low]
        cands = list(dict.fromkeys(cands))
        if len(cands) == 1:
            return cands[0]
        if cands:
            # 多个候选：取名称最长（最具体）的那个
            best = max(cands, key=lambda i: len(str(next((e.get("name") or "" for e in entries if e.get("id") == i), i))))
            return best
        return None

    @staticmethod
    def _known_dataset_ids() -> list[str]:
        try:
            from core.resource.registry.data_registry import DataRegistry
            return [str(e.get("id")) for e in DataRegistry()._read() if e.get("id")]
        except Exception:
            return []

    @staticmethod
    def _pick_scalar_value(text: str) -> str:
        """剥掉「参数就用 X」「列就用 X」「改成 X」这类口语句式前缀，只留值本身。"""
        s = (text or "").strip()
        s = re.sub(r"^[^，。,\s]{1,12}(就)?(用|是|填|改成|换成|设置为)\s*[:：]?\s*", "", s)
        s = re.sub(r"^(参数|值|改成|换成|设置为)\s*[:：]?\s*", "", s)
        return s.strip().strip("「」“”\"'") or (text or "").strip()

    @staticmethod
    def _is_path_param(param: dict) -> bool:
        desc = (param.get("desc", "") or "").lower()
        ptype = (param.get("type", "") or "").lower()
        name = (param.get("name", "") or "").lower()
        # 数据集 id / 目录 id 是逻辑标识不是文件路径：它的 desc 里常带
        # 「CSV/目录」等字样，按关键词会被误判成路径，随后解析不到真实文件
        # 被丢掉，工具反而报「参数不足」。这类参数原样透传给工具。
        if name in ("dataset_id", "dataset", "data_id", "dir_id"):
            return False
        return (
            any(kw in desc for kw in ("文件", "路径", "目录", "pdf", "图片", "图像", "csv", "文档"))
            or "path" in ptype or "file" in ptype or "file" in name or "_path" in name
        )

    @staticmethod
    def _resolve_path_answer(val: str) -> str | None:
        """从用户回答里解析出一个真实存在的文件路径，解析不到返回 None。"""
        from pathlib import Path
        s = val.strip().strip('"\'“”')
        if not s:
            return None
        project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        # 私有数据根按多用户规划取 SOTABAND_USER_HOME（factory 注入），不再硬编码
        _uh = os.environ.get("SOTABAND_USER_HOME", "").strip()
        data_roots = [project_root / "resources" / "data", project_root / "storage" / "workspace"]
        if _uh:
            data_roots.append(Path(_uh) / "resources-private" / "data")
        # 候选 1：整条回答就是路径
        # 候选 2：从文本中提取带扩展名的文件名 token
        cands = [s]
        cands += re.findall(r"[\w\-./\\一-鿿]+\.[A-Za-z0-9]{1,8}", val)
        for c in cands:
            c = c.strip().strip('"\'')
            if not c:
                continue
            p = Path(c)
            if p.is_absolute() and p.exists():
                return str(p)
            for root in [project_root, *data_roots]:
                fp = root / c
                if fp.exists():
                    return str(fp)
            # 在数据目录里按文件名递归搜（数据区文件量小，设遍历上限防意外大目录）
            for root in data_roots:
                if not root.exists():
                    continue
                for i, f in enumerate(root.rglob("*")):
                    if i > 2000:
                        break
                    if f.is_file() and f.name == Path(c).name:
                        return str(f)
        return None

    @staticmethod
    def _coerce_number(text: str, want_float: bool = False):
        """从文本中尽力提取一个数值（类型纠正）。

        支持 "10" / "10个" / "返回10条" / "10.5" / "1,000" 等；
        无法提取时返回 None。
        """
        import re as _re

        if text is None:
            return None
        s = str(text).strip().replace(",", "")
        # 直接可转换
        try:
            return float(s) if want_float else int(s)
        except (ValueError, TypeError):
            pass
        # 从文本中提取第一个数字
        m = _re.search(r"-?\d+(?:\.\d+)?", s)
        if not m:
            return None
        try:
            return float(m.group()) if want_float else int(float(m.group()))
        except (ValueError, TypeError):
            return None

    async def _check_missing_params(self, tool_id: str, current_params: dict, mode: str = None) -> list[str]:
        """返回缺失的必填参数名列表（支持按 mode 过滤）"""
        try:
            from core.resource.registry.tool_registry import ToolRegistry
            reg = ToolRegistry()
            entry = await reg.get(tool_id)
            if not entry:
                return []
            param_meta = entry.get("param_meta", [])
            missing = []
            for p in param_meta:
                # 排除 "mode" 分发参数（由模式选择流程处理，不参与普通参数引导）
                if p.get("name") == "mode":
                    continue
                # 多模式过滤：只检查属于当前 mode（或无 mode）的参数
                p_mode = p.get("mode")
                if mode and p_mode and p_mode != mode:
                    continue
                if p.get("required") and p.get("name") not in current_params:
                    missing.append(p)
            return missing
        except Exception:
            return []

    def _get_tool_modes(self, tool_id: str) -> list[dict]:
        """从 registry 读取工具的 modes 列表（空表示单模式工具）。

        若 registry.modes 为空，但 param_meta 存在名为 "mode" 的参数（hints 含多值），
        则自动从 mode 参数的 hints 推导出 modes（兼容「mode 参数分发」的老写法）。
        """
        try:
            from core.resource.registry.tool_registry import ToolRegistry
            reg = ToolRegistry()
            entry = None
            for e in reg._read():
                if e.get("id") == tool_id:
                    entry = e
                    break
            if not entry:
                return []
            modes = entry.get("modes", []) or []
            if modes:
                return modes

            # 兜底：从 mode 参数推导
            param_meta = entry.get("param_meta", []) or []
            mode_param = next((p for p in param_meta if p.get("name") == "mode"), None)
            if mode_param:
                hints = mode_param.get("hints", []) or []
                # 从 desc 或 hints 提取模式取值
                values = [h for h in hints if isinstance(h, str)]
                if len(values) >= 2:
                    # 尝试从 desc 提取中文名
                    return [{"id": v, "name": v, "desc": ""} for v in values]
            return []
        except Exception:
            return []

    def _resolve_mode_selection(self, answer: str, modes: list[dict]) -> dict | None:
        """识别用户对模式的选择（编号或名称/id），返回选中的模式或 None"""
        if not modes:
            return None
        a = answer.strip()
        a_lower = a.lower()

        # 1. 按编号选择：1 / 第1个 / 模式1
        num_matches = re.findall(r"\d+", a)
        if num_matches:
            idx = int(num_matches[0])
            if 1 <= idx <= len(modes):
                return modes[idx - 1]

        # 2. 按模式 id 精确匹配
        for m in modes:
            mid = m.get("id", "")
            if mid and mid.lower() in a_lower:
                return m

        # 3. 按模式名匹配
        for m in modes:
            name = m.get("name", "")
            if name and name in a:
                return m

        return None

    async def _extract_params_for_mode(
        self, query: str, tool_id: str, mode: str, dataset_paths: list[str] = None
    ) -> dict:
        """提取指定模式的参数：附件先填路径参数，其余交给 LLM 从话里抽取"""
        return await self._extract_params(query, tool_id, dataset_paths, mode)

    async def _extract_params(
        self, query: str, tool_id: str, dataset_paths: list[str] = None, mode: str = None
    ) -> dict:
        """从用户输入中尽力提取工具参数（附件填路径类参数，其余走 LLM 抽取）。

        曾经一度改成「只自动填附件路径、其他参数一律追问用户」（04035e3），
        结果「查询广州的天气」也要用户手报一次 city 才肯执行——城市名明明
        就在话里。参数抽取是工具自动化的核心，因此恢复抽取；抽不到的参数
        仍走原引导流程逐个追问，不会瞎填。
        """
        params = {}

        # 附件优先填文件/路径类参数（比让 LLM 猜路径可靠）
        path_param_names = self._get_path_params(tool_id, mode)
        if dataset_paths and path_param_names:
            for i, param_name in enumerate(path_param_names):
                if i < len(dataset_paths):
                    params[param_name] = dataset_paths[i]

        try:
            from core.resource.registry.tool_registry import ToolRegistry
            entry = await ToolRegistry().get(tool_id)
            if not entry:
                return params
            param_meta = [
                p for p in (entry.get("param_meta", []) or [])
                if p.get("name") != "mode"
                and not (mode and p.get("mode") and p.get("mode") != mode)
            ]
            if not param_meta:
                return params

            prompt = (
                "从用户输入中提取工具参数，只返回能从输入中明确得到的参数，"
                "不确定的不要编造。\n"
                f"参数定义: {json.dumps(param_meta, ensure_ascii=False)}\n"
                f"用户输入: \"{query}\"\n"
            )
            if params:
                prompt += f"已注入: {json.dumps(params, ensure_ascii=False)}\n"
            prompt += "只返回 JSON 对象（无则返回 {}）："

            resp = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=300,
            )
            text = (resp or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            extra = json.loads(text)
            if not isinstance(extra, dict):
                return params

            # 只采纳参数表里定义过的参数名，并按声明类型纠正
            valid = {p.get("name"): p for p in param_meta if p.get("name")}
            for name, val in extra.items():
                p = valid.get(name)
                if not p or val in (None, ""):
                    continue
                ptype = (p.get("type", "") or "").lower()
                if "int" in ptype or "float" in ptype:
                    num = self._coerce_number(str(val), want_float="float" in ptype)
                    if num is not None:
                        params[name] = num
                elif self._is_path_param(p):
                    resolved = self._resolve_path_answer(str(val))
                    if resolved:
                        params[name] = resolved
                    # 路径解析不到就不填，交给引导流程要用户提供真实文件
                else:
                    params[name] = val
        except Exception:
            pass

        return params

    def _get_path_params(self, tool_id: str, mode: str = None) -> list[str]:
        """通过 param_meta 的 desc 字段识别文件/路径类型参数（支持按 mode 过滤）"""
        try:
            import json
            from pathlib import Path
            project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
            reg_path = project_root / "resources" / "tools" / "registry.json"
            if not reg_path.exists():
                return []
            tools = json.loads(reg_path.read_text())
            entry = next((t for t in tools if t.get("id") == tool_id), None)
            if not entry:
                return []
            param_meta = entry.get("param_meta", [])
            if not param_meta:
                return []

            # 路径/文件 关键词
            PATH_KEYWORDS = [
                "文件", "路径", "目录", "文件夹",
                "pdf", "图片", "图像", "音频", "视频", "文档",
                "csv", "edf", "json", "xml", "txt",
                "png", "jpg", "jpeg", "tiff", "wav", "mp3",
            ]
            path_params = []
            for p in param_meta:
                # 多模式过滤：只处理属于当前 mode（或无 mode）的参数
                p_mode = p.get("mode")
                if mode and p_mode and p_mode != mode:
                    continue
                desc = (p.get("desc", "") or "").lower()
                ptype = (p.get("type", "") or "").lower()
                name = (p.get("name", "") or "").lower()
                # 数据集 id 是逻辑标识，不能被附件路径覆盖（见 _is_path_param）
                if name in ("dataset_id", "dataset", "data_id", "dir_id"):
                    continue
                # 主要通过 desc 判断
                is_path = any(kw in desc for kw in PATH_KEYWORDS)
                # 辅助：type 包含 path/file
                if not is_path and ("path" in ptype or "file" in ptype):
                    is_path = True
                # 辅助：name 包含 file（如 pdf_file, ref_file）
                if not is_path and "file" in name:
                    is_path = True
                if is_path:
                    path_params.append(p.get("name"))
            return path_params
        except Exception:
            return []

    def _resolve_dataset_paths(self, attachments: list) -> list[str]:
        """从附件中解析文件路径"""
        paths = []
        try:
            from pathlib import Path
            from core.resource.registry.data_registry import DataRegistry
            reg = DataRegistry()
            project_root = Path(__file__).resolve().parent.parent.parent.parent.parent

            for att in attachments:
                if hasattr(att, 'filePath'):
                    file_path = att.filePath
                    ds_id = att.id if hasattr(att, 'id') else ""
                elif isinstance(att, dict):
                    file_path = att.get("filePath", att.get("file_path", ""))
                    ds_id = att.get("id", "")
                else:
                    continue

                # 方式1: 工作区间文件 → 拼接绝对路径（无论存在与否都传递）
                if file_path:
                    full = Path(file_path)
                    if not full.is_absolute():
                        full = project_root / file_path
                    if full.exists():
                        paths.append(str(full))
                    elif Path(file_path).exists():
                        paths.append(file_path)
                    else:
                        # 文件不存在也传递，让工具自行判断
                        paths.append(str(full))
                    continue

                # 方式2: 已注册数据集 ID
                if ds_id:
                    for e in reg._read():
                        if e["id"] == ds_id and e.get("status") == "active":
                            data_path = e.get("data_path", "")
                            if data_path:
                                full = project_root / data_path
                                paths.append(str(full) if full.exists() else data_path)
        except Exception:
            pass
        return paths

    def _build_user_message(self, content: str, attachments: list, dataset_paths: list[str] = None):
        """构建带附件上下文的用户消息，返回 (文本, 小图片 URL 列表)。

        只给名字/大小对 LLM 没用——它看不到文件里写了什么。这里：
        - 文本类附件：把内容预览读出来拼进 prompt；
        - 小图片（≤2MB）：作为多模态内容随消息发送（OpenAI image_url）；
        - 大图片/二进制：只给元数据 + file_id，提示用工具分析。
        """
        if not attachments:
            return content, []

        image_urls: list[str] = []
        parts = [content, "", "附加文件:"]
        for att in attachments:
            # 兼容 Pydantic 模型和 dict
            if hasattr(att, 'fileName'):
                name = att.fileName
                fmt = getattr(att, 'format', '') or 'FILE'
                size = getattr(att, 'fileSize', 0)
                file_id = getattr(att, 'fileId', '')
            elif isinstance(att, dict):
                name = att.get("fileName", att.get("file_name", "unknown"))
                fmt = att.get("format", "") or 'FILE'
                size = att.get("fileSize", att.get("file_size", 0))
                file_id = att.get("fileId", "")
            else:
                continue

            # 大小/格式由**服务器**从登记表解析：前端只带 file_id，
            # 传上来的 fileSize 恒为 0（曾导致 LLM 误报"0 字节空文件"）
            meta = self._file_meta(file_id) if file_id else None
            if meta:
                name = name or meta.get("name") or "unknown"
                size = meta.get("size", size)
                if meta.get("name"):
                    ext = meta["name"].rsplit(".", 1)[-1] if "." in meta["name"] else ""
                    if ext:
                        fmt = ext

            if size > 1048576:
                size_str = f"{size / 1048576:.1f}MB"
            elif size > 1024:
                size_str = f"{size / 1024:.1f}KB"
            else:
                size_str = f"{size}B"
            parts.append(f"- {name} ({fmt.upper()}, {size_str})")

            if file_id:
                preview = self._read_file_preview(file_id)
                if preview:
                    parts.append(f"  内容:\n{preview}")
                elif fmt.lower() in ("png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff", "svg", "ico"):
                    url = self._inline_image_url(file_id)
                    if url:
                        # 小图片随消息发送（多模态），大图片只给元数据
                        image_urls.append(url)
                        parts.append(f"  （图片已随消息发送，可直接查看）")
                    else:
                        parts.append(f"  （图片较大，内容不直接发送，请用视觉工具分析）")
                else:
                    parts.append(f"  （二进制/大文件，内容无法直接展示，请用工具分析）")
                parts.append(f"  file_id: {file_id}（工具参数请用这个 id）")

        if dataset_paths:
            parts.append(f"\n数据集文件路径: {', '.join(dataset_paths)}")

        return "\n".join(parts), image_urls

    def _file_meta(self, file_id: str) -> dict | None:
        """从当前用户的文件登记表取真实元数据（大小/文件名）。

        前端只带 file_id，大小/格式一律以服务器登记为准——前端传的值
        不可信（拖拽构造的 attachment 里恒为 0，曾让 LLM 误判文件为空）。
        """
        try:
            from core import user_files
            return user_files.get(file_id)
        except Exception:
            return None

    # 小图片随消息发送的上限：2MB（base64 约 2.7MB，仍可控）
    _INLINE_IMAGE_MAX = 2 * 1024 * 1024

    def _inline_image_url(self, file_id: str) -> str | None:
        """小图片转 data URL 随消息发送；超过上限或非图片返回 None。"""
        try:
            from core import user_files
            path = user_files.resolve(file_id)
            if path.stat().st_size > self._INLINE_IMAGE_MAX:
                return None
            import base64
            import mimetypes
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            mime = mimetypes.guess_type(str(path))[0] or "image/png"
            return f"data:{mime};base64,{data}"
        except Exception:
            return None

    def _read_file_preview(self, file_id: str, limit: int = 100_000, head: int = 10_000):
        """读取文本文件内容预览（供 LLM 理解文件）；非文本返回 None。

        - 小文件（≤1MB）：读全文，超 limit 截断；
        - 大文件（>1MB）：只读头部 head 字节，让 LLM 看到表头/结构，
          完整内容交给工具处理（避免撑爆上下文）。
        只读用户登记表里的文件（file_id → 当前用户目录），不碰任意路径。
        """
        try:
            from core import user_files
            path = user_files.resolve(file_id)
            text_exts = {
                ".md", ".txt", ".csv", ".json", ".jsonl", ".log", ".py",
                ".js", ".ts", ".yaml", ".yml", ".html", ".xml", ".sql",
                ".sh", ".rst", ".ini", ".toml",
            }
            if path.suffix.lower() not in text_exts:
                return None
            size = path.stat().st_size
            if size > 1024 * 1024:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read(head)
                return f"{text}\n…（大文件 {size // 1024}KB，仅显示前 {head // 1024}KB，完整内容请用工具处理）"
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text) > limit:
                text = text[:limit] + "\n…（内容过长，已截断）"
            return text
        except Exception:
            return None


# 全局单例，开机时初始化
interactive_agent = InteractiveAgent()
