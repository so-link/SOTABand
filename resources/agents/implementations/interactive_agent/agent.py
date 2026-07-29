"""交互 Agent — 系统默认入口，开机自启，处理用户对话"""

import json
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
- 无匹配工具: 回复以 **TOOL_NEEDED:** 开头，然后简要描述需要创建什么工具（中文描述），
  系统会自动跳转到工具编辑器并预填需求。例如: "TOOL_NEEDED: EEG带通滤波器，输入EDF文件，支持可配置的低频和高频截止频率"

## 注意事项
- 回复简洁清晰，逐步引导用户
- 如用户提及具体数据文件，引用文件名
- 当用户描述数据处理需求但当前没有匹配工具时，必须使用 TOOL_NEEDED: 格式
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

        if not in_param_collection:
            matched_tool = self._pre_match_tool(content, workspace_tool_ids or [])

        history = self._sessions.get(session_key, [])
        if not history:
            system_content = self._build_system_with_context(workspace_tool_ids or [])
            history = [{"role": "system", "content": system_content}]

        # 如果预匹配到工具 → 先执行工具，用真实结果驱动 LLM 回复
        tool_result = None
        tool_failed = False

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
            async for token in self.llm.chat_stream(
                messages=history,
                temperature=0.1,
                max_tokens=self.config.get("max_tokens", 4096),
            ):
                full_response += token
                yield {"event": "content", "data": {"text": token}}

            # 检查是否需要创建工具
            if full_response.startswith("TOOL_NEEDED:"):
                tool_desc = full_response[len("TOOL_NEEDED:"):].strip()
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

    def _pre_match_tool(self, query: str, workspace_tool_ids: list[str] = None) -> dict | None:
        """预匹配：只在工具空间中的工具里做关键词匹配"""
        try:
            from core.resource.registry.tool_registry import ToolRegistry
            reg = ToolRegistry()
            tools = reg._read()
            active = [t for t in tools if t.get("status") == "active"]
            # 如果指定了工具空间范围，只匹配工具空间中的工具
            if workspace_tool_ids:
                ws_set = set(workspace_tool_ids)
                active = [t for t in active if t.get("id") in ws_set]
            query_lower = query.lower()

            # 按匹配度打分
            scored = []
            for t in active:
                score = 0
                tid = t.get("id", "").lower()
                tname = t.get("name", "").lower()
                tags = " ".join(t.get("tags", [])).lower()
                tid_tokens = set(tid.replace("-", " ").replace("_", " ").split())
                all_text = f"{tid} {tname} {tags}"

                # 工具名/ID 完全出现在查询中
                if tid in query_lower or tname in query_lower:
                    score += 10

                # 英文: 连字符拆分的 token 匹配
                for qword in query_lower.split():
                    if len(qword) >= 2 and qword in tid_tokens:
                        score += 3

                # 中文: 字符级匹配（工具名/标签中的汉字出现在查询中）
                chinese_chars = [c for c in tname + tags if '一' <= c <= '鿿']
                if chinese_chars:
                    matched = sum(1 for c in set(chinese_chars) if c in query_lower)
                    if matched >= 2:
                        score += min(matched, 5)  # 最多+5

                # 标签完全匹配
                for tag in t.get("tags", []):
                    if tag.lower() in query_lower:
                        score += 3

                # 单词匹配
                for word in query_lower.split():
                    if len(word) >= 2 and word in all_text:
                        score += 1

                if score > 0:
                    scored.append((score, t))

            scored.sort(key=lambda x: x[0], reverse=True)
            if scored and scored[0][0] >= 2:
                return scored[0][1]
        except Exception:
            pass
        return None

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
