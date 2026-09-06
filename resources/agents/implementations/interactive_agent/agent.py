"""交互 Agent — 系统默认入口，开机自启，处理用户对话"""

import json
import re
import time
from typing import AsyncGenerator

from core.agent.base import BaseAgent, AgentContext, AgentSpec, AgentRole
from core.llm.client import create_llm_client, LLMClient


SYSTEM_PROMPT = """你是 SOTABand Engine 的交互 Agent，一个多智能体多模态智能处理引擎的对话入口。

## 你的职责
1. 理解用户的自然语言意图
2. 分析用户的需求类型（简单数据处理 / 复杂多步任务编排）
3. 引导用户完成任务

## 当前系统能力
- 工具空间: 用户可以创建和使用各种数据处理工具
- Agent 空间: 数据加载、异常检测、编排等专业 Agent
- 数据空间: 支持 EEG (EDF)、图像 (PNG/TIFF)、表格 (CSV)、文本等多模态数据
- 模型空间: 支持 LLM、ViT、3D-CNN、时序模型
- 探索式能力增长: 当没有匹配工具时，可自动生成代码，经用户核验后注册为本地工具

## 响应格式
- 普通对话: 用自然语言回复
- 简单数据处理需求: 分析需求 → 匹配工具 → 建议执行方案
- 复杂任务: 分析步骤 → 建议使用编排模式
- 无匹配工具: 回复必须**以 TOOL_NEEDED: 开头**（不要有任何前缀文字），然后简要描述需要创建什么工具（中文描述），
  系统会自动跳转到工具编辑器并预填需求。例如: "TOOL_NEEDED: EEG带通滤波器，输入EDF文件，支持可配置的低频和高频截止频率"

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

    async def execute(
        self, ctx: AgentContext, **kwargs
    ) -> AsyncGenerator[dict, None]:
        """处理用户输入，调用 DeepSeek v4，流式返回"""
        content = kwargs.get("content", "")
        attachments = kwargs.get("attachments", [])
        workspace_tool_ids = kwargs.get("workspace_tool_ids", [])

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

        # 如果正在等待用户确认工具选择 → 识别用户的选择
        pending_confirm = self._pending_confirm.get(session_key)
        if pending_confirm:
            selected = self._resolve_tool_selection(content, pending_confirm["candidates"])
            if selected is None:
                # 无法识别选择 → 重新列出候选，请用户明确选择
                self._pending_confirm[session_key] = pending_confirm
                yield {"event": "content", "data": {"text": "请回复编号或工具名称来选择要使用的工具。"}}
                return
            self._pending_confirm.pop(session_key, None)
            # 选定工具 → 继续走参数引导
            matched_tool = selected
            skip_param_extract = {}
            in_param_collection = False
        else:
            matched_tool = None
            skip_param_extract = {}

        # 如果有 pending 的参数收集 → 只处理参数回答，不做工具匹配
        pending = self._pending_calls.get(session_key)
        in_param_collection = pending is not None
        if pending:
            # 用户的回应只用于填写参数，不触发新工具匹配
            answer_params = await self._extract_answer_params(
                content, pending["tool_id"], pending["missing"][0]
            )
            pending["params"].update(answer_params)
            pending["missing"] = await self._check_missing_params(pending["tool_id"], pending["params"])
            if pending["missing"]:
                p = pending["missing"][0]
                yield {"event": "content", "data": {"text": f"请提供 **{p['name']}** ({p.get('desc', '')}) 的值。"}}
                return
            # 参数齐备 → 执行之前匹配的工具
            self._pending_calls.pop(session_key, None)
            matched_tool = {"id": pending["tool_id"], "name": pending["tool_name"]}
            skip_param_extract = pending["params"]

        need_create_tool_desc = None
        matched_tools: list[dict] = []
        if not in_param_collection and not matched_tool:
            matched_tools, need_create_tool_desc = await self._pre_match_tool(content, workspace_tool_ids or [])

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
            # 参数（pending 已收集或重新提取）
            if 'skip_param_extract' in dir() and skip_param_extract:
                params = skip_param_extract
            else:
                params = await self._extract_params(content, matched_tool["id"], dataset_paths)
            missing = await self._check_missing_params(matched_tool["id"], params)
            if missing:
                # 参数不足 → 逐一引导，存储 pending 状态
                self._pending_calls[session_key] = {
                    "tool_id": matched_tool["id"],
                    "tool_name": matched_tool["name"],
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

                card_data: dict = {
                    "type": "result-summary",
                    "title": f"工具执行: {matched_tool['name']}",
                    "summary": tool_result.get("message", json.dumps(tool_result, ensure_ascii=False, default=str)[:200]),
                    "data": {
                        "tool_id": matched_tool["id"],
                        "result": tool_result,
                        "registered_dataset_id": registered_dataset_id,
                    },
                }
                # 如果是数据集注册操作，附加信息供前端自动加入数据空间
                if registered_dataset_id:
                    tool_data = tool_result.get("data", {})
                    if isinstance(tool_data, dict):
                        card_data["data"]["_action"] = tool_data.get("_action", "register_dataset")
                        card_data["data"]["name"] = tool_data.get("name", registered_dataset_id)
                        card_data["data"]["tags"] = tool_data.get("tags", [])
                    else:
                        card_data["data"]["_action"] = "register_dataset"
                        card_data["data"]["name"] = registered_dataset_id
                        card_data["data"]["tags"] = []
                    card_data["data"]["dataset_id"] = registered_dataset_id
                yield {
                    "event": "card",
                    "data": card_data,
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

            # 保存对话历史
            history.append({"role": "assistant", "content": full_response})
            self._sessions[session_key] = history

        except Exception as e:
            yield {
                "event": "error",
                "data": {"code": "llm_error", "message": str(e)},
            }

    async def _pre_match_tool(self, query: str, workspace_tool_ids: list[str] = None) -> tuple[list[dict], str | None]:
        """预匹配：先用 LLM 语义匹配，失败时回退到关键词匹配。

        返回 (匹配到的工具候选列表, 需要创建的工具描述 或 None)
        """
        # 0. 明确「创建工具」意图 → 直接进入创建流程，不做工具匹配
        create_intent = self._detect_create_intent(query)
        if create_intent:
            return [], create_intent

        # 收集活跃工具（限定工具空间范围）
        try:
            from core.resource.registry.tool_registry import ToolRegistry
            reg = ToolRegistry()
            tools = reg._read()
            active = [t for t in tools if t.get("status") == "active"]
            if workspace_tool_ids:
                ws_set = set(workspace_tool_ids)
                active = [t for t in active if t.get("id") in ws_set]
        except Exception:
            return [], None

        # 工具空间为空 → 判断是否需要创建工具
        if not active:
            need_create = await self._llm_check_create_needed(query)
            return [], need_create

        # 1. 优先：LLM 语义匹配（返回多个候选，同时判断是否需创建新工具）
        llm_candidates, llm_create = await self._llm_semantic_match(query, active)
        if llm_candidates:
            return llm_candidates, None
        if llm_create:
            return [], llm_create

        # 2. 回退：关键词匹配（返回多个候选）
        kw_candidates = self._keyword_match(query, active)
        if kw_candidates:
            return kw_candidates, None

        return [], None

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
- 如果有一个或多个工具能匹配用户需求，返回 matched_tool_ids 列表（按匹配度从高到低，最多 3 个）
- 如果没有任何工具匹配，且用户的需求是「数据处理/文件处理/计算/分析」类（需要用工具完成），返回 need_new_tool=true，并给出新工具的简短中文描述
- 如果用户只是普通闲聊/咨询，不需要工具，返回 need_new_tool=false

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
                if score > 0:
                    scored.append((score, t))

            scored.sort(key=lambda x: x[0], reverse=True)
            # 返回 top 3 且分数 >= 2 的候选
            result = [t for s, t in scored if s >= 2][:3]
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
        """从用户回答中提取单个参数的值 — 直接使用用户输入作为参数值"""
        val = answer.strip()
        # 类型转换：如果参数类型是 int/float，尝试转换
        ptype = param.get("type", "string")
        if "int" in ptype:
            try:
                val = int(val)
            except (ValueError, TypeError):
                pass
        elif "float" in ptype:
            try:
                val = float(val)
            except (ValueError, TypeError):
                pass
        return {param["name"]: val}

    async def _check_missing_params(self, tool_id: str, current_params: dict) -> list[str]:
        """返回缺失的必填参数名列表"""
        try:
            from core.resource.registry.tool_registry import ToolRegistry
            reg = ToolRegistry()
            entry = await reg.get(tool_id)
            if not entry:
                return []
            param_meta = entry.get("param_meta", [])
            missing = []
            for p in param_meta:
                if p.get("required") and p.get("name") not in current_params:
                    missing.append(p)
            return missing
        except Exception:
            return []

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

    def _get_path_params(self, tool_id: str) -> list[str]:
        """通过 param_meta 的 desc 字段识别文件/路径类型参数"""
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
