"""交互 Agent — 系统默认入口，开机自启，处理用户对话"""

import json
import time
from typing import AsyncGenerator

from core.agent.base import BaseAgent, AgentContext, AgentSpec, AgentRole
from core.llm.client import create_llm_client, LLMClient


SYSTEM_PROMPT = """你是 MAIA Engine 的交互 Agent，一个多智能体多模态智能处理引擎的对话入口。

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
            description="MAIA Engine 主交互入口，处理用户对话",
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

    async def execute(
        self, ctx: AgentContext, **kwargs
    ) -> AsyncGenerator[dict, None]:
        """处理用户输入，调用 DeepSeek v4，流式返回"""
        content = kwargs.get("content", "")
        attachments = kwargs.get("attachments", [])

        if not content.strip():
            yield {"event": "done", "data": {"messageId": ctx.session_id}}
            return

        # 解析附件中的数据集路径
        dataset_paths = self._resolve_dataset_paths(attachments)

        # 预匹配：检查用户查询是否直接命中了已有工具
        matched_tool = self._pre_match_tool(content)

        session_key = ctx.session_id or "default"
        history = self._sessions.get(session_key, [])
        if not history:
            system_content = self._build_system_with_context()
            history = [{"role": "system", "content": system_content}]

        # 如果预匹配到工具 → 先执行工具，用真实结果驱动 LLM 回复
        tool_result = None
        tool_failed = False
        if matched_tool:
            tool_result = await self._execute_tool(matched_tool, content, dataset_paths)
            if tool_result:
                yield {
                    "event": "card",
                    "data": {
                        "type": "result-summary",
                        "title": f"工具执行: {matched_tool['name']}",
                        "summary": json.dumps(tool_result, ensure_ascii=False, default=str)[:500],
                        "data": {"tool_id": matched_tool["id"], "result": tool_result},
                    },
                }
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
            yield {"event": "done", "data": {"messageId": f"msg-{int(time.time() * 1000)}"}}
            return

        user_msg = self._build_user_message(content, attachments, dataset_paths)
        # 工具结果作为上下文注入（仅成功时）
        if tool_result:
            user_msg += (
                f"\n\n[系统提示: 工具 {matched_tool['id']} 已自动执行，"
                f"实际结果为: {json.dumps(tool_result, ensure_ascii=False, default=str)[:800]}。"
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
                temperature=kwargs.get("temperature", self.config.get("temperature", 0.7)),
                max_tokens=kwargs.get("max_tokens", self.config.get("max_tokens", 4096)),
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

            yield {
                "event": "done",
                "data": {"messageId": f"msg-{int(time.time() * 1000)}"},
            }

        except Exception as e:
            yield {
                "event": "error",
                "data": {"code": "llm_error", "message": str(e)},
            }
            yield {
                "event": "done",
                "data": {"messageId": f"msg-{int(time.time() * 1000)}"},
            }

    def _pre_match_tool(self, query: str) -> dict | None:
        """预匹配：关键词命中工具名/标签时直接返回匹配工具"""
        try:
            from core.resource.registry.tool_registry import ToolRegistry
            reg = ToolRegistry()
            tools = reg._read()
            active = [t for t in tools if t.get("status") == "active"]
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

    def _build_system_with_context(self) -> str:
        """构建 system prompt，动态注入可用工具和数据信息"""
        base = SYSTEM_PROMPT

        # 加载可用工具列表
        try:
            from core.resource.registry.tool_registry import ToolRegistry
            reg = ToolRegistry()
            tools = reg._read()
            active_tools = [t for t in tools if t.get("status") == "active"]
            if active_tools:
                tool_lines = "\n".join(
                    f"- {t['id']}: {t['name']} (type: {t.get('type', 'function')})"
                    for t in active_tools
                )
                base += f"\n\n## 当前可用工具 ({len(active_tools)} 个)\n{tool_lines}"
        except Exception:
            pass

        return base

    async def _execute_tool(self, tool_info: dict, user_query: str, dataset_paths: list[str] = None) -> dict | None:
        """实际执行工具并返回结果"""
        try:
            import importlib.util
            from pathlib import Path

            tool_id = tool_info["id"]
            project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
            impl_path = project_root / "resources" / "tools" / "implementations" / tool_id / "tool.py"

            if not impl_path.exists():
                return None

            spec = importlib.util.spec_from_file_location(f"tool_{tool_id}", str(impl_path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if not hasattr(module, "execute"):
                return None

            # 用 LLM 提取参数（传入数据集路径上下文）
            params = await self._extract_params(user_query, tool_id, dataset_paths)
            result = module.execute(**params)
            return result
        except Exception as e:
            return None  # 返回 None 表示失败，触发失败报告

    async def _extract_params(self, query: str, tool_id: str, dataset_paths: list[str] = None) -> dict:
        """从用户查询中提取工具调用参数，自动注入数据集路径"""
        try:
            # 1. 先读取工具的 MD spec，找出路径类型的参数
            path_param_names = self._get_path_params(tool_id)

            # 2. 自动注入数据集路径到匹配的参数
            auto_params = {}
            if dataset_paths and path_param_names:
                for i, param_name in enumerate(path_param_names):
                    if i < len(dataset_paths):
                        auto_params[param_name] = dataset_paths[i]

            # 3. 让 LLM 提取其余参数
            context_parts = []
            if auto_params:
                context_parts.append(f"已自动注入的参数: {json.dumps(auto_params)}（这些是数据集路径，不要修改）")
            if dataset_paths:
                context_parts.append(f"可用数据集路径: {json.dumps(dataset_paths)}")

            context = "\n".join(context_parts)
            prompt = (
                f"用户查询: \"{query}\"\n"
                f"工具ID: {tool_id}\n"
                f"{context}\n\n"
                f"请提取工具需要的其他参数，返回 JSON。\n"
                f"已有参数不要重复，只提取额外参数。如果没有额外参数，返回 {{}}。"
                f"只返回 JSON，不要其他内容。"
            )
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=500,
            )
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("\n", 1)[0]
            extra_params = json.loads(clean)

            # 4. 合并自动参数和 LLM 提取的参数
            merged = {**auto_params, **extra_params}
            return merged
        except Exception:
            import re
            params = {}
            if dataset_paths:
                path_names = self._get_path_params(tool_id)
                if path_names:
                    params[path_names[0]] = dataset_paths[0]
            numbers = re.findall(r'\d+\.?\d*', query)
            if numbers:
                params["number"] = float(numbers[0])
            return params

    def _get_path_params(self, tool_id: str) -> list[str]:
        """从工具的 MD spec 中解析路径类型参数名（如 data_path, input_file 等）"""
        try:
            from pathlib import Path
            project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
            spec_path = project_root / "resources" / "tools" / "definitions" / f"{tool_id}.md"
            if not spec_path.exists():
                return []
            md = spec_path.read_text()
            path_params = []
            # 解析输入规范表格
            in_section = False
            for line in md.split("\n"):
                if "输入规范" in line:
                    in_section = True
                    continue
                if in_section and line.startswith("##"):
                    break
                if in_section and line.startswith("|") and "参数名" not in line and "---" not in line:
                    parts = [p.strip() for p in line.split("|")[1:-1]]
                    if len(parts) >= 2:
                        name, ptype = parts[0], parts[1].lower()
                        # 判断参数是否为路径类型
                        if "path" in name.lower() or "file" in name.lower() or "dir" in name.lower():
                            path_params.append(name)
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
