"""Low-oxygen conversation stages, parameter collection and event formatting."""
import json
import os
import re
import time
from pathlib import Path
from typing import AsyncGenerator
from core.agent.base import AgentContext
from .state import HypoxemiaState
from .conversation_config import (
    TIMESERIES_MODEL_NAMES, TIMESERIES_DEFAULT_MODEL_CONFIG,
    TIMESERIES_DEFAULT_TRAINING_CONFIG, EVALUATION_METRICS,
    RAW_PROCESSING_PARAMETER_DEFS, RAW_PROCESSING_PARAMETER_LIMITS,
    MAMBA_MODEL_NAMES, UNSUPPORTED_CLASSIFICATION_MODEL_NAMES,
    model_architecture_defaults,
)


class HypoxemiaConversation:
    async def _execute_impl(
        self, ctx: AgentContext, content: str, attachments: list, workspace_tool_ids: list[str] = None
    ) -> AsyncGenerator[dict, None]:

        # 解析附件中的数据集路径
        dataset_paths = self._resolve_dataset_paths(attachments)

        session_key = ctx.session_id or "default"

        # A new explicit workflow command must not be consumed as an old parameter.
        pending = self._pending_calls.get(session_key) or self._pending_modes.get(session_key)
        active_workflow = getattr(self, "_timeseries_workflows", {}).get(session_key, {})
        explicit_mode = self._resolve_timeseries_workflow_intent(
            content,
            bool(active_workflow),
            session_key=session_key,
        )
        if explicit_mode and (
            self._pending_confirm.get(session_key)
            or (pending and (
                pending.get("tool_id") != "timeseries-classification-runner"
                or (pending.get("mode") != explicit_mode and explicit_mode != "dataset-source")
            ))
        ):
            self._pending_calls.pop(session_key, None)
            self._pending_modes.pop(session_key, None)
            self._pending_confirm.pop(session_key, None)

        # 前置状态机：模式选择 / 工具确认 / 参数收集 三者互斥，同一时刻最多一个生效
        selected_mode_id = None
        matched_tool = None
        skip_param_extract = {}
        execution_query = content
        force_llm_intervention = False

        # Explicitly starting a new low-oxygen workflow must recover from an
        # abandoned parameter prompt in the same session.
        normalized_content = content.strip().lower()
        if normalized_content in {"开始低氧血症预测", "开始低氧预测", "重新开始低氧血症预测"}:
            self._pending_timeseries_input.pop(session_key, None)
            self._pending_timeseries_choice.pop(session_key, None)
            self._pending_calls.pop(session_key, None)
            self._pending_modes.pop(session_key, None)
            self._timeseries_workflows.pop(session_key, None)

        pending_input = getattr(self, "_pending_timeseries_input", {}).pop(session_key, None)
        if pending_input:
            async for event in self._handle_timeseries_input(session_key, pending_input, content):
                yield event
            return

        pending_choice = getattr(self, "_pending_timeseries_choice", {}).pop(session_key, None)
        if pending_choice:
            selected = self._resolve_timeseries_choice(content, pending_choice["candidates"])
            if selected is None and pending_choice.get("stage") == "model":
                selected = self._resolve_model_from_candidates(content, pending_choice["candidates"])
            if selected is None:
                selected = self._resolve_timeseries_choice_alias(content, pending_choice)
            if selected:
                async for event in self._handle_timeseries_choice(session_key, pending_choice, selected):
                    yield event
                return
            # 无法匹配时允许大模型理解用户的高级或自定义要求。
            workflow = self._timeseries_workflows.setdefault(session_key, {})
            workflow["llm_intervention_stage"] = pending_choice.get("stage", "")

        workflow = getattr(self, "_timeseries_workflows", {}).get(session_key, {})
        if workflow.get("llm_intervention_stage"):
            workflow.pop("llm_intervention_stage", None)
            force_llm_intervention = True
        # Recover a lost choice-card state for the common case where the user
        # replies with an exact directory model name.  Without this guard the
        # domain marker in the model name can route back to dataset-source and
        # produce the generic tool prompt instead of continuing training setup.
        if (
            not force_llm_intervention
            and not pending_choice
            and workflow.get("dataset_source_type") == "processed"
        ):
            candidates = self._timeseries_model_selection_candidates()
            selected_model = self._resolve_model_from_candidates(content, candidates)
            if selected_model:
                async for event in self._handle_timeseries_choice(
                    session_key,
                    {"stage": "model", "candidates": candidates},
                    selected_model,
                ):
                    yield event
                return
        # Raw processing is submitted as an asynchronous task.  A follow-up
        # "继续" must wait for that task and never submit the same raw job again.
        if (
            not force_llm_intervention
            and explicit_mode == "validate"
            and workflow.get("processing_task_id")
            and normalized_content in {"继续", "开始检查", "检查数据", "继续处理"}
        ):
            try:
                from core.task.task_manager import TaskManager

                processing_task = TaskManager().get(workflow["processing_task_id"])
            except Exception:
                processing_task = None
            if not processing_task:
                yield {"event": "content", "data": {"text": "未找到原始数据处理任务，请重新提供原始数据目录。"}}
                return
            processing_status = processing_task.get("status")
            if processing_status in {"queued", "running"}:
                yield {
                    "event": "content",
                    "data": {"text": "原始数据仍在处理中，请等待任务完成后再回复“继续”。"},
                }
                return
            if processing_status != "completed":
                yield {
                    "event": "content",
                    "data": {"text": f"原始数据处理任务状态为 {processing_status}，请查看任务日志后重试。"},
                }
                return
        # A completed training task is a different continuation than raw
        # processing.  Older task cards used the generic "继续扫描数据集"
        # label, so accept that reply as an evaluation transition as well.
        if (
            not force_llm_intervention
            and normalized_content in {"继续", "开始评测", "评测", "开始测评"}
            and workflow.get("task_id")
            and "evaluation_metrics" not in workflow
        ):
            try:
                from core.task.task_manager import TaskManager
                training_task = TaskManager().get(workflow["task_id"])
            except Exception:
                training_task = None
            if training_task and training_task.get("status") == "completed":
                for event in self._queue_training_artifact_choice(session_key, training_task):
                    yield event
                return
        if (
            not force_llm_intervention
            and explicit_mode == "evaluate"
            and workflow.get("task_id")
            and "evaluation_metrics" not in workflow
        ):
            try:
                from core.task.task_manager import TaskManager

                task = TaskManager().get(workflow["task_id"])
            except Exception:
                task = None
            if not task:
                yield {"event": "content", "data": {"text": "未找到训练任务，请先确认训练任务状态。"}}
                return
            if task.get("status") in {"queued", "running"}:
                yield {
                    "event": "content",
                    "data": {"text": "训练任务尚未完成，请等待任务完成后再选择评测指标。"},
                }
                return
            if task.get("status") != "completed":
                yield {
                    "event": "content",
                    "data": {"text": f"训练任务状态为 {task.get('status')}，暂时无法进入评测。"},
                }
                return
            for event in self._queue_training_artifact_choice(session_key, task):
                yield event
            return
        if not force_llm_intervention and workflow and self._resolve_timeseries_workflow_intent(
            content, True, session_key=session_key
        ) == "train" and not workflow.get("workflow_ready"):
            if pending_choice:
                self._pending_timeseries_choice[session_key] = pending_choice
                yield self._choice_card(
                    "请先完成模型与训练参数设置", "完成当前选择后才能启动训练。",
                    pending_choice["candidates"],
                )
            else:
                for event in self._queue_timeseries_choice(
                    session_key, "model", "选择低氧血症模型",
                    "请先选择模型并设置参数，再启动训练。",
                    self._timeseries_model_selection_candidates(),
                ):
                    yield event
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
            params = self._apply_timeseries_workflow_context(session_key, matched_tool, selected_mode_id, params)
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
            # 工具确认消息通常只有“第 N 个工具”，应继续使用原始请求的动作和参数。
            selected_mode_id = pending_confirm.get("mode")
            execution_query = pending_confirm.get("query", content)
            if matched_tool["id"] == "timeseries-classification-runner":
                selected_mode_id = self._resolve_timeseries_workflow_intent(
                    execution_query
                ) or "dataset-source"

        # ③ 正在收集参数
        elif self._pending_calls.get(session_key):
            pending = self._pending_calls[session_key]
            if pending.get("await_raw_processing_start"):
                if normalized_content not in {"开始处理", "处理数据", "运行处理", "确认", "开始"}:
                    self._pending_calls[session_key] = pending
                    yield {
                        "event": "content",
                        "data": {"text": "处理参数已准备好。请回复“开始处理”提交原始数据处理任务。"},
                    }
                    return
                self._pending_calls.pop(session_key, None)
                matched_tool = {"id": pending["tool_id"], "name": pending["tool_name"]}
                selected_mode_id = pending.get("mode")
                skip_param_extract = pending.get("params", {})
            else:
                answer_params = await self._extract_answer_params(
                    content, pending["tool_id"], pending["missing"][0]
                )
            if not pending.get("await_raw_processing_start") and (
                pending.get("tool_id") == "timeseries-classification-runner"
                and answer_params.get("dataset_source_type") == "__other__"
            ):
                self._pending_calls.pop(session_key, None)
                self._timeseries_workflows.setdefault(session_key, {})[
                    "llm_intervention_stage"
                ] = "dataset-source"
                yield {
                    "event": "content",
                    "data": {"text": "请直接描述你的其它数据需求，我会让大模型参与当前步骤。"},
                }
                return
            if not pending.get("await_raw_processing_start") and pending.get("tool_id") == "timeseries-classification-runner":
                extracted_path = self._extract_timeseries_path(content)
                path = (dataset_paths or [extracted_path])[0]
                staging_root = self._extract_timeseries_staging_root(content)
                # When the previous prompt explicitly asked for the missing
                # staging_root, a bare local path is that staging directory.
                # Do not mistake it for a replacement source_path.
                missing_name = (pending.get("missing") or [{}])[0].get("name")
                if (
                    missing_name == "staging_root"
                    and extracted_path
                    and not staging_root
                    and not re.search(r"(?:staging[_ -]?root|ZIP\s*临时解压目录|临时解压目录|解压临时目录)", content, re.IGNORECASE)
                ):
                    staging_root = extracted_path
                    path = ""
                # A message containing only the ZIP staging directory must not
                # replace the original source directory collected earlier.
                if staging_root and path and Path(path).resolve() == Path(staging_root).resolve():
                    path = ""
                if path and pending.get("mode") in {"dataset-source", "validate", "process-raw-dataset"}:
                    answer_params["source_path"] = path
                if pending.get("mode") in {"dataset-source", "process-raw-dataset"}:
                    if staging_root:
                        answer_params["staging_root"] = staging_root
                if pending.get("mode") in {"dataset-source", "process-raw-dataset"}:
                    answer_params.update(self._extract_raw_processing_params(content))
            if not pending.get("await_raw_processing_start"):
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

        if force_llm_intervention:
            async for event in self.host.respond(ctx, content, attachments, workspace_tool_ids):
                yield event
            return
        if not matched_tool:
            matched_tool = {"id": self.profile.tool_id, "name": "通用时序分类训练评测"}

        if matched_tool:
            # 多模式工具且尚未选定模式 → 先引导用户选择模式
            if selected_mode_id is None:
                modes = self._get_tool_modes(matched_tool["id"])
                if modes:
                    # Explicit low-oxygen workflow actions take precedence over
                    # broad mode-name matches such as "预测" -> the reserved
                    # single-window predict mode.
                    hint_mode = None
                    if matched_tool["id"] == "timeseries-classification-runner":
                        forced_mode = self._resolve_timeseries_workflow_intent(
                            execution_query,
                            session_key in getattr(self, "_timeseries_workflows", {}),
                            session_key=session_key,
                        )
                        if forced_mode:
                            hint_mode = next(
                                (mode for mode in modes if mode.get("id") == forced_mode),
                                None,
                            )
                    if hint_mode is None:
                        hint_mode = self._resolve_mode_selection(content, modes)
                    if hint_mode is None:
                        self._pending_modes[session_key] = {
                            "tool_id": matched_tool["id"],
                            "tool_name": matched_tool["name"],
                            "modes": modes,
                        }
                        options = "、".join(
                            f"{i}.{m['name']}" + (f"（{m['desc']}）" if m.get('desc') else "")
                            for i, m in enumerate(modes, 1)
                        )
                        yield {"event": "content", "data": {"text": f"「{matched_tool['name']}」支持以下模式：\n{options}\n请回复编号或模式名称选择。"}}
                        return
                    selected_mode_id = hint_mode["id"]

            # 参数（pending 已收集或重新提取）
            if skip_param_extract:
                params = skip_param_extract
            else:
                params = await self._extract_params_for_mode(
                    execution_query, matched_tool["id"], selected_mode_id, dataset_paths
                )
            params = self._apply_timeseries_workflow_context(session_key, matched_tool, selected_mode_id, params)
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
                if matched_tool["id"] == "timeseries-classification-runner" and selected_mode_id == "dataset-source":
                    yield self._dataset_source_choice_card()
                    yield {"event": "content", "data": {"text": "请选择数据集类型：1. 原始数据；2. 已处理数据。可回复编号或名称。"}}
                    return
                yield {"event": "content", "data": {"text": f"要使用 **{matched_tool['name']}** 工具，请提供以下参数:\n\n**{p['name']}** — {p.get('desc', '')}"}}
                return
            if (
                matched_tool["id"] == "timeseries-classification-runner"
                and selected_mode_id == "process-raw-dataset"
            ):
                raw_workflow = self._timeseries_workflows.setdefault(session_key, {})
                raw_source = str(params.get("source_path", "")).strip()
                staging_root = str(params.get("staging_root", "")).strip()
                if raw_source and self._raw_source_contains_zip(raw_source) and not staging_root:
                    self._pending_calls[session_key] = {
                        "tool_id": matched_tool["id"],
                        "tool_name": matched_tool["name"],
                        "mode": selected_mode_id,
                        "params": params,
                        "missing": [{
                            "name": "staging_root",
                            "type": "string",
                            "desc": "ZIP 临时解压目录（需要有足够空间）",
                        }],
                    }
                    yield {
                        "event": "content",
                        "data": {"text": "检测到原始目录包含 ZIP。请提供 staging_root（ZIP 临时解压目录），再选择处理参数。"},
                    }
                    return
                raw_workflow.update({
                    "source_path": raw_source,
                    "staging_root": staging_root,
                    "raw_processing_config": {
                        key: value for key, value in params.items()
                        if key not in {"source_path", "staging_root"}
                    },
                })
                if not raw_workflow.get("raw_processing_config_confirmed"):
                    self._pending_calls[session_key] = {
                        "tool_id": matched_tool["id"],
                        "tool_name": matched_tool["name"],
                        "mode": selected_mode_id,
                        "params": dict(params),
                        "missing": [],
                        "await_raw_processing_start": False,
                    }
                    for event in self._queue_raw_processing_choice(session_key):
                        yield event
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
            tool_result = await self._execute_tool(matched_tool, execution_query, dataset_paths, params)
            if tool_result:
                from core.workflow.execution import persist_workflow_result
                tool_result = persist_workflow_result(tool_result, matched_tool, self.timeseries_service)
                # All mode-specific response handling below may inspect the
                # tool payload, including failure responses.
                result_data = tool_result.get("data", {})
                if not isinstance(result_data, dict):
                    result_data = {}
                # 长任务工具只返回经过校验的任务描述；真正的子进程由平台任务层托管，
                # 不占用 Agent 请求，也不随 SSE 连接断开而终止。
                tool_data = tool_result.get("data", {})
                task_request = tool_data.get("task_request") if isinstance(tool_data, dict) else None
                if isinstance(task_request, dict):
                    tool_result = self.host.submit_task_result(tool_result, matched_tool)
                # The task wrapper replaces the original tool payload.  Use
                # the new payload below so the session retains task_id and
                # processed_dataset_path for the next "继续".
                result_data = tool_result.get("data", {})
                if not isinstance(result_data, dict):
                    result_data = {}
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

                if matched_tool["id"] == "timeseries-classification-runner" and tool_result.get("status") == "success":
                    workflow = self._timeseries_workflows.setdefault(session_key, {})
                    if selected_mode_id == "dataset-source":
                        workflow.clear()
                    if isinstance(result_data, dict):
                        if result_data.get("source_path"):
                            workflow["source_path"] = result_data["source_path"]
                        if result_data.get("model_id"):
                            workflow["model_id"] = result_data["model_id"]
                        for key in (
                            "dataset_source_type", "output_root", "processed_dataset_path", "staging_root",
                        ):
                            if result_data.get(key):
                                workflow[key] = result_data[key]
                        if selected_mode_id == "validate":
                            workflow["dataset_source_type"] = "processed"
                            if result_data.get("source_path"):
                                workflow["processed_dataset_path"] = result_data["source_path"]
                        if selected_mode_id == "process-raw-dataset" and result_data.get("task_id"):
                            workflow["processing_task_id"] = result_data["task_id"]
                    if registered_dataset_id:
                        workflow["dataset_id"] = registered_dataset_id
                    if selected_mode_id == "train":
                        task_id = tool_result.get("data", {}).get("task_id", "") if isinstance(tool_result.get("data"), dict) else ""
                        if task_id:
                            workflow["task_id"] = task_id

                card_type = tool_result.get("card_type", "result-summary")
                card_data = {
                    "tool_id": matched_tool["id"],
                    "result": tool_result,
                    "registered_dataset_id": registered_dataset_id,
                }
                if card_type != "result-summary" and isinstance(tool_result.get("data"), dict):
                    card_data.update(tool_result["data"])

                yield {
                    "event": "card",
                    "data": {
                        "type": card_type,
                        "title": f"工具执行: {matched_tool['name']}",
                        "summary": tool_result.get("message", json.dumps(tool_result, ensure_ascii=False, default=str)[:200]),
                        "data": card_data,
                    },
                }
                # The catalog is a structured selection list, not material for LLM summarization.
                if matched_tool["id"] == "timeseries-classification-runner" and selected_mode_id == "list-models":
                    yield {
                        "event": "content",
                        "data": {"text": self._format_timeseries_model_catalog(tool_result)},
                    }
                    return
                if matched_tool["id"] == "timeseries-classification-runner" and selected_mode_id == "dataset-source":
                    result_data = tool_result.get("data", {})
                    if tool_result.get("status") != "success":
                        self._pending_calls[session_key] = {
                            "tool_id": matched_tool["id"], "tool_name": matched_tool["name"],
                            "mode": "dataset-source", "params": {
                                key: value for key, value in params.items()
                                if key in {"source_path", "staging_root"}
                            },
                            "missing": [{"name": "dataset_source_type", "type": "string"}],
                        }
                        yield self._dataset_source_choice_card()
                        yield {"event": "content", "data": {"text": tool_result.get("message", "请选择数据集类型")}}
                        return
                    next_mode = result_data["next_mode"]
                    next_params = {
                        key: value for key, value in {
                            "source_path": result_data.get("source_path"),
                            "staging_root": result_data.get("staging_root"),
                        }.items() if value
                    }
                    if isinstance(result_data.get("raw_processing_config"), dict):
                        next_params.update(result_data["raw_processing_config"])
                    if next_mode == "validate" and not next_params.get("source_path"):
                        self._pending_calls.pop(session_key, None)
                        for event in self._queue_timeseries_choice(
                            session_key, "processed-data-source", "选择已处理数据",
                            "选择数据空间中的数据集、上传本机文件，或输入已有路径和数据集 ID。",
                            [
                                *self.processed_dataset_candidates(),
                                {"id": "__upload__", "name": "选择本机文件"},
                                {"id": "__path__", "name": "输入路径或数据集 ID"},
                            ],
                        ):
                            yield event
                        return
                    missing = await self._check_missing_params(matched_tool["id"], next_params, next_mode)
                    if missing:
                        self._pending_calls[session_key] = {
                            "tool_id": matched_tool["id"], "tool_name": matched_tool["name"],
                            "mode": next_mode, "params": next_params, "missing": missing,
                        }
                    yield {
                        "event": "content",
                        "data": {"text": self._format_timeseries_dataset_source(tool_result)},
                    }
                    return
                if (matched_tool["id"] == "timeseries-classification-runner"
                        and selected_mode_id == "validate"
                        and tool_result.get("status") == "success"):
                    for event in self._queue_timeseries_choice(
                        session_key, "workflow-kind", "选择低氧血症后续操作",
                        "数据扫描和五通道文件检查已完成。请选择训练新模型，或加载已训练模型进行评测。",
                        [
                            {"id": "train", "name": "选择模型架构并训练"},
                            {"id": "evaluate", "name": "选择已训练模型进行评测"},
                            {"id": "__other__", "name": "其它（交给大模型）"},
                        ],
                    ):
                        yield event
                    return
                if matched_tool["id"] == "timeseries-classification-runner" and selected_mode_id == "process-raw-dataset":
                    if tool_result.get("status") != "success":
                        message = str(tool_result.get("message", ""))
                        if "staging_root" in message and not params.get("staging_root"):
                            self._pending_calls[session_key] = {
                                "tool_id": matched_tool["id"],
                                "tool_name": matched_tool["name"],
                                "mode": selected_mode_id,
                                "params": dict(params),
                                "missing": [{
                                    "name": "staging_root",
                                    "type": "string",
                                    "desc": "ZIP 临时解压目录（需要有足够空间）",
                                }],
                            }
                            yield {
                                "event": "content",
                                "data": {"text": "请提供 staging_root（ZIP 临时解压目录），然后可以重新提交原始数据处理。"},
                            }
                        return
                    processing_config = result_data.get("processing_config", {}) if isinstance(result_data, dict) else {}
                    window_config = result_data.get("window_config", "256:30") if isinstance(result_data, dict) else "256:30"
                    task_id = result_data.get("task_id") if isinstance(result_data, dict) else None
                    task_status = "queued"
                    if task_id:
                        try:
                            from core.task.task_manager import TaskManager

                            task = TaskManager().get(task_id)
                            task_status = task.get("status", "queued") if task else "queued"
                        except Exception:
                            task_status = "queued"
                    if task_status in {"queued", "running"}:
                        text = (
                            f"原始数据处理任务已提交，当前状态：{task_status}。"
                            f"任务完成后回复“继续”进行数据扫描。\n任务 ID：{task_id or 'unknown'}"
                        )
                    elif task_status == "completed":
                        text = "原始数据处理任务已完成。请回复“继续”进行数据扫描。"
                    else:
                        text = f"原始数据处理任务状态为 {task_status}，请查看任务日志。"
                    yield {
                        "event": "content",
                        "data": {
                            "text": f"{text}\n窗口配置：{window_config}\n处理参数：\n{self._format_raw_processing_config(processing_config)}"
                        },
                    }
                    return
                if matched_tool["id"] == "timeseries-classification-runner" and selected_mode_id == "train":
                    if tool_result.get("status") == "success":
                        yield {
                            "event": "content",
                            "data": {"text": "训练任务已提交。任务完成后回复“开始评测”，再选择评测指标。"},
                        }
                    return
                if matched_tool["id"] == "timeseries-classification-runner" and selected_mode_id == "model-creation-guide":
                    yield {
                        "event": "content",
                        "data": {"text": self._format_timeseries_model_creation_guide(tool_result)},
                    }
                    return
                if matched_tool["id"] == "timeseries-classification-runner" and selected_mode_id == "custom-model-design":
                    yield {
                        "event": "content",
                        "data": {"text": self._format_custom_model_design(tool_result)},
                    }
                    return
                if matched_tool["id"] == "timeseries-classification-runner" and selected_mode_id == "model-config":
                    yield {
                        "event": "content",
                        "data": {"text": self._format_timeseries_model_configuration(tool_result)},
                    }
                    return
                if (matched_tool["id"] == "timeseries-classification-runner"
                        and selected_mode_id == "evaluate"
                        and tool_result.get("status") == "success"):
                    evaluation = tool_result.get("data", {}) if isinstance(tool_result.get("data"), dict) else {}
                    metrics = evaluation.get("selected_metrics", {})
                    metric_lines = []
                    if isinstance(metrics, dict):
                        for key in ("auc", "auprc", "f1", "accuracy", "balanced_accuracy", "precision", "recall", "mcc"):
                            if key in metrics:
                                metric_lines.append(f"- {key}: {metrics[key]}")
                    scope_label = evaluation.get(
                        "evaluation_scope_label",
                        "验证集（训练集用于训练，测试集保留作独立最终测评）",
                    )
                    result_lines = [
                        "评测已完成。",
                        f"评测范围：{scope_label}",
                        f"输入数据集总窗口：{evaluation.get('source_dataset_window_count', '-')}",
                        f"本次验证集窗口：{evaluation.get('dataset_window_count', '-')}（低氧 {evaluation.get('dataset_positive_count', '-')}, 非低氧 {evaluation.get('dataset_negative_count', '-')})",
                    ]
                    if metric_lines:
                        result_lines.extend(["验证集指标：", *metric_lines])
                    if evaluation.get("threshold") is not None:
                        result_lines.append(f"验证集阈值：{evaluation['threshold']}")
                    if evaluation.get("model_resource_id"):
                        result_lines.append(f"模型资源 ID：{evaluation['model_resource_id']}")
                    if evaluation.get("model_artifact"):
                        result_lines.append(f"模型产物目录：{evaluation['model_artifact']}")
                    model_files = evaluation.get("model_files", {})
                    if isinstance(model_files, dict):
                        for label, key in (("权重", "weights"), ("缩放器", "scaler"), ("配置", "config"), ("阈值报告", "threshold_report")):
                            if model_files.get(key):
                                result_lines.append(f"{label}：{model_files[key]}")
                    if evaluation.get("report_path"):
                        result_lines.append(f"评测报告：{evaluation['report_path']}")
                    yield {"event": "content", "data": {"text": "\n".join(result_lines)}}
                    return
                # 图片/表格/文件/失败 → 直接结束，不调 LLM（文件由前端渲染，如音频播放器、下载链接等）
                if tool_result.get("output_format") in ("image", "table", "file", "task") or tool_result.get("status") == "failed":
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

        async for event in self.host.respond(ctx, content, attachments, workspace_tool_ids, tool_result):
            yield event

    def _resolve_timeseries_workflow_intent(
        self, query: str, has_workflow_context: bool = False, *, session_key: str | None = None
    ) -> str | None:
        """Resolve explicit workflow commands, or concise follow-ups in an active workflow."""
        normalized = query.strip().lower()
        if not normalized:
            return None

        domain_markers = (
            "\u4e94\u901a\u9053", "\u4f4e\u6c27", "\u4f4e\u6c27\u8840\u75c7", "\u4f4e\u6c27\u9884\u6d4b",
            "\u4f4e\u6c27\u68c0\u6d4b", "\u8840\u6c27", "\u751f\u7406\u4fe1\u53f7",
            "五通道", "低氧", "低氧血症", "低氧预测", "低氧检测", "血氧", "spo2",
            "生理信号", "segrnn", "256x5", "v3", "hypoxemia", "hypoxaemia",
        )
        is_domain_request = any(marker in normalized for marker in domain_markers)
        workflow_adapter = self
        is_domain_request = is_domain_request or workflow_adapter.owns_message(
            query, has_workflow_context
        )
        if not is_domain_request and not has_workflow_context:
            return None

        workflow = getattr(self, "_timeseries_workflows", {}).get(session_key, {})
        source_type = workflow.get("dataset_source_type")
        if source_type and self._looks_like_local_path(query):
            return "process-raw-dataset" if source_type == "raw" else "validate"
        if source_type == "raw" and workflow.get("source_path") and normalized in {
            "继续", "开始处理", "处理数据", "运行处理", "继续处理",
        }:
            return "validate" if (
                workflow.get("processing_task_id") or workflow.get("processed_dataset_path")
            ) else "process-raw-dataset"
        if source_type == "processed" and workflow.get("source_path") and normalized in {
            "继续", "开始检查", "检查数据",
        }:
            return "validate"
        if source_type == "raw" and any(
            marker in normalized for marker in ("处理原始", "原始数据处理", "预处理数据")
        ):
            return "process-raw-dataset"
        if source_type == "processed" and any(
            marker in normalized for marker in ("处理好的数据", "已处理数据", "窗口数据")
        ):
            return "validate"
        if any(
            marker in normalized
            for marker in ("原始数据", "处理好的数据", "已处理数据", "选择数据集", "上传数据集", "数据集来源")
        ):
            return "dataset-source"

        # “创建一个新的低氧血症模型”中的“创建”和“模型”经常被疾病名称隔开；
        # 模型目录后的“新的结构”则完全依赖会话上下文。二者都应进入结构引导，
        # 不允许回落到通用工具创建流程。
        if (
            ("创建" in normalized and "模型" in normalized)
            or any(marker in normalized for marker in ("新的结构", "新结构", "自定义结构", "自定义模型", "新网络", "新架构"))
        ):
            if "创建自定义模型" in normalized or (
                "自定义模型" in normalized
                and any(marker in normalized for marker in ("编码器", "时序模块", "时序聚合", "分类头", "输入：", "输入:"))
            ):
                return "custom-model-design"
            if any(marker in normalized for marker in ("基于当前", "基于刚创建", "基于已有", "复制当前", "派生")):
                return "derive-model"
            if self._find_timeseries_model_name(query):
                return "create-model"
            return "model-creation-guide"

        # 先判定更具体的操作，避免“创建模型资源”被泛化为创建工具。
        if any(marker in normalized for marker in ("可用模型", "模型列表", "所有模型", "有哪些模型", "模型目录")):
            return "list-models"
        if "删除" in normalized and "模型" in normalized:
            return "delete-model"
        if any(marker in normalized for marker in (
            "查看模型配置", "查看配置", "查看参数", "模型架构参数",
        )):
            return "model-config"
        if "注册" in normalized and "数据集" in normalized:
            return "register-dataset"
        if any(marker in normalized for marker in ("检查", "校验", "查看数据集", "数据集信息")) and "数据集" in normalized:
            return "validate"
        if any(marker in normalized for marker in (
            "创建模型", "创建一个模型", "创建一个新模型", "新建模型", "模型资源", "派生模型", "复制模型",
        )):
            if any(marker in normalized for marker in ("基于当前", "基于刚创建", "基于已有", "复制当前", "派生")):
                return "derive-model"
            if self._find_timeseries_model_name(query):
                return "create-model"
            return "model-creation-guide"
        if (
            any(marker in normalized for marker in ("启动训练", "开始训练", "执行训练", "正式训练", "训练模型"))
            or ("训练" in normalized and any(marker in normalized for marker in ("启动", "开始", "执行", "正式")))
        ):
            return "train"
        if any(marker in normalized for marker in (
            "执行评测", "开始评测", "评测", "评估", "执行测评", "开始测评", "测评",
            "查看指标", "查看报告",
        )):
            return "evaluate"
        if any(marker in normalized for marker in ("导入模型", "已有模型", "检查模型", "模型产物")):
            return "inspect-model"
        if is_domain_request:
            return "dataset-source"
        return None


    @staticmethod
    def _dataset_source_choice_card() -> dict:
        return {"event": "card", "data": {
            "type": "tool-confirm", "title": "选择低氧血症数据集类型",
            "summary": "先选择原始监护数据，或已处理的五通道窗口数据。",
            "data": {"candidates": [
                {"id": "raw", "name": "原始数据", "index": 1},
                {"id": "processed", "name": "已处理数据", "index": 2},
                {"id": "__other__", "name": "其它（交给大模型）", "index": 3},
            ]},
        }}


    @staticmethod
    def _looks_like_local_path(query: str) -> bool:
        value = query.strip().strip('"')
        return bool(re.match(r"^(?:[A-Za-z]:[\\/]|[\\/]{1,2})", value))


    @staticmethod
    def _format_timeseries_model_catalog(tool_result: dict) -> str:
        """Render only the fixed five-channel model lists."""
        data = tool_result.get("data", {})
        catalog = data.get("models", []) if isinstance(data, dict) else []
        available = [item.get("model_name", "") for item in catalog if item.get("available")]
        return (
            f"## 五通道低氧血症可用模型（{len(available)} 个）\n\n"
            f"{'、'.join(available)}"
        )


    @staticmethod
    def _timeseries_model_selection_candidates(include_trained: bool = False) -> list[dict]:
        """Return trainable architecture/resources; trained artifacts are opt-in."""
        selectable = TIMESERIES_MODEL_NAMES - UNSUPPORTED_CLASSIFICATION_MODEL_NAMES
        if os.environ.get("SOTABAND_HYPOXEMIA_ENABLE_MAMBA") != "1":
            selectable -= MAMBA_MODEL_NAMES
        candidates = [
            {"id": name, "name": name, "index": index}
            for index, name in enumerate(sorted(selectable), 1)
        ]
        try:
            from core.resource.registry.model_registry import ModelRegistry
            for item in ModelRegistry()._read():
                if item.get("associated_tool_id") != "timeseries-classification-runner":
                    continue
                if item.get("runtime_status") == "trained" and not include_trained:
                    continue
                if item.get("model_name") not in selectable:
                    continue
                candidates.append({
                    "id": item.get("id", ""),
                    "name": (
                        "已训练模型：" if item.get("runtime_status") == "trained"
                        else "已有自定义模型：" if item.get("creation_type") == "custom-model"
                        else "已有模型资源："
                    ) + str(item.get("name") or item.get("id")),
                    "index": len(candidates) + 1,
                    "resource": True,
                    "model_name": item.get("model_name", ""),
                    "runtime_status": item.get("runtime_status", ""),
                    "model_path": item.get("model_path", ""),
                    "model_config": (item.get("training_config") or {}).get("architecture", {}) if isinstance(item.get("training_config"), dict) else {},
                    "training_config": (item.get("training_config") or {}).get("training", {}) if isinstance(item.get("training_config"), dict) else {},
                })
        except Exception:
            pass
        candidates.extend([
            {"id": "__create__", "name": "创建新的模型", "index": len(candidates) + 1},
            {"id": "__other__", "name": "其它（交给大模型）", "index": len(candidates) + 2},
        ])
        return candidates


    @staticmethod
    def _timeseries_trained_model_candidates() -> list[dict]:
        """Return only registered, loadable training artifacts for evaluation."""
        candidates = []
        try:
            from core.resource.registry.model_registry import ModelRegistry
            from core.runtime.paths import timeseries_runs_dir

            runs_root = timeseries_runs_dir().resolve()
            required_files = (
                "best_model.pt",
                "scaler.pkl",
                "tslib_model_config.json",
                "threshold_report_f1.json",
                "manifest.json",
            )
            for item in ModelRegistry()._read():
                if item.get("associated_tool_id") != "timeseries-classification-runner":
                    continue
                if item.get("runtime_status") != "trained" or not item.get("model_path"):
                    continue
                model_path = Path(str(item["model_path"])).expanduser().resolve()
                artifact_root = model_path.parent
                # Do not expose stale absolute paths from older checkouts or
                # external research projects. Only current runtime artifacts
                # with a complete manifest are valid evaluation candidates.
                if runs_root != artifact_root and runs_root not in artifact_root.parents:
                    continue
                if not all((artifact_root / name).is_file() for name in required_files):
                    continue
                candidates.append({
                    "id": item.get("id", ""),
                    "name": "已训练模型：" + str(item.get("name") or item.get("id")),
                    "index": len(candidates) + 1,
                    "resource": True,
                    "model_name": item.get("model_name", ""),
                    "runtime_status": "trained",
                    "model_path": str(model_path),
                })
        except Exception:
            pass
        return candidates


    @staticmethod
    def _choice_card(title: str, summary: str, candidates: list[dict]) -> dict:
        return {"event": "card", "data": {
            "type": "model-confirm", "title": title, "summary": summary,
            "data": {"candidates": candidates},
        }}


    @staticmethod
    def _resolve_timeseries_choice(answer: str, candidates: list[dict]) -> dict | None:
        return HypoxemiaState.resolve_choice(answer, candidates)


    @classmethod
    def _resolve_model_from_candidates(cls, answer: str, candidates: list[dict]) -> dict | None:
        """Resolve a model name when a previous choice card was lost."""
        return HypoxemiaState.resolve_model_from_candidates(answer, candidates)


    @staticmethod
    def _resolve_timeseries_choice_alias(answer: str, pending: dict) -> dict | None:
        """Accept natural workflow commands when a card choice is still pending."""
        return HypoxemiaState.resolve_choice_alias(answer, pending)


    def _queue_timeseries_choice(
        self, session_key: str, stage: str, title: str, summary: str, candidates: list[dict]
    ) -> list[dict]:
        normalized = [
            {**candidate, "index": index}
            for index, candidate in enumerate(candidates, 1)
        ]
        self._pending_timeseries_choice[session_key] = {"stage": stage, "candidates": normalized}
        return [
            self._choice_card(title, summary, normalized),
            {"event": "content", "data": {"text": "请回复编号或名称进行选择；其它要求可直接描述。"}},
        ]


    async def _handle_timeseries_choice(
        self, session_key: str, pending: dict, selected: dict
    ) -> AsyncGenerator[dict, None]:
        stage = pending.get("stage", "")
        choice = selected.get("id", "")
        workflow = self._timeseries_workflows.setdefault(session_key, {})
        if stage == "processed-data-source":
            if choice in {"__upload__", "__path__"}:
                self._pending_calls[session_key] = {
                    "tool_id": "timeseries-classification-runner",
                    "tool_name": "通用时序分类训练评测",
                    "mode": "validate", "params": {},
                    "missing": [{"name": "source_path", "type": "string"}],
                }
                prompt = (
                    "请点击输入框的附件按钮，选择 window_features.pkl 和 window_info.pkl 文件，然后发送。"
                    if choice == "__upload__"
                    else "请输入已处理数据目录、window_features.pkl 文件路径或已注册数据集 ID。"
                )
                yield {"event": "content", "data": {"text": prompt}}
                return
            result = await self._execute_tool(
                {"id": "timeseries-classification-runner"}, "检查已处理数据", [],
                {"mode": "validate", "source_path": choice},
            )
            if not result or result.get("status") != "success":
                yield {"event": "content", "data": {"text": (result or {}).get("message", "数据集检查失败")}}
                self._pending_timeseries_choice[session_key] = pending
                return
            data = result.get("data", {})
            workflow["dataset_source_type"] = "processed"
            workflow["dataset_id"] = data.get("dataset_id") or choice
            workflow["source_path"] = data.get("source_path", "")
            workflow["processed_dataset_path"] = data.get("source_path", "")
            yield {"event": "card", "data": {
                "type": result.get("card_type", "dataset-summary"),
                "title": "已处理数据检查通过", "summary": result.get("message", ""),
                "data": data,
            }}
            for event in self._queue_timeseries_choice(
                session_key, "workflow-kind", "选择低氧血症后续操作",
                "请选择训练新模型，或加载已训练模型进行评测。",
                [
                    {"id": "train", "name": "选择模型架构并训练"},
                    {"id": "evaluate", "name": "选择已训练模型进行评测"},
                ],
            ):
                yield event
            return
        if stage == "raw-processing-config-action":
            pending_call = self._pending_calls.get(session_key)
            if choice == "default":
                workflow["raw_processing_config"] = {}
                workflow["raw_processing_config_confirmed"] = True
                if pending_call:
                    for key, _label, _default, _help in RAW_PROCESSING_PARAMETER_DEFS:
                        pending_call["params"].pop(key, None)
                    pending_call["await_raw_processing_start"] = True
                yield {
                    "event": "content",
                    "data": {"text": "已选择默认原始数据处理参数。回复“开始处理”后提交任务。"},
                }
                yield {
                    "event": "card",
                    "data": {
                        "type": "workflow-action",
                        "title": "原始处理参数已确定",
                        "summary": "将使用默认窗口、阈值、质量筛选和类别比例设置。",
                        "data": {"action": "开始处理", "label": "开始处理"},
                    },
                }
                return
            if choice == "modify":
                self._pending_timeseries_input[session_key] = {"stage": "raw-processing-config"}
                values = {
                    key: workflow.get("raw_processing_config", {}).get(key, default)
                    for key, _label, default, _help in RAW_PROCESSING_PARAMETER_DEFS
                }
                yield {
                    "event": "card",
                    "data": {
                        "type": "raw-processing-parameters",
                        "title": "原始数据处理参数",
                        "summary": "可编辑处理参数；留空的字段恢复默认值。系统会校验数值范围。",
                        "data": {"parameters": [
                            {"key": key, "label": label, "value": value, "help": help_text, **RAW_PROCESSING_PARAMETER_LIMITS.get(key, {})}
                            for key, label, _default, help_text in RAW_PROCESSING_PARAMETER_DEFS
                            for value in [values[key]]
                        ]},
                    },
                }
                yield {
                    "event": "content",
                    "data": {"text": "请描述要修改的原始处理参数，支持 JSON 或自然语言，例如 configs=[\"180:60\"]、spo2_threshold=92、max_non_hypo_ratio=1.0。"},
                }
                return
            if choice == "__other__":
                workflow["llm_intervention_stage"] = "raw-processing-config"
                yield {
                    "event": "content",
                    "data": {"text": "请直接描述原始数据处理需求，我会让大模型参与当前步骤。"},
                }
                return
        if stage == "workflow-kind":
            if choice == "train":
                for event in self._queue_timeseries_choice(
                    session_key, "model", "选择模型架构",
                    "请选择可训练的目录模型、已注册的模型配置，或创建新的模型。已训练模型请从评测入口选择。",
                    self._timeseries_model_selection_candidates(),
                ):
                    yield event
                return
            if choice == "evaluate":
                candidates = self._timeseries_trained_model_candidates()
                if not candidates:
                    yield {"event": "content", "data": {"text": "当前没有可加载的已训练模型。请先选择模型架构并完成训练。"}}
                    for event in self._queue_timeseries_choice(
                        session_key, "workflow-kind", "选择低氧血症后续操作",
                        "请选择训练新模型，或加载已训练模型进行评测。",
                        [
                            {"id": "train", "name": "选择模型架构并训练"},
                            {"id": "evaluate", "name": "选择已训练模型进行评测"},
                            {"id": "__other__", "name": "其它（交给大模型）"},
                        ],
                    ):
                        yield event
                    return
                for event in self._queue_timeseries_choice(
                    session_key, "trained-model", "选择已训练模型",
                    "请选择已保存的训练产物，加载后进入评测指标和结果输出设置。",
                    candidates,
                ):
                    yield event
                return
        if stage == "trained-model":
            workflow["model_id"] = choice
            workflow["model_name"] = selected.get("model_name") or choice
            workflow["architecture_saved"] = bool(selected.get("resource"))
            workflow["artifact_path"] = str(Path(selected.get("model_path", "")).parent)
            workflow["model_trainable"] = False
            for event in self._queue_timeseries_choice(
                session_key, "trained-model-action", "已训练模型操作",
                "已加载训练产物。可以直接进入评测，也可以删除该模型资源。",
                [
                    {"id": "evaluate", "name": "进入评测流程"},
                    {"id": "delete", "name": "删除此模型资源"},
                    {"id": "__other__", "name": "其它（交给大模型）"},
                ],
            ):
                yield event
            return
        if stage == "model" and choice == "__other__":
            self._pending_timeseries_input[session_key] = {"stage": "custom-model-description"}
            yield {"event": "content", "data": {"text": self._custom_model_prompt()}}
            return
        if stage == "training-action" and choice == "__other__":
            self._pending_timeseries_input[session_key] = {"stage": "training-config"}
            yield {"event": "content", "data": {"text": self._training_parameter_prompt()}}
            return
        if stage == "architecture-action" and choice == "__other__":
            self._pending_timeseries_input[session_key] = {"stage": "architecture-config"}
            yield {"event": "content", "data": {"text": self._architecture_parameter_prompt()}}
            return
        if choice == "__other__":
            workflow["llm_intervention_stage"] = stage
            yield {"event": "content", "data": {"text": "请直接描述你的其它要求，我会让大模型参与当前步骤。"}}
            return
        if stage == "validation-next":
            for event in self._queue_timeseries_choice(
                session_key, "model", "选择低氧血症模型",
                "数据检查通过。可选择目录模型、已有自定义模型，或创建新的模型。",
                self._timeseries_model_selection_candidates(),
            ):
                yield event
            return
        if stage == "model":
            if choice == "__create__":
                self._pending_timeseries_input[session_key] = {"stage": "custom-model-description"}
                yield {"event": "content", "data": {"text": self._custom_model_prompt()}}
                return
            workflow["model_id"] = choice
            workflow["model_name"] = selected.get("model_name") or choice
            if isinstance(selected.get("model_config"), dict):
                workflow["model_config"] = dict(selected["model_config"])
            if isinstance(selected.get("training_config"), dict):
                workflow["training_config"] = dict(selected["training_config"])
            workflow["model_trainable"] = selected.get("runtime_status") != "design-submitted"
            if selected.get("runtime_status") == "trained" and selected.get("model_path"):
                workflow["artifact_path"] = str(Path(selected["model_path"]).parent)
            if selected.get("runtime_status") == "trained":
                for event in self._queue_timeseries_choice(
                    session_key, "trained-model-action", "已训练模型",
                    "可直接加载现有产物评测，也可修改配置重新训练或删除模型资源。",
                    [
                        {"id": "evaluate", "name": "直接评测已有模型"},
                        {"id": "retrain", "name": "修改参数并重新训练"},
                        {"id": "delete", "name": "删除此模型资源"},
                        {"id": "__other__", "name": "其它（交给大模型）"},
                    ],
                ):
                    yield event
                return
            params_card = await self._execute_tool(
                {"id": "timeseries-classification-runner"}, "查看模型参数", [],
                {"mode": "model-parameters", "model_id": workflow.get("model_id", "")},
            )
            if params_card and params_card.get("status") == "success":
                yield {"event": "card", "data": {
                    "type": "model-parameters", "title": "当前模型架构详情",
                    "summary": "这里显示当前模型的架构参数；架构修改请用自然语言描述，不同模型的可用字段以当前模型为准。",
                    "data": params_card.get("data", {}),
                }}
            for event in self._queue_timeseries_choice(
                session_key, "architecture-action", "模型架构参数",
                "模型已选择。可使用默认架构、修改层数和结构参数，或注册为可复用模型资源。",
                [
                    {"id": "default", "name": "使用当前架构参数"},
                    {"id": "modify", "name": "修改架构参数（层数、维度、注意力头等）"},
                    {"id": "register", "name": "注册当前模型配置"},
                    {"id": "__other__", "name": "其它（交给大模型）"},
                ],
            ):
                yield event
            return
        if stage == "trained-model-action":
            if choice == "evaluate":
                for event in self._queue_evaluation_choice(session_key):
                    yield event
                return
            if choice == "delete":
                result = await self._execute_tool(
                    {"id": "timeseries-classification-runner"}, "删除已注册模型", [],
                    {"mode": "delete-model", "model_id": workflow.get("model_id", "")},
                )
                yield {"event": "content", "data": {"text": (result or {}).get("message", "删除模型失败")}}
                workflow.pop("model_id", None)
                workflow.pop("model_name", None)
                workflow.pop("artifact_path", None)
                for event in self._queue_timeseries_choice(
                    session_key, "model", "选择低氧血症模型",
                    "请选择其它模型，或创建新模型。", self._timeseries_model_selection_candidates(),
                ):
                    yield event
                return
            workflow.pop("artifact_path", None)
            for event in self._queue_timeseries_choice(
                session_key, "architecture-action", "模型架构参数",
                "请选择使用当前参数、修改架构参数或注册配置。",
                [
                    {"id": "default", "name": "使用当前架构参数"},
                    {"id": "modify", "name": "修改架构参数"},
                    {"id": "register", "name": "注册当前模型配置"},
                    {"id": "__other__", "name": "其它（交给大模型）"},
                ],
            ):
                yield event
            return
        if stage == "training-artifact":
            if choice == "save":
                result = await self._execute_tool(
                    {"id": "timeseries-classification-runner"}, "保存训练模型", [], {
                        "mode": "inspect-model", "artifact_path": workflow.get("artifact_path", ""),
                        "register_model": True, "dataset_id": workflow.get("dataset_id", ""),
                        "model_resource_name": workflow.get("model_resource_name") or workflow.get("model_name", ""),
                    },
                )
                if not result or result.get("status") != "success":
                    yield {"event": "content", "data": {"text": (result or {}).get("message", "训练模型保存失败")}}
                    self._pending_timeseries_choice[session_key] = pending
                    return
                resource_id = result.get("data", {}).get("model_id", "")
                if not resource_id:
                    yield {"event": "content", "data": {"text": "模型保存未返回模型 ID，请重试保存。"}}
                    self._pending_timeseries_choice[session_key] = pending
                    return
                workflow["trained_model_id"] = resource_id
                # Evaluation must use the registered training artifact,
                # not the configured architecture resource.
                workflow["model_id"] = resource_id
                workflow["model_name"] = result.get("data", {}).get(
                    "name", workflow.get("model_name", resource_id)
                )
                yield {"event": "content", "data": {"text": f"训练模型已保存到模型空间：{resource_id}"}}
            else:
                # A one-off evaluation uses the artifact path directly.
                workflow["model_id"] = ""
            workflow["save_trained_model"] = choice == "save"
            for event in self._queue_evaluation_choice(session_key):
                yield event
            return
        if stage == "architecture-action" and choice == "default":
            for event in self._queue_architecture_save_choice(session_key):
                yield event
            return
        if stage == "architecture-save":
            if choice == "save" and not workflow.get("architecture_saved"):
                created = await self._create_timeseries_model_resource(workflow)
                if created.get("status") != "success":
                    yield {"event": "content", "data": {"text": created.get("message", "模型配置保存失败")}}
                    return
                workflow["model_id"] = created["data"].get("model_id", workflow.get("model_id", ""))
                workflow["architecture_saved"] = True
                yield {"event": "content", "data": {"text": f"模型架构配置已保存：{workflow['model_id']}。"}}
            if not workflow.get("model_trainable", True):
                yield {"event": "content", "data": {"text": "当前模型只有设计说明，尚不能训练。"}}
                return
            for event in self._queue_training_choice(session_key):
                yield event
            return
        if stage == "architecture-action":
            if choice == "modify":
                self._pending_timeseries_input[session_key] = {"stage": "architecture-config"}
                yield {"event": "content", "data": {"text": self._architecture_parameter_prompt()}}
                return
            if choice == "register":
                created = await self._create_timeseries_model_resource(workflow)
                if created.get("status") != "success":
                    yield {"event": "content", "data": {"text": created.get("message", "模型注册失败")}}
                    return
                workflow["model_id"] = created["data"].get("model_id", workflow.get("model_id", ""))
                yield {"event": "content", "data": {"text": f"模型配置已注册：{workflow['model_id']}。"}}
            if not workflow.get("model_trainable", True):
                yield {"event": "content", "data": {"text": "该模型仅有设计描述，尚无经过验证的可执行实现，暂不能训练。"}}
                return
            for event in self._queue_training_choice(session_key):
                yield event
            return
        if stage == "training-action":
            if choice == "modify":
                params_card = await self._execute_tool(
                    {"id": "timeseries-classification-runner"}, "查看模型参数", [],
                    {"mode": "model-parameters", "model_id": workflow.get("model_id", "")},
                )
                card_data = (
                    params_card.get("data", {})
                    if params_card and params_card.get("status") == "success"
                    else self._training_parameter_card_data(workflow)
                )
                yield {"event": "card", "data": {
                    "type": "training-parameters", "title": "训练超参数编辑",
                    "summary": "训练参数适用于当前训练流程；清空字段恢复默认值，提交后会进行类型和范围校验。",
                    "data": card_data,
                }}
                self._pending_timeseries_input[session_key] = {"stage": "training-config"}
                yield {"event": "content", "data": {"text": self._training_parameter_prompt()}}
                return
            workflow["workflow_ready"] = True
            yield {
                "event": "card",
                "data": {
                    "type": "workflow-action",
                    "title": "训练配置已确定",
                    "summary": "模型架构和训练参数已准备好。",
                    "data": {"action": "开始训练", "label": "开始训练"},
                },
            }
            yield {"event": "content", "data": {"text": self._workflow_ready_summary(workflow)}}
            return
        if stage == "evaluation-metrics":
            workflow["evaluation_metrics"] = list(EVALUATION_METRICS) if choice == "all" else [choice]
            for event in self._queue_timeseries_choice(
                session_key, "evaluation-export", "评测结果输出",
                "请选择是否导出 Excel。训练模型的保存选项已在训练完成后确认。",
                [
                    {"id": "excel", "name": "评测并导出 Excel"},
                    {"id": "display", "name": "仅显示评测结果"},
                ],
            ):
                yield event
            return
        if stage == "evaluation-export":
            workflow["export_excel"] = choice == "excel"
            workflow["evaluation_ready"] = True
            yield {"event": "card", "data": {
                "type": "workflow-action", "title": "评测设置已保存",
                "summary": "评测指标和结果输出方式已准备好。",
                "data": {"action": "开始评测", "label": "开始评测"},
            }}
            yield {"event": "content", "data": {"text": "评测设置已保存，开始评测即可运行。"}}
            return


    async def _handle_timeseries_input(
        self, session_key: str, pending: dict, content: str
    ) -> AsyncGenerator[dict, None]:
        stage = pending.get("stage", "")
        workflow = self._timeseries_workflows.setdefault(session_key, {})
        normalized = content.strip().lower()
        is_default = normalized in {"1", "默认", "使用默认", "使用默认参数", "使用默认训练超参数"} or "选择第1" in content
        is_cancel = normalized in {"退出", "取消", "返回", "重置", "取消当前操作"}
        if is_cancel:
            yield {"event": "content", "data": {"text": "已退出当前参数设置，可以重新开始低氧血症预测。"}}
            return
        if stage == "raw-processing-config":
            parsed = self._extract_raw_processing_params(content)
            empty_config = bool(re.search(
                r"(?:原始处理参数|raw[_ -]?processing[_ -]?parameters?)\s*[:：=]\s*\{\s*\}",
                content,
                re.IGNORECASE,
            )) or normalized in {"默认", "使用默认参数", "恢复默认", "清空参数", "全部删除"}
            if empty_config:
                workflow["raw_processing_config"] = {}
                pending_call = self._pending_calls.get(session_key)
                if pending_call:
                    for key, _label, _default, _help in RAW_PROCESSING_PARAMETER_DEFS:
                        pending_call["params"].pop(key, None)
                    pending_call["await_raw_processing_start"] = True
                workflow["raw_processing_config_confirmed"] = True
                yield {
                    "event": "content",
                    "data": {"text": "已清空自定义原始处理参数，全部恢复默认值。回复“开始处理”提交任务。"},
                }
                yield {
                    "event": "card",
                    "data": {
                        "type": "workflow-action",
                        "title": "已恢复默认处理参数",
                        "summary": "空白字段和已删除字段将使用系统默认值。",
                        "data": {"action": "开始处理", "label": "开始处理"},
                    },
                }
                return
            if not parsed:
                self._pending_timeseries_input[session_key] = pending
                yield {
                    "event": "content",
                    "data": {"text": "未识别到原始处理参数。请使用 JSON，或描述窗口长度、预测间隔、低氧阈值、非低氧比例等设置。"},
                }
                return
            workflow["raw_processing_config"] = {
                **workflow.get("raw_processing_config", {}),
                **parsed,
            }
            pending_call = self._pending_calls.get(session_key)
            if pending_call:
                pending_call["params"].update(parsed)
                pending_call["await_raw_processing_start"] = True
            workflow["raw_processing_config_confirmed"] = True
            yield {
                "event": "content",
                "data": {
                    "text": f"已记录原始处理参数：\n{self._format_raw_processing_config(parsed)}\n\n回复“开始处理”提交任务。",
                },
            }
            yield {
                "event": "card",
                "data": {
                    "type": "workflow-action",
                    "title": "原始处理参数已保存",
                    "summary": "参数已通过范围校验，确认后即可提交原始数据处理任务。",
                    "data": {"action": "开始处理", "label": "开始处理"},
                },
            }
            return
        if stage == "architecture-config" and (
            is_default or "使用当前架构参数" in content or "完成架构设置" in content
        ):
            for event in self._queue_architecture_save_choice(session_key):
                yield event
            return
        if stage == "architecture-config" and (
            is_default or normalized in {"使用当前架构参数", "保持当前架构参数", "使用当前参数", "完成架构设置，进入训练参数"}
            or "使用当前架构参数" in content
        ):
            for event in self._queue_training_choice(session_key):
                yield event
            return
        if stage == "training-config" and is_default:
            workflow["workflow_ready"] = True
            yield {"event": "content", "data": {"text": self._workflow_ready_summary(workflow)}}
            return
        if stage == "custom-model-description":
            enriched_description, base_model = await self._llm_complete_custom_model_description(content)
            extracted = self._extract_timeseries_model_params(content)
            requested_name = ""
            name_match = re.search(r"(?:名为|模型名称\s*[：:]|名称为|叫做|命名为)\s*([A-Za-z][A-Za-z0-9_-]*)", content)
            if name_match:
                requested_name = name_match.group(1)
            params = {
                "description": content,
                "structured_description": enriched_description,
                "dataset_id": workflow.get("dataset_id", ""),
                "register_model": True,
                "model_resource_name": requested_name,
                "model_config": extracted.get("model_config", {}),
                "training_config": extracted.get("training_config", {}),
            }
            if base_model:
                params["base_model"] = base_model
            result = await self._execute_tool(
                {"id": "timeseries-classification-runner"}, content, [],
                {**params, "mode": "custom-model-design"},
            )
            data = (result or {}).get("data", {})
            if result and result.get("status") == "success":
                workflow["model_id"] = data.get("model_id") or data.get("design", {}).get("name", "")
                workflow["model_name"] = data.get("model_name") or base_model or data.get("design", {}).get("name", "")
                workflow["model_resource_name"] = data.get("name") or requested_name or data.get("design", {}).get("name", "")
                if params.get("model_config"):
                    workflow["model_config"] = params["model_config"]
                if params.get("training_config"):
                    workflow["training_config"] = params["training_config"]
                workflow["model_trainable"] = bool(data.get("trainable"))
                yield {"event": "content", "data": {"text": self._format_custom_model_design(result)}}
                if not workflow["model_trainable"]:
                    yield {"event": "content", "data": {"text": "该全新结构已注册为设计资源。生成并验证 PyTorch 实现后才能进入训练。"}}
                    return
                # Return the executable base model's complete, effective
                # parameter schema immediately after registration.  This
                # keeps the architecture confirmation step self-contained.
                params_card = await self._execute_tool(
                    {"id": "timeseries-classification-runner"}, "查看模型参数", [],
                    {"mode": "model-parameters", "model_id": workflow.get("model_id", "")},
                )
                if params_card and params_card.get("status") == "success":
                    yield {"event": "card", "data": {
                        "type": "model-parameters", "title": "当前模型架构详情",
                        "summary": "已注册模型的有效架构参数。架构修改请用自然语言描述。",
                        "data": params_card.get("data", {}),
                    }}
                for event in self._queue_timeseries_choice(
                    session_key, "architecture-action", "模型架构参数",
                    "自定义模型已注册。请选择是否继续修改可执行基础模型的参数。",
                    [
                        {"id": "default", "name": "使用当前架构参数"},
                        {"id": "modify", "name": "继续修改架构参数"},
                        {"id": "__other__", "name": "其它（交给大模型）"},
                    ],
                ):
                    yield event
            else:
                yield {"event": "content", "data": {"text": (result or {}).get("message", "自定义模型解析失败")}}
            return
        parsed = self._extract_timeseries_model_params(content)
        if stage == "architecture-config":
            workflow["model_config"] = {**workflow.get("model_config", {}), **parsed.get("model_config", {})}
            if parsed.get("training_config"):
                workflow["training_config"] = {**workflow.get("training_config", {}), **parsed["training_config"]}
            if not parsed.get("model_config"):
                self._pending_timeseries_input[session_key] = pending
                yield {"event": "content", "data": {"text": "未识别到架构参数。请使用 JSON，或描述层数、隐藏维度、注意力头、dropout 等。"}}
                return
            yield {"event": "content", "data": {"text": f"已记录架构参数：\n```json\n{json.dumps(workflow['model_config'], ensure_ascii=False, indent=2)}\n```"}}
            for event in self._queue_timeseries_choice(
                session_key, "architecture-action", "模型架构参数",
                "架构覆盖已保存。可继续修改、注册当前模型配置，或进入训练参数设置。",
                [
                    {"id": "default", "name": "完成架构设置，进入训练参数"},
                    {"id": "modify", "name": "继续修改架构参数"},
                    {"id": "register", "name": "注册当前模型配置"},
                    {"id": "__other__", "name": "其它（交给大模型）"},
                ],
            ):
                yield event
            return
        if stage == "training-config":
            if content.strip().lower() in {"继续", "返回参数卡", "修改参数", "重新修改"}:
                params_card = await self._execute_tool(
                    {"id": "timeseries-classification-runner"}, "查看训练参数", [],
                    {"mode": "model-parameters", "model_id": workflow.get("model_id", "")},
                )
                card_data = (
                    params_card.get("data", {})
                    if params_card and params_card.get("status") == "success"
                    else self._training_parameter_card_data(workflow)
                )
                yield {"event": "card", "data": {
                    "type": "training-parameters", "title": "训练超参数编辑",
                    "summary": "请编辑训练参数；清空字段恢复默认值。",
                    "data": card_data,
                }}
                self._pending_timeseries_input[session_key] = pending
                yield {"event": "content", "data": {"text": self._training_parameter_prompt()}}
                return
            if not parsed.get("training_config") and "学习率" in content and any(marker in content for marker in ("一半", "减半", "半")):
                current_lr = float((workflow.get("training_config") or {}).get("learning_rate", 0.00025))
                parsed["training_config"] = {"learning_rate": current_lr / 2}
            workflow["training_config"] = {**workflow.get("training_config", {}), **parsed.get("training_config", {})}
            if not parsed.get("training_config"):
                self._pending_timeseries_input[session_key] = pending
                yield {"event": "content", "data": {"text": "未识别到训练参数。请使用 JSON，或描述优化器、轮数、批次、学习率、早停等。"}}
                return
            yield {"event": "content", "data": {"text": f"已记录训练参数：\n```json\n{json.dumps(workflow['training_config'], ensure_ascii=False, indent=2)}\n```"}}
            workflow["workflow_ready"] = True
            yield {"event": "content", "data": {"text": self._workflow_ready_summary(workflow)}}


    def _queue_architecture_save_choice(self, session_key: str) -> list[dict]:
        return self._queue_timeseries_choice(
            session_key, "architecture-save", "保存模型架构配置",
            "可以保存当前架构参数供后续训练复用，或只用于本次训练。",
            [
                {"id": "save", "name": "保存架构配置"},
                {"id": "skip", "name": "本次使用，不保存架构配置"},
            ],
        )


    def _queue_training_choice(self, session_key: str) -> list[dict]:
        return self._queue_timeseries_choice(
            session_key, "training-action", "训练超参数",
            "请选择使用默认训练参数或修改优化器、学习率、批次、早停等设置。",
            [
                {"id": "default", "name": "使用默认训练超参数"},
                {"id": "modify", "name": "修改训练超参数"},
                {"id": "__other__", "name": "其它（交给大模型）"},
            ],
        )


    def _queue_raw_processing_choice(self, session_key: str) -> list[dict]:
        return self._queue_timeseries_choice(
            session_key,
            "raw-processing-config-action",
            "原始数据处理参数",
            "可以使用默认处理参数，也可以修改窗口、预测间隔、低氧阈值和类别比例等设置。",
            [
                {"id": "default", "name": "使用默认处理参数"},
                {"id": "modify", "name": "修改处理参数"},
                {"id": "__other__", "name": "其它（交给大模型）"},
            ],
        )


    @staticmethod
    def _format_raw_processing_config(config: dict) -> str:
        labels = {key: label for key, label, _default, _help in RAW_PROCESSING_PARAMETER_DEFS}
        lines = []
        for key, value in config.items():
            lines.append(f"- {labels.get(key, key)}（{key}）：{value}")
        return "\n".join(lines) if lines else "（全部使用默认值）"


    def _queue_training_artifact_choice(self, session_key: str, task: dict) -> list[dict]:
        workflow = self._timeseries_workflows.setdefault(session_key, {})
        artifact_path = str(task.get("metadata", {}).get("artifact_path") or "")
        if not artifact_path:
            return [{"event": "content", "data": {"text": "训练任务已完成，但未找到模型产物路径，请检查任务日志。"}}]
        workflow["artifact_path"] = artifact_path
        return self._queue_timeseries_choice(
            session_key, "training-artifact", "保存训练模型",
            "训练已完成。保存后可在模型空间的已训练模型列表中复用；不保存仍可用本次产物评测。",
            [
                {"id": "save", "name": "保存训练模型到模型空间"},
                {"id": "skip", "name": "仅本次评测，不保存训练模型"},
            ],
        )


    def _queue_evaluation_choice(self, session_key: str) -> list[dict]:
        return self._queue_timeseries_choice(
            session_key, "evaluation-metrics", "选择评测指标",
            "默认使用原项目报告中的全部可用指标。也可以先选择一个重点指标。",
            [
                {"id": "all", "name": "全部指标（默认）"},
                {"id": "auc", "name": "AUC"}, {"id": "auprc", "name": "AUPRC"},
                {"id": "f1", "name": "F1"}, {"id": "accuracy", "name": "Accuracy"},
                {"id": "balanced_accuracy", "name": "Balanced Accuracy"},
                {"id": "precision", "name": "Precision"}, {"id": "recall", "name": "Recall"},
                {"id": "mcc", "name": "MCC"}, {"id": "__other__", "name": "其它（交给大模型）"},
            ],
        )


    async def _create_timeseries_model_resource(self, workflow: dict) -> dict:
        return await self._execute_tool(
            {"id": "timeseries-classification-runner"}, "注册当前低氧模型", [], {
                "mode": "create-model", "dataset_id": workflow.get("dataset_id", ""),
                "model_name": workflow.get("model_name") or workflow.get("model_id", ""),
                "model_config": workflow.get("model_config", {}),
                "training_config": workflow.get("training_config", {}),
            },
        ) or {"status": "failed", "message": "模型注册失败", "data": {}}


    @staticmethod
    def _architecture_parameter_prompt() -> str:
        return (
            "请描述要修改的架构参数，支持 JSON 或自然语言。常用项：`e_layers`、`d_layers`、"
            "`n_heads`、`d_model`、`d_ff`、`dropout`、`patch_len`、`stride`、`seg_len`、"
            "`moving_avg`、`top_k`、`p_hidden_dims`、`p_hidden_layers`，以及分解、下采样和图结构参数。"
        )


    @staticmethod
    def _training_parameter_prompt() -> str:
        return (
            "请描述训练参数，支持 JSON 或自然语言。可修改：`optimizer`（adamw/adam/sgd/rmsprop）、"
            "`epochs`、`batch_size`、`learning_rate`、`weight_decay`、`seed`、`pos_weight_scale`、"
            "`label_smoothing`、`grad_clip_norm`、EMA、早停、学习率调度和阈值指标。"
        )


    @staticmethod
    def _training_parameter_card_data(workflow: dict) -> dict:
        """Build a usable training editor even if the schema tool is unavailable."""
        return {
            "model_id": workflow.get("model_id", ""),
            "model_name": workflow.get("model_name", ""),
            "training": {
                **TIMESERIES_DEFAULT_TRAINING_CONFIG,
                **(workflow.get("training_config") or {}),
            },
            "training_groups": {
                "优化器": ["optimizer", "learning_rate", "weight_decay", "batch_size", "epochs", "seed"],
                "损失": ["pos_weight_scale", "label_smoothing", "grad_clip_norm"],
                "EMA": ["use_ema", "ema_momentum"],
                "早停": ["early_stop_patience", "early_stop_start_epoch", "selection_monitor"],
                "学习率调度": ["reduce_lr_patience", "reduce_lr_factor", "min_lr", "lr_monitor"],
                "阈值": ["threshold_metric"],
            },
        }


    @staticmethod
    def _custom_model_prompt() -> str:
        return (
            "请描述新模型：模型名称、模型来源（现有目录模型或全新结构）、编码器、时序聚合、"
            "分类头和训练设置。若基于现有模型（例如 DLinear/Transformer），注册后可直接训练；"
            "全新结构会先注册为设计资源，生成并验证实现后才能训练。"
        )


    async def _llm_complete_custom_model_description(self, description: str) -> tuple[str, str]:
        """Use the LLM to structure a design; never treat its text as executable code."""
        base_model = self._find_timeseries_model_name(description) or ""
        prompt = (
            "请将用户的五通道低氧血症模型需求整理成 JSON。只输出 JSON，字段为 "
            "name、base_model、encoder、temporal_module、classification_head、training。"
            "base_model 仅在用户明确以已有模型为基础时填写；不确定时留空。"
            "不要生成代码、虚构已实现的层，缺失信息留空。\n"
            f"用户需求：{description}"
        )
        try:
            response = await self.host.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=800,
            )
            match = re.search(r"\{.*\}", response, re.DOTALL)
            structured = json.loads(match.group(0)) if match else {}
            if not isinstance(structured, dict):
                return description, base_model
            candidate = str(structured.get("base_model") or "").strip()
            if not base_model and candidate in TIMESERIES_MODEL_NAMES:
                base_model = candidate
            lines = [
                f"模型名称：{structured.get('name') or 'custom_model'}",
                f"模型来源：{base_model or '自定义结构'}",
                "输入：256x5 五通道生理信号",
                f"编码器：{structured.get('encoder') or ''}",
                f"时序聚合：{structured.get('temporal_module') or ''}",
                f"分类头：{structured.get('classification_head') or ''}",
                f"训练：{structured.get('training') or ''}",
            ]
            return "\n".join(lines), base_model
        except Exception:
            return description, base_model


    @staticmethod
    def _workflow_ready_summary(workflow: dict) -> str:
        overrides_architecture = workflow.get("model_config") or {}
        overrides_training = workflow.get("training_config") or {}
        architecture = {
            **TIMESERIES_DEFAULT_MODEL_CONFIG,
            **model_architecture_defaults(workflow.get("model_name", "")),
            **overrides_architecture,
        }
        training = {**TIMESERIES_DEFAULT_TRAINING_CONFIG, **overrides_training}
        architecture_text = json.dumps(architecture, ensure_ascii=False, indent=2)
        training_text = json.dumps(training, ensure_ascii=False, indent=2)
        override_note = (
            "已应用用户覆盖项"
            if (overrides_architecture or overrides_training)
            else "未修改，使用所选模型当前架构参数；未修改，使用默认训练超参数"
        )
        return (
            "模型与训练配置已确定。\n\n"
            f"- 模型：`{workflow.get('model_id') or workflow.get('model_name')}`\n"
            f"- 配置状态：{override_note}\n"
            f"- 完整架构设置：\n```json\n{architecture_text}\n```\n"
            f"- 完整训练设置：\n```json\n{training_text}\n```\n"
            "回复“开始训练”后才会提交训练任务。"
        )


    @staticmethod
    def _format_timeseries_dataset_source(tool_result: dict) -> str:
        data = tool_result.get("data", {})
        if not isinstance(data, dict):
            return str(tool_result.get("message", ""))
        next_step = data.get("next_step", "")
        if data.get("dataset_source_type") == "raw":
            next_step = (
                f"{next_step}\n\n如果目录中包含 ZIP，系统会先解压再处理；请同时提供"
                "ZIP 临时解压目录（staging_root），并预留不少于 ZIP 展开大小加处理输出的磁盘空间。"
                "可用 JSON 修改窗口配置（configs，例如 [\"256:30\"]）、低氧阈值（spo2_threshold）、"
                "连续低氧时长（hypoxemia_duration_seconds）、非低氧比例（max_non_hypo_ratio）、"
                "缺失值阈值（nan_ratio_threshold）、PI 阈值和随机种子。"
            )
        return f"## 数据集类型已选择\n\n{tool_result.get('message', '')}\n\n{next_step}".strip()


    @staticmethod
    def _format_timeseries_model_configuration(tool_result: dict) -> str:
        """Render the selected resource configuration without LLM interpretation."""
        data = tool_result.get("data", {})
        configuration = data.get("training_config", {}) if isinstance(data, dict) else {}
        architecture = configuration.get("architecture", {}) if isinstance(configuration, dict) else {}
        training = configuration.get("training", {}) if isinstance(configuration, dict) else {}
        creation_type = data.get("creation_type", "")
        source_line = ""
        if creation_type == "derived-from-resource":
            source_line = f"派生来源：`{data.get('derived_from_model_id', '')}`\n\n"
        return (
            f"## {data.get('model_name', '当前模型')} 配置\n\n"
            f"模型资源 ID：`{data.get('model_id', '')}`\n\n"
            f"资源类型：`{creation_type or 'catalog-model'}`\n\n"
            f"{source_line}"
            f"### 架构参数\n\n```json\n{json.dumps(architecture, ensure_ascii=False, indent=2)}\n```\n\n"
            f"### 训练参数\n\n```json\n{json.dumps(training, ensure_ascii=False, indent=2)}\n```\n\n"
            "修改参数时，请创建新的模型资源并在消息中附上 JSON 配置，已有资源保持不变。"
        )


    @staticmethod
    def _format_timeseries_model_creation_guide(tool_result: dict) -> str:
        """Render the two creation paths without implying that arbitrary text is executable code."""
        data = tool_result.get("data", {})
        return str(data.get("text", "")) if isinstance(data, dict) else ""


    @staticmethod
    def _format_custom_model_design(tool_result: dict) -> str:
        """Render a submitted custom architecture as a reviewable, non-trainable design."""
        data = tool_result.get("data", {})
        design = data.get("design", {}) if isinstance(data, dict) else {}
        if not isinstance(design, dict):
            return "未能读取自定义模型设计。"
        sections = (
            ("输入", design.get("input", "未描述")),
            ("编码器", design.get("encoder", "未描述")),
            ("时序聚合", design.get("temporal_module", "未描述")),
            ("分类头", design.get("classification_head", "未描述")),
            ("训练", design.get("training", "未描述")),
        )
        details = "\n".join(f"- {label}：{value}" for label, value in sections)
        missing = data.get("missing_sections", [])
        missing_text = f"\n\n仍需补充：{'、'.join(missing)}。" if missing else ""
        registered = data.get("model_id")
        if registered:
            status_text = (
                f"状态：已注册为模型资源 `{registered}`，"
                + ("可进入参数配置和训练。" if data.get("trainable") else "仍需生成并验证 PyTorch 实现后才能训练。")
            )
        else:
            status_text = "状态：结构描述已接收，尚未注册。"
        return (
            f"## 自定义模型设计：{design.get('name', '未命名')}\n\n"
            f"{details}\n\n"
            f"{status_text}"
            f"{missing_text}"
        )


    @staticmethod
    def _find_timeseries_model_name(query: str) -> str | None:
        normalized = query.lower()
        for model_name in sorted(TIMESERIES_MODEL_NAMES, key=len, reverse=True):
            if model_name.lower() in normalized:
                return model_name
        return None


    def _apply_timeseries_workflow_context(
        self, session_key: str, tool: dict, mode: str | None, params: dict
    ) -> dict:
        if hasattr(self, "_timeseries_workflows"):
            self._workflow_states = self._timeseries_workflows
        return self.apply_context(session_key, tool, mode, params)


    @staticmethod
    def _extract_timeseries_path(query: str) -> str:
        value = query.strip()
        value = re.split(
            r"(?:[，,；;]\s*|\s+)(?=(?:source_path|output_root|staging_root|原始数据|输出目录|ZIP|临时解压|解压))",
            value,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]

        # Users commonly paste a quoted Windows path followed by an explanation,
        # e.g. `"D:\\data\\windows"这是数据文件路径`. Keep only the quoted path.
        quoted = re.search(
            r"[\"']((?:[A-Za-z]:[\\/]|[\\/]{1,2})[^\"'\r\n]+)[\"']",
            value,
        )
        if quoted:
            return quoted.group(1).strip()

        # For an unquoted path, remove a prose suffix when it is separated from
        # the path by whitespace. Path components themselves may contain spaces.
        match = re.search(r"((?:[A-Za-z]:[\\/]|[\\/]{1,2})[^\r\n]+)", value)
        if not match:
            return ""
        candidate = match.group(1).strip().strip('"\'')
        candidate = re.split(
            r"\s+(?=(?:这是|这里|数据|文件|路径|目录|请|然后))",
            candidate,
            maxsplit=1,
        )[0]
        return candidate.rstrip("，。；,.;:：")


    @staticmethod
    def _extract_timeseries_staging_root(query: str) -> str:
        """Extract an optional ZIP staging directory from raw-data instructions."""
        value = query.strip()

        # Accept an explicit JSON/object-style parameter in chat text.
        for candidate in re.findall(r"\{[^{}]*\}", value):
            try:
                config = json.loads(candidate)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(config, dict):
                for key in ("staging_root", "stagingRoot"):
                    raw = config.get(key)
                    if isinstance(raw, str) and raw.strip():
                        return raw.strip().strip('"\'')

        # Chinese and English labels commonly used in the raw-data workflow.
        label = (
            r"(?:staging[_ -]?root|ZIP\s*(?:temporary\s*)?(?:extract(?:ion)?|unpack)\s*"
            r"(?:directory|path)?|临时解压目录|ZIP临时解压目录|解压临时目录)"
        )
        match = re.search(
            rf"{label}\s*(?:是|为|设置为|路径为|path)?\s*[:：=]?\s*[\"']?"
            r"((?:[A-Za-z]:[\\/]|[\\/]{1,2})[^\"'\r\n]+)",
            value,
            re.IGNORECASE,
        )
        if not match:
            return ""
        candidate = match.group(1).strip().strip('"\'')
        # Stop before another labelled argument or normal explanatory prose.
        candidate = re.split(
            r"\s+(?=(?:source_path|output_root|staging_root|原始数据|输出目录|ZIP|临时解压|解压))",
            candidate,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        return candidate.rstrip("，。；;,. ")


    @staticmethod
    def _raw_source_contains_zip(source_path: str) -> bool:
        """Return whether a raw source is a ZIP or contains ZIP archives."""
        try:
            source = Path(str(source_path)).expanduser()
            if source.is_file():
                return source.suffix.lower() == ".zip"
            return source.is_dir() and any(
                item.is_file() and item.suffix.lower() == ".zip"
                for item in source.rglob("*")
            )
        except (OSError, ValueError):
            return False


    @staticmethod
    def _extract_raw_processing_params(query: str) -> dict:
        """Extract bounded raw-data pipeline settings from JSON or clear labels."""
        allowed = {
            "configs", "lookback_seconds", "window_seconds", "sequence_length",
            "prediction_interval_seconds", "interval_seconds", "min_segment_seconds",
            "spo2_threshold", "hypo_operator", "drop_spo2_na_before_segment",
            "spo2_na_break_segment", "crop_seconds", "edge_trim_mode",
            "nan_ratio_threshold", "fill_method", "apply_physiologic_bounds",
            "pi_threshold", "low_pi_ratio_threshold", "hypoxemia_duration_seconds",
            "preprocess_hypo_operator", "window_hypo_operator", "min_hypo_duration_seconds",
            "max_non_hypo_ratio", "negative_positive_ratio", "seed",
        }
        result: dict = {}
        for candidate in re.findall(r"\{[^{}]*\}", query):
            try:
                parsed = json.loads(candidate)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                result.update({key: value for key, value in parsed.items() if key in allowed})

        # Also accept compact forms used in chat examples, including multiple
        # windows and Chinese labels such as "窗口配置：180:60".
        config_match = re.search(
            r"(?:configs?|窗口配置|窗口设置)\s*[:：=]\s*(\[[^\]]*\]|[\"']?\d+\s*:\s*\d+(?:\s*,\s*\d+\s*:\s*\d+)*[\"']?)",
            query,
            re.IGNORECASE,
        )
        if config_match:
            raw = config_match.group(1).strip().strip("\"'")
            if raw.startswith("["):
                try:
                    decoded = json.loads(raw.replace("'", '"'))
                except (TypeError, json.JSONDecodeError):
                    decoded = [item.strip().strip("\"'") for item in raw[1:-1].split(",") if item.strip()]
            else:
                decoded = [item.strip().strip("\"'") for item in raw.split(",") if item.strip()]
            result["configs"] = [re.sub(r"\s+", "", str(item)) for item in decoded]

        # Natural language window and prediction interval can be supplied
        # together without the internal parameter names.
        window_match = re.search(r"窗口(?:长度|大小)?\s*(?:改为|设置为|为|是|：|:)\s*(\d+)\s*秒", query)
        interval_match = re.search(r"(?:预测间隔|预测提前量)\s*(?:改为|设置为|为|是|：|:)\s*(\d+)\s*秒", query)
        if window_match and interval_match and "configs" not in result:
            result["configs"] = [f"{int(window_match.group(1))}:{int(interval_match.group(1))}"]

        patterns = (
            ("lookback_seconds", r"(?:lookback_seconds|window_seconds|窗口长度|窗口大小)\s*[:：=]?\s*(\d+)", int),
            ("prediction_interval_seconds", r"(?:prediction_interval_seconds|interval_seconds|预测间隔|预测提前量)\s*[:：=]?\s*(\d+)", int),
            ("spo2_threshold", r"(?:spo2_threshold|SpO2阈值|低氧阈值)\s*[:：=]?\s*(\d+(?:\.\d+)?)", float),
            ("max_non_hypo_ratio", r"(?:max_non_hypo_ratio|negative_positive_ratio|非低氧(?:窗口)?比例)\s*[:：=]?\s*(\d+(?:\.\d+)?)", float),
            ("hypoxemia_duration_seconds", r"(?:hypoxemia_duration_seconds|低氧持续时间)\s*[:：=]?\s*(\d+)", int),
            ("min_hypo_duration_seconds", r"(?:min_hypo_duration_seconds|最小低氧持续时间)\s*[:：=]?\s*(\d+)", int),
            ("window_hypo_operator", r"(?:window_hypo_operator|窗口标签比较方式|窗口低氧比较方式)\s*[:：=]?\s*(le|lt|<=|<)", lambda value: "le" if value in {"le", "<="} else "lt"),
            ("seed", r"(?:seed|随机种子)\s*[:：=]?\s*(\d+)", int),
        )
        for key, pattern, converter in patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                result[key] = converter(match.group(1))
        return result


    def _extract_timeseries_model_params(self, query: str) -> dict:
        """Extract explicit model names and JSON configuration without asking the LLM to guess values."""
        result: dict = {}
        model_name = self._find_timeseries_model_name(query)
        if model_name:
            result["model_name"] = model_name
        model_id_match = re.search(r"\bmodel-[A-Za-z0-9_-]+\b", query)
        if model_id_match:
            result["source_model_id"] = model_id_match.group(0)

        # Explicit JSON lets advanced users adjust any field that the tool's bounded schema allows.
        for candidate in re.findall(r"\{[^{}]*\}", query):
            try:
                config = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if not isinstance(config, dict):
                continue
            architecture_keys = {
                "d_model", "n_heads", "e_layers", "d_layers", "d_ff", "dropout", "activation",
                "patch_len", "stride", "top_k", "num_kernels", "moving_avg", "seg_len",
                "p_hidden_dims", "p_hidden_layers", "embed", "freq", "factor", "distil", "individual",
                "channel_independence", "down_sampling_layers", "down_sampling_window",
                "down_sampling_method", "use_norm", "decomp_method", "alpha", "top_p", "pos",
                "node_dim", "gcn_depth", "propalpha", "conv_channel", "skip_channel",
                "subgraph_size", "ratio", "expand", "d_conv",
            }
            training_keys = {
                "optimizer", "epochs", "batch_size", "learning_rate", "weight_decay", "seed",
                "pos_weight_scale", "use_ema",
                "ema_momentum", "label_smoothing", "grad_clip_norm", "early_stop_patience",
                "early_stop_start_epoch", "reduce_lr_patience", "reduce_lr_factor", "min_lr",
                "selection_monitor", "lr_monitor", "threshold_metric",
            }
            # The editable parameter card submits explicit model_config and
            # training_config objects.  Accept those namespaces as well as
            # the flat JSON form used by advanced users.
            nested_architecture = config.get("model_config") if isinstance(config.get("model_config"), dict) else {}
            nested_training = config.get("training_config") if isinstance(config.get("training_config"), dict) else {}
            architecture = {key: value for key, value in config.items() if key in architecture_keys}
            training = {key: value for key, value in config.items() if key in training_keys}
            architecture.update({key: value for key, value in nested_architecture.items() if key in architecture_keys})
            training.update({key: value for key, value in nested_training.items() if key in training_keys})
            if architecture:
                result["model_config"] = architecture
            if training:
                result["training_config"] = training
        architecture, training = self._extract_timeseries_natural_language_config(query)
        if architecture:
            result["model_config"] = {**result.get("model_config", {}), **architecture}
        if training:
            result["training_config"] = {**result.get("training_config", {}), **training}
        return result


    @staticmethod
    def _extract_timeseries_natural_language_config(query: str) -> tuple[dict, dict]:
        """Parse common, unambiguous Chinese/English hyperparameter phrases."""
        architecture: dict = {}
        training: dict = {}
        patterns = (
            (architecture, "d_model", r"(?:d[_ -]?model|隐藏(?:层)?维度|嵌入维度|模型维度)\s*(?:改为|设置为|调整为|设为|为|是|=|:|：)?\s*(\d+)", int),
            (architecture, "n_heads", r"(?:n[_ -]?heads?|注意力头(?:数)?)\s*(?:改为|设置为|调整为|设为|为|是|=|:|：)?\s*(\d+)", int),
            (architecture, "e_layers", r"(?:e[_ -]?layers?|编码器层(?:数)?)\s*(?:改为|设置为|调整为|设为|为|是|=|:|：)?\s*(\d+)", int),
            (architecture, "d_layers", r"(?:d[_ -]?layers?|解码器层(?:数)?)\s*(?:改为|设置为|调整为|设为|为|是|=|:|：)?\s*(\d+)", int),
            (architecture, "d_ff", r"(?:d[_ -]?ff|前馈(?:层)?维度)\s*(?:改为|设置为|调整为|设为|为|是|=|:|：)?\s*(\d+)", int),
            (architecture, "dropout", r"(?:dropout|丢弃率)\s*(?:改为|设置为|调整为|设为|为|是|=|:|：)?\s*(0?(?:\.\d+)|1(?:\.0+)?)", float),
            (architecture, "patch_len", r"(?:patch[_ -]?len|补丁长度)\s*(?:改为|设置为|调整为|设为|为|是|=|:|：)?\s*(\d+)", int),
            (architecture, "stride", r"(?:stride|步长)\s*(?:改为|设置为|调整为|设为|为|是|=|:|：)?\s*(\d+)", int),
            (training, "epochs", r"(?:训练|训练轮数|epochs?)\s*(?:改为|设置为|调整为|设为|为|是|=|:|：)?\s*(\d+)\s*(?:轮|epochs?)?", int),
            (training, "batch_size", r"(?:batch[_ -]?size|批大小|批次大小)\s*(?:改为|设置为|调整为|设为|为|是|=|:|：)?\s*(\d+)", int),
            (training, "learning_rate", r"(?:learning[_ -]?rate|学习率)\s*(?:改为|设置为|调整为|设为|为|是|=|:|：)?\s*([0-9]+(?:\.\d+)?(?:e-?\d+)?)", float),
            (training, "weight_decay", r"(?:weight[_ -]?decay|权重衰减)\s*(?:改为|设置为|调整为|设为|为|是|=|:|：)?\s*([0-9]+(?:\.\d+)?(?:e-?\d+)?)", float),
        )
        for target, key, pattern, converter in patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                target[key] = converter(match.group(1))
        hidden_dims = re.search(
            r"(?:p_hidden_dims|分类头[^\n，。]*隐藏维度|隐藏维度)\s*(?:改为|设置为|调整为|设为|为|是|=|:|：)?\s*(\[\s*\d+(?:\s*[,，]\s*\d+)*\s*\])",
            query,
            re.IGNORECASE,
        )
        if hidden_dims:
            architecture["p_hidden_dims"] = [int(value) for value in re.findall(r"\d+", hidden_dims.group(1))]
        hidden_layers = re.search(
            r"(?:p_hidden_layers|分类头层数)\s*(?:为|是|=|:|：)?\s*(\d+)",
            query,
            re.IGNORECASE,
        ) or re.search(r"([一二两三四五六七八\d]+)\s*层(?:全连接)?分类头", query)
        if hidden_layers:
            chinese_numbers = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
            value = hidden_layers.group(1)
            architecture["p_hidden_layers"] = chinese_numbers.get(value, int(value) if value.isdigit() else 0)
        extra_training = (
            ("optimizer", r"(?:optimizer|优化器)\s*(?:使用|改为|设置为|调整为|为|是|=|:|：)?\s*(adamw|adam|sgd|rmsprop)", str.lower),
            ("seed", r"(?:seed|随机种子)\s*(?:改为|设置为|调整为|为|是|=|:|：)?\s*(\d+)", int),
            ("early_stop_patience", r"(?:early[_ -]?stop[_ -]?patience|早停\s*patience|早停耐心值)\s*(?:改为|设置为|调整为|为|是|=|:|：)?\s*(\d+)", int),
            ("early_stop_start_epoch", r"(?:early[_ -]?stop[_ -]?start[_ -]?epoch|早停开始轮数)\s*(?:改为|设置为|调整为|为|是|=|:|：)?\s*(\d+)", int),
            ("selection_monitor", r"(?:selection[_ -]?monitor|训练监控指标)\s*(?:为|是|=|:|：)?\s*(auc|accuracy|loss)", str.lower),
            ("threshold_metric", r"(?:threshold[_ -]?metric|阈值选择指标)\s*(?:为|是|=|:|：)?\s*(f1|accuracy|balanced)", str.lower),
        )
        for key, pattern, converter in extra_training:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                training[key] = converter(match.group(1))
        return architecture, training


    async def _extract_answer_params(self, answer: str, tool_id: str, param: dict) -> dict:
        """从用户回答中提取单个参数的值 — 直接使用用户输入作为参数值"""
        val = answer.strip()
        if tool_id == "timeseries-classification-runner" and param["name"] == "dataset_source_type":
            selected = self._resolve_tool_selection(answer, [
                {"id": "raw", "name": "原始数据"},
                {"id": "processed", "name": "已处理数据"},
                {"id": "__other__", "name": "其它（交给大模型）"},
            ])
            if selected:
                return {"dataset_source_type": selected["id"]}
            extracted = await self._extract_params_for_mode(answer, tool_id, "dataset-source")
            if extracted.get("dataset_source_type"):
                return extracted
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
        if tool_id == "timeseries-classification-runner":
            local_path = self._extract_timeseries_path(query)
            if local_path and mode in {"validate", "register-dataset", "process-raw-dataset"}:
                params.setdefault("source_path", local_path)
            if mode in {"dataset-source", "process-raw-dataset"}:
                staging_root = self._extract_timeseries_staging_root(query)
                if staging_root:
                    params["staging_root"] = staging_root
            if mode == "process-raw-dataset":
                params.update(self._extract_raw_processing_params(query))
            if mode == "dataset-source":
                if dataset_paths or local_path:
                    params["source_path"] = (dataset_paths or [local_path])[0]
                normalized = query.strip().lower()
                if "原始" in normalized:
                    params["dataset_source_type"] = "raw"
                elif any(
                    marker in normalized for marker in ("处理好", "已处理", "窗口数据", "v3")
                ):
                    params["dataset_source_type"] = "processed"
        if tool_id == "timeseries-classification-runner" and mode == "custom-model-design":
            params["description"] = query
        if tool_id == "timeseries-classification-runner" and mode in {"delete-model", "model-parameters"}:
            model_id = re.search(r"\bmodel-[A-Za-z0-9_-]+\b", query)
            if model_id:
                params["model_id"] = model_id.group(0)
        if tool_id == "timeseries-classification-runner" and mode in {"create-model", "derive-model", "train"}:
            params.update(self._extract_timeseries_model_params(query))
        return params


    def _resolve_dataset_paths(self, attachments):
        return self.host.resolve_dataset_paths(attachments)

    async def _execute_tool(self, tool, query, dataset_paths=None, pre_params=None):
        params = pre_params or {}
        if tool.get("id") == "timeseries-classification-runner":
            from core.timeseries import DatasetRef, EvaluationRequest, ModelRef, TrainingRequest

            mode = params.get("mode")
            if mode in {"train", "evaluate"}:
                dataset_id = str(params.get("dataset_id") or "")
                model_id = str(params.get("model_id") or "")
                dataset = DatasetRef(id=dataset_id)
                artifact_path = params.get("artifact_path")
                model = ModelRef(id=model_id, artifact_path=Path(artifact_path) if artifact_path else None)
                config = dict(params)
                if mode == "train":
                    return await self.timeseries_service.submit_training(
                        TrainingRequest(dataset, model, config=config)
                    )
                metrics = params.get("metrics")
                if isinstance(metrics, str):
                    metrics = tuple(item.strip() for item in metrics.split(",") if item.strip())
                elif isinstance(metrics, list):
                    metrics = tuple(metrics)
                else:
                    metrics = ()
                return await self.timeseries_service.evaluate(EvaluationRequest(
                    dataset, model, metrics=metrics, config=config,
                    metadata={"task_id": str(params.get("task_id") or ""),
                              "tool_id": tool["id"]},
                ))
        return await self.host.execute_tool(tool, query, dataset_paths, pre_params)

    async def _check_missing_params(self, tool_id, params, mode=None):
        return await self.host.check_missing_params(tool_id, params, mode)

    def _get_tool_modes(self, tool_id):
        return self.host.get_tool_modes(tool_id)

    def _resolve_mode_selection(self, answer, modes):
        return self.host.resolve_mode_selection(answer, modes)

    def _resolve_tool_selection(self, answer, candidates):
        return self.host.resolve_tool_selection(answer, candidates)

    def _get_path_params(self, tool_id, mode=None):
        return self.host.get_path_params(tool_id, mode)

    async def _extract_params(self, query, tool_id, dataset_paths=None):
        return await self.host.extract_params(query, tool_id, dataset_paths)
