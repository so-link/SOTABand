"""交互 Agent — 系统默认入口，开机自启，处理用户对话"""

import json
import re
import time
from typing import AsyncGenerator
import os
import subprocess
import sys
import tempfile

from core.agent.base import BaseAgent, AgentContext, AgentSpec, AgentRole
from core.llm.client import create_llm_client, LLMClient


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
- 无匹配工具: 回复必须**以 TOOL_NEEDED: 开头**（不要有任何前缀文字），然后简要描述需要创建什么工具（中文描述），
  系统会自动跳转到工具编辑器并预填需求。例如: "TOOL_NEEDED: EEG带通滤波器，输入EDF文件，支持可配置的低频和高频截止频率"

## 平台工具（重要）
- 你可以直接调用平台工具查询系统真实状态（数据集/工具/模型/Agent 清单、定时任务、目录浏览、数据预览等）。需要这类信息时**必须调用工具**，禁止凭空猜测或编造
- 涉及修改平台资源的工具会先向用户发出确认卡片，工具返回 status=pending 时，你只需告知用户在卡片上确认，**绝不能声称操作已执行**
- 工具返回的 status=error 表示未执行，把错误原因用自然语言告诉用户或修正参数后重试

## 注意事项
- 回复简洁清晰，逐步引导用户
- 如用户提及具体数据文件，引用文件名
- 当用户描述数据处理需求但当前没有匹配工具时，必须使用 TOOL_NEEDED: 格式，且 TOOL_NEEDED: 必须在回复最开头
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
                async for ev in self._dispatch_to_agent(selected, pending_agent["request"], session_key):
                    yield ev
                return
            if len(content) <= 60:
                merged = (
                    content if self._resolve_agent_selection(content, [pending_agent["agent"]])
                    else f"{pending_agent['request']}，{content}"
                )
                async for ev in self._dispatch_to_agent(pending_agent["agent"], merged, session_key):
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
            pending_confirm = self._pending_confirm.pop(session_key)
            selected = self._resolve_tool_selection(content, pending_confirm["candidates"])
            if selected is None:
                self._pending_confirm[session_key] = pending_confirm
                yield {"event": "content", "data": {"text": "请回复编号或工具名称来选择要使用的工具。"}}
                return
            matched_tool = selected
            # 本次回复用于「选择工具」，不可再用作模式提示依据，
            # 否则用户回复的编号（如 "1"）会被误判为模式编号，导致跳过模式选择。
            tool_from_confirm = True

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
            agent_hit = await self._match_agent(content)
            if agent_hit:
                candidates = agent_hit if isinstance(agent_hit, list) else [agent_hit]
                if len(candidates) > 1:
                    # 多个 Agent 名称都可能匹配 → 请用户确认，不擅自选一个
                    infos = [{"id": a.get("id", ""), "name": a.get("name", ""), "index": i}
                             for i, a in enumerate(candidates, 1)]
                    yield {
                        "event": "card",
                        "data": {
                            "type": "tool-confirm",
                            "title": "请确认要使用的 Agent",
                            "summary": f"找到 {len(candidates)} 个可能匹配的 Agent，请选择其中一个",
                            "data": {"candidates": infos},
                        },
                    }
                    options = "、".join(f"{i}.{a['name']}" for i, a in enumerate(infos, 1))
                    yield {
                        "event": "content",
                        "data": {"text": f"检测到多个可能匹配的 Agent，请选择：{options}。回复编号或 Agent 名称即可。"},
                    }
                    self._pending_agents[session_key] = {"candidates": candidates, "request": content}
                    return
                async for ev in self._dispatch_to_agent(candidates[0], content, session_key):
                    yield ev
                return
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
            self._pending_confirm[session_key] = {"candidates": matched_tools}
            # 构建候选列表展示
            candidates_info = []
            for i, t in enumerate(matched_tools, 1):
                name = t.get("name", t.get("id", ""))
                candidates_info.append({"id": t.get("id", ""), "name": name, "index": i})
            # 发确认卡片
            yield {
                "event": "card",
                "data": {
                    "type": "tool-confirm",
                    "title": "请确认要使用的工具",
                    "summary": f"找到 {len(matched_tools)} 个可能匹配的工具，请选择其中一个",
                    "data": {"candidates": candidates_info},
                },
            }
            # 文本引导
            options_text = "、".join(f"{i}.{t['name']}" for i, t in enumerate(matched_tools, 1))
            yield {
                "event": "content",
                "data": {"text": f"检测到多个可能匹配的工具，请选择：{options_text}。回复编号或工具名称即可。"},
            }
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
            options = "、".join(f"「{t['name']}」" for t in infos)
            yield {
                "event": "content",
                "data": {"text": f"当前工具空间里没有匹配的工具，不过工具仓库里已经有 {options}，"
                                 f"点上方卡片加入工具空间，我马上就能用它处理。"},
            }
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
            # 同时用自然语言告知用户
            yield {
                "event": "content",
                "data": {"text": f"当前工具空间中没有匹配的工具，我理解你需要创建一个新工具：{need_create_tool_desc}。点击下方卡片即可跳转到工具编辑器。"},
            }
            return

        if matched_tool:
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
            if skip_param_extract:
                params = skip_param_extract
            else:
                params = await self._extract_params_for_mode(
                    content, matched_tool["id"], selected_mode_id, dataset_paths
                )
            missing = await self._check_missing_params(matched_tool["id"], params, selected_mode_id)
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
            # 执行提示：工具可能耗时较长（如模型训练），先告知用户正在执行，
            # 避免界面长时间无反馈被误认为卡死。
            yield {"event": "content",
                   "data": {"text": f"正在执行 **{matched_tool['name']}**，请稍候……（耗时任务可能需要较长时间，可在需要时手动停止）"}}
            tool_result = await self._execute_tool(matched_tool, content, dataset_paths, params)
            if tool_result:
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

                yield {
                    "event": "card",
                    "data": {
                        "type": "result-summary",
                        "title": f"工具执行: {matched_tool['name']}",
                        "summary": tool_result.get("message", json.dumps(tool_result, ensure_ascii=False, default=str)[:200]),
                        "data": {
                            "tool_id": matched_tool["id"],
                            "result": tool_result,
                            "registered_dataset_id": registered_dataset_id,
                        },
                    },
                }
                # 图片/表格/文件/失败 → 直接结束，不调 LLM（文件由前端渲染，如音频播放器、下载链接等）
                if tool_result.get("output_format") in ("image", "table", "file") or tool_result.get("status") == "failed":
                    return
            else:
                tool_failed = True
                yield {
                    "event": "card",
                    "data": {
                        "type": "result-summary",
                        "title": f"工具执行失败: {matched_tool['name']}",
                        "summary": "工具调用失败，请检查工具代码或参数",
                        "data": {"tool_id": matched_tool["id"], "error": "execution_failed"},
                    },
                }

        # 工具执行失败 → 不调用 LLM，直接返回
        if tool_failed:
            return

        user_msg = self._build_user_message(content, attachments, dataset_paths)
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
                f"请基于此真实结果回复用户，不要自行推测或计算。]"
            )
        history.append({"role": "user", "content": user_msg})

        # 裁剪历史
        max_history = self.config.get("max_history", 20)
        if len(history) > max_history * 2 + 1:
            history = [history[0]] + history[-(max_history * 2):]

        try:
            full_response = ""
            platform_tools = self._platform_tool_schemas()

            if not platform_tools:
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
                        tools=platform_tools,
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
                        result = await self._run_platform_tool(tc, ctx, session_key)
                        if result.get("status") == "pending" and result.get("confirm"):
                            # 写操作 → 下发确认卡片，本轮不执行
                            yield {
                                "event": "card",
                                "data": {
                                    "type": "platform-confirm",
                                    "title": "请确认平台操作",
                                    "summary": result["confirm"].get("summary", ""),
                                    "data": result["confirm"],
                                },
                            }
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

            # 检查是否需要创建工具（宽松匹配，兼容前缀/中英文冒号）
            m_tool_needed = re.search(r"TOOL_NEEDED[:：]\s*(.+)", full_response, re.DOTALL)
            if m_tool_needed:
                tool_desc = m_tool_needed.group(1).strip()
                yield {
                    "event": "card",
                    "data": {
                        "type": "create-tool",
                        "title": "需要创建新工具",
                        "summary": tool_desc,
                        "data": {"description": tool_desc},
                    },
                }

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
            "请用简洁的中文向用户解读这次操作的结果：做了什么、关键数据或 ID、"
            "下一步建议。数据类结果提炼要点，不要照抄 JSON。"
            "不要向用户提及本条系统回执本身。"
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

    async def _match_agent(self, query: str):
        """判断用户是否明确指定了某个已注册 Agent。

        返回单个 Agent 条目 dict、多个候选 list，或 None（不是 Agent 派发场景）。
        仅做名称级强匹配（名称/id 显式出现在句中出现才命中），
        不做模糊语义匹配——错派 Agent 的代价远高于错配工具。
        """
        try:
            from core.resource.registry.agent_registry import AgentRegistry
            entries = AgentRegistry()._read()
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
        if not hits:
            return None
        if len(hits) == 1:
            return hits[0]
        return hits

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

    async def _dispatch_to_agent(self, agent_entry: dict, request: str, session_key: str):
        """把用户请求转发给指定 Agent（经主进程 RPC），聚合结果后返回。

        RPC 语义（见 core/agent/entrypoint.py）：参数平铺为 execute kwargs，
        content 即用户原话；成功聚合为 {"text": ...}。
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

        yield {"event": "content",
               "data": {"text": f"正在转交给 **{name}** 处理，请稍候……（任务可能需要较长时间）"}}

        resp = await self._bus.call_agent(aid, "execute", {"content": request}, timeout=300)

        if isinstance(resp, dict) and "未运行" in str(resp.get("error", "")):
            # 锁定该 Agent：用户下一条短回复会补全请求后自动重试
            self._pending_agents[session_key] = {"agent": agent_entry, "request": request}
            # 直接经平台网关发起 start_agent（写操作 → 确认卡片），
            # 用户在卡片上确认即可由系统拉起，无需去 Agent 空间手动启动。
            if self._bus is not None:
                r = await self._bus.platform_call(
                    "start_agent", {"agent_id": aid},
                    session_id=session_key, user_id="default")
                if isinstance(r, dict) and r.get("status") == "pending" and r.get("confirm"):
                    yield {
                        "event": "card",
                        "data": {
                            "type": "platform-confirm",
                            "title": "请确认平台操作",
                            "summary": r["confirm"].get("summary", ""),
                            "data": r["confirm"],
                        },
                    }
                    yield {"event": "content",
                           "data": {"text": f"**{name}** 当前未运行。在上方卡片点「确认执行」我就启动它；"
                                             "启动后回复「继续」，我会重新提交任务。"}}
                    return
            yield {"event": "content",
                   "data": {"text": f"**{name}** 当前未运行。请到 Agent 空间启动它；启动后直接回复我（如「继续」），我会重新提交任务。"}}
            return

        session = self._sessions.setdefault(session_key, [])
        if not session:
            # 与其他轮次一致：历史首条恒为 system，裁剪逻辑依赖该约定
            session.append({"role": "system", "content": self._build_system_with_context([])})
        if isinstance(resp, dict) and resp.get("ok"):
            result = resp.get("result") or {}
            text = result.get("text") if isinstance(result, dict) else json.dumps(result, ensure_ascii=False, default=str)
            text = (text or "").strip() or "（该 Agent 未返回文本结果）"
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
            summary = f"Agent「{name}」已完成任务，结果如下：\n{text[:3000]}\n请用一两句话向用户小结以上结果。"
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
            summary = f"Agent「{name}」执行失败：{err}。请向用户说明失败原因并给出建议。"

        # 用 LLM 对结果做简短小结（直接走 content 流式，不重复输出全文）
        try:
            hist = [m for m in session if m["role"] != "system"][-12:]
            msgs = [{"role": "system", "content": "你是交互助手。基于最近的 Agent 执行结果，用简洁中文向用户小结，不要编造未提供的信息。"}] + hist
            async for token in self.llm.chat_stream(messages=msgs, temperature=0.2, max_tokens=800):
                yield {"event": "content", "data": {"text": token}}
        except Exception:
            pass

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
        # 0. 明确「创建工具」意图 → 直接进入创建流程，不做工具匹配
        create_intent = self._detect_create_intent(query)
        if create_intent:
            return [], create_intent, []

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
        """检测用户是否明确表达「创建工具」意图，返回工具描述（或 None）"""
        # 明确创建意图的关键词
        CREATE_KEYWORDS = [
            "创建工具", "新建工具", "生成工具", "做一个工具", "做个工具",
            "创建新工具", "新建一个工具", "开发一个工具", "开发个工具",
            "帮我做工具", "帮我写工具", "我要工具", "写个工具", "写一个工具",
            "create tool", "create a tool", "new tool", "make a tool",
            "我要创建", "我想创建", "帮我创建",
        ]
        q = query.strip()
        q_lower = q.lower()
        has_create_intent = any(kw in q for kw in CREATE_KEYWORDS) or any(kw in q_lower for kw in CREATE_KEYWORDS)

        if not has_create_intent:
            return None

        # 提取工具描述：去掉「创建工具」等意图词，剩下的作为描述
        desc = q
        for kw in CREATE_KEYWORDS:
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

    async def _llm_semantic_match(self, query: str, tools: list[dict]) -> tuple[list[dict], str | None]:
        """用 LLM 做语义匹配。

        返回 (匹配到的工具候选列表, 需要创建的工具描述 或 None)
        """
        try:
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
                raise ValueError(
                    f"参数 **{name}** 需要一个文件路径，但在「{val}」里没有找到可用的文件。"
                    f"请提供完整路径或文件名（可先把文件拖入工作区间），或回复「取消」放弃本次执行。"
                )
            return {name: resolved}

        # 其他类型（string 等）：直接使用输入
        return {name: val}

    @staticmethod
    def _is_path_param(param: dict) -> bool:
        desc = (param.get("desc", "") or "").lower()
        ptype = (param.get("type", "") or "").lower()
        name = (param.get("name", "") or "").lower()
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
        data_roots = [project_root / "resources" / "data", project_root / "storage" / "workspace", Path("/root/resources-private/data")]
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
        """提取指定模式的参数：只自动填充该模式的路径参数，其余交由引导"""
        from pathlib import Path as _Path

        params = {}
        if not mode:
            return await self._extract_params(query, tool_id, dataset_paths)

        # 只自动填充该模式下的文件/路径类参数（来自附件）
        path_param_names = self._get_path_params(tool_id, mode)
        if dataset_paths and path_param_names:
            for i, param_name in enumerate(path_param_names):
                if i < len(dataset_paths):
                    params[param_name] = dataset_paths[i]
        return params

    async def _extract_params(self, query: str, tool_id: str, dataset_paths: list[str] = None) -> dict:
        """只自动填充文件路径参数，其他参数不自动提取，交由后续引导流程让用户输入"""
        from pathlib import Path as _Path

        params = {}

        # 只自动填充文件/路径类参数（来自附件）
        path_param_names = self._get_path_params(tool_id)
        if dataset_paths and path_param_names:
            for i, param_name in enumerate(path_param_names):
                if i < len(dataset_paths):
                    params[param_name] = dataset_paths[i]

        # 不再调用 LLM 自动提取其他参数
        # 未填写的参数会触发 _check_missing_params → 引导用户逐一输入
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

    def _build_user_message(self, content: str, attachments: list, dataset_paths: list[str] = None) -> str:
        """构建带附件上下文的用户消息"""
        if not attachments:
            return content

        parts = [content, "", "附加文件:"]
        for att in attachments:
            # 兼容 Pydantic 模型和 dict
            if hasattr(att, 'fileName'):
                name = att.fileName
                fmt = getattr(att, 'format', 'unknown')
                size = getattr(att, 'fileSize', 0)
            elif isinstance(att, dict):
                name = att.get("fileName", att.get("file_name", "unknown"))
                fmt = att.get("format", "unknown")
                size = att.get("fileSize", att.get("file_size", 0))
            else:
                continue

            if size > 1048576:
                size_str = f"{size / 1048576:.1f}MB"
            elif size > 1024:
                size_str = f"{size / 1024:.1f}KB"
            else:
                size_str = f"{size}B"
            parts.append(f"- {name} ({fmt.upper()}, {size_str})")

        if dataset_paths:
            parts.append(f"\n数据集文件路径: {', '.join(dataset_paths)}")

        return "\n".join(parts)


# 全局单例，开机时初始化
interactive_agent = InteractiveAgent()
