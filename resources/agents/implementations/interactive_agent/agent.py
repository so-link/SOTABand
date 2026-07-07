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

        if not content.strip():
            yield {"event": "done", "data": {"messageId": ctx.session_id}}
            return

        # 解析附件中的数据集路径
        dataset_paths = self._resolve_dataset_paths(attachments)

        session_key = ctx.session_id or "default"

        # 如果有 pending 的参数收集 → 只处理参数回答，不做工具匹配
        pending = self._pending_calls.get(session_key)
        if pending:
            # 解析用户的回答，提取参数值
            answer_params = await self._extract_answer_params(
                content, pending["tool_id"], pending["missing"][0]
            )
            pending["params"].update(answer_params)
            pending["missing"] = await self._check_missing_params(pending["tool_id"], pending["params"])
            if pending["missing"]:
                p = pending["missing"][0]
                yield {"event": "content", "data": {"text": f"请提供 **{p['name']}** ({p.get('desc', '')}) 的值。"}}
                yield {"event": "done", "data": {"messageId": f"msg-{int(time.time() * 1000)}"}}
                return
            # 参数齐备 → 执行工具
            self._pending_calls.pop(session_key, None)
            matched_tool = {"id": pending["tool_id"], "name": pending["tool_name"]}
            skip_param_extract = pending["params"]
        else:
            matched_tool = self._pre_match_tool(content)

        history = self._sessions.get(session_key, [])
        if not history:
            system_content = self._build_system_with_context()
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
                yield {"event": "done", "data": {"messageId": f"msg-{int(time.time() * 1000)}"}}
                return
            tool_result = await self._execute_tool(matched_tool, content, dataset_paths, params)
            if tool_result:
                yield {
                    "event": "card",
                    "data": {
                        "type": "result-summary",
                        "title": f"工具执行: {matched_tool['name']}",
                        "summary": tool_result.get("message", json.dumps(tool_result, ensure_ascii=False, default=str)[:200]),
                        "data": {"tool_id": matched_tool["id"], "result": tool_result},
                    },
                }
                # 图片/表格/失败 → 直接结束，不调 LLM
                if tool_result.get("output_format") in ("image", "table") or tool_result.get("status") == "failed":
                    yield {"event": "done", "data": {"messageId": f"msg-{int(time.time() * 1000)}"}}
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
            yield {"event": "done", "data": {"messageId": f"msg-{int(time.time() * 1000)}"}}
            return

        user_msg = self._build_user_message(content, attachments, dataset_paths)
        # 工具结果作为上下文注入（仅文字摘要，不含图片路径）
        if tool_result:
            summary = tool_result.get("message", "") or tool_result.get("summary", "")
            if not summary:
                # 提取不含 image_path/data 的关键信息
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

    async def _execute_tool(self, tool_info: dict, user_query: str, dataset_paths: list[str] = None, pre_params: dict = None) -> dict | None:
        """实际执行工具并返回结果（优先使用工具独立 venv）"""
        try:
            import subprocess
            import json as _json
            import tempfile
            import os
            from pathlib import Path

            tool_id = tool_info["id"]
            project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
            impl_path = project_root / "resources" / "tools" / "implementations" / tool_id / "tool.py"
            self._current_process = None  # 用于外部终止

            if not impl_path.exists():
                return {"status": "failed", "message": f"工具代码不存在: {impl_path}"}

            # 提取参数（优先用已提取的）
            params = pre_params if pre_params else await self._extract_params(user_query, tool_id, dataset_paths)

            # 检查是否有独立 venv
            venv_python = project_root / "resources" / "tools" / "implementations" / tool_id / ".venv" / "bin" / "python"
            python_exe = str(venv_python) if venv_python.exists() else None

            if python_exe:
                # 使用工具独立 venv，子进程执行
                code = impl_path.read_text()
                test_script = (
                    f"import json, sys\n"
                    f"sys.path.insert(0, {_json.dumps(str(project_root))})\n"
                    f"code = {_json.dumps(code)}\n"
                    f"exec(code)\n"
                    f"result = execute(**{_json.dumps(params)})\n"
                    f"print(json.dumps(result, default=str))\n"
                )
                with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                    f.write(test_script)
                    tmp_path = f.name
                try:
                    # 传递环境和工具目录
                    env = os.environ.copy()
                    env["TOOL_DIR"] = str(impl_path.parent)
                    env.setdefault("CUDA_VISIBLE_DEVICES", "0")
                    proc = subprocess.run([python_exe, tmp_path],
                                        capture_output=True, text=True,
                                        env=env)
                    if proc.returncode == 0:
                        return _json.loads(proc.stdout.strip())
                    else:
                        return {"status": "failed", "message": proc.stderr[:300]}
                finally:
                    os.unlink(tmp_path)
            else:
                # 回退：同进程加载执行
                import importlib.util
                spec = importlib.util.spec_from_file_location(f"tool_{tool_id}", str(impl_path))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "execute"):
                    result = module.execute(**params)
                    # 兼容 async/sync
                    if hasattr(result, '__await__'):
                        return await result
                    return result
                return {"status": "failed", "message": "工具未实现 execute()"}
        except Exception as e:
            return {"status": "failed", "message": f"工具执行异常: {str(e)[:300]}"}

    async def _extract_answer_params(self, answer: str, tool_id: str, param: dict) -> dict:
        """从用户回答中提取单个参数的值"""
        try:
            prompt = (
                f"用户正在回答关于工具参数的提问。\n"
                f"参数名: {param['name']}\n"
                f"参数描述: {param.get('desc', '')}\n"
                f"用户回答: \"{answer}\"\n\n"
                f"提取参数值，返回 JSON: {{\"{param['name']}\": <value>}}\n"
                f"如果用户回答中包含数值，保持数值类型。只返回 JSON。"
            )
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=100,
            )
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("\n", 1)[0]
            return json.loads(clean)
        except Exception:
            # 降级：直接把用户输入作为参数值
            return {param['name']: answer.strip()}

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
        """基于 registry 预提取的 param_meta + LLM 智能提取参数"""
        from pathlib import Path as _Path

        params = {}

        path_param_names = self._get_path_params(tool_id)
        all_valid_names = set(path_param_names)
        if dataset_paths and path_param_names:
            for i, param_name in enumerate(path_param_names):
                if i < len(dataset_paths):
                    params[param_name] = dataset_paths[i]

        try:
            from core.resource.registry.tool_registry import ToolRegistry
            reg = ToolRegistry()
            entry = await reg.get(tool_id)
            if not entry:
                return params
            param_meta = entry.get("param_meta", [])
            if not param_meta:
                return params

            prompt = (
                f"Extract tool parameters from user query.\n\n"
                f"Parameter definitions:\n{json.dumps(param_meta, ensure_ascii=False, indent=2)}\n\n"
                f"User query: \"{query}\"\n\n"
            )
            if params:
                prompt += f"Already injected: {json.dumps(params, ensure_ascii=False)}\n"
            prompt += "\nReturn JSON with extracted parameters. Parameter names MUST match definitions. Only include parameters clearly inferable from the query."

            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=300,
            )
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("\n", 1)[0]
            extra = json.loads(clean)
            # 只保留 MD spec 中定义的参数名
            valid_names = {p.get("name") for p in param_meta if p.get("name")}
            extra = {k: v for k, v in extra.items() if k in valid_names}
            params.update(extra)
            # 最终过滤：确保不返回任何未定义的参数
            all_valid_names.update(valid_names)
            params = {k: v for k, v in params.items() if k in all_valid_names}
        except Exception:
            pass

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
