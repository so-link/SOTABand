"""Low-oxygen session state and reusable choice resolution."""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from core.workflow.base import WorkflowAdapter, WorkflowContext

from .profile import HypoxemiaProfile


LegacyHandler = Callable[..., AsyncIterator[dict[str, Any]]]


class HypoxemiaState:
    workflow_id = "hypoxemia-prediction"

    def __init__(
        self,
        profile: HypoxemiaProfile | None = None,
        legacy_handler: LegacyHandler | None = None,
    ):
        self.profile = profile or HypoxemiaProfile()
        self._legacy_handler = legacy_handler
        self._workflow_states: dict[str, dict[str, Any]] = {}
        self._pending_choices: dict[str, dict[str, Any]] = {}
        self._pending_inputs: dict[str, dict[str, Any]] = {}

    @property
    def workflow_states(self) -> dict[str, dict[str, Any]]:
        """Expose the map temporarily for compatibility with the old Agent."""
        return self._workflow_states

    @property
    def pending_choices(self) -> dict[str, dict[str, Any]]:
        return self._pending_choices

    @property
    def pending_inputs(self) -> dict[str, dict[str, Any]]:
        return self._pending_inputs

    def state_for(self, session_id: str) -> dict[str, Any]:
        return self._workflow_states.setdefault(session_id or "default", {})

    def owns_message(self, message: str, has_workflow_context: bool = False) -> bool:
        normalized = (message or "").strip().lower()
        if not normalized:
            return False
        return has_workflow_context or any(
            marker.lower() in normalized for marker in self.profile.domain_markers
        )

    def detect(self, message: str, context: WorkflowContext) -> bool:
        return self.owns_message(
            message,
            bool(self._workflow_states.get(context.session_id or "default")),
        )

    def reset_session(self, session_id: str) -> None:
        key = session_id or "default"
        self._workflow_states.pop(key, None)
        self._pending_choices.pop(key, None)
        self._pending_inputs.pop(key, None)

    def reset(self, state: dict[str, Any]) -> None:
        state.clear()

    def apply_context(
        self,
        session_id: str,
        tool: dict[str, Any],
        mode: str | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Fill parameters from the active workflow session.

        The platform Agent supplies the session and tool envelope; all
        low-oxygen resource chaining stays behind this adapter boundary.
        """
        if tool.get("id") != self.profile.tool_id or not mode:
            return params
        context = self.state_for(session_id)
        completed = dict(params)
        if mode == "register-dataset" and not completed.get("source_path"):
            completed["source_path"] = context.get("processed_dataset_path") or context.get("source_path", "")
        elif mode == "process-raw-dataset" and not completed.get("source_path"):
            completed["source_path"] = context.get("source_path", "")
        if mode == "process-raw-dataset" and not completed.get("staging_root"):
            completed["staging_root"] = context.get("staging_root", "")
        if mode == "process-raw-dataset" and isinstance(context.get("raw_processing_config"), dict):
            for key, value in context["raw_processing_config"].items():
                completed.setdefault(key, value)
        elif mode == "validate" and not completed.get("source_path"):
            completed["source_path"] = context.get("processed_dataset_path") or context.get("source_path", "")
        elif mode == "create-model" and not completed.get("dataset_id"):
            completed["dataset_id"] = context.get("dataset_id", "")
        elif mode == "derive-model":
            completed.setdefault("source_model_id", context.get("model_id", ""))
            completed.setdefault("dataset_id", context.get("dataset_id", ""))
        elif mode == "model-config" and not completed.get("model_id"):
            completed["model_id"] = context.get("model_id", "")
        elif mode == "model-parameters" and not completed.get("model_id"):
            completed["model_id"] = context.get("model_id") or context.get("model_name", "")
        elif mode == "delete-model" and not completed.get("model_id"):
            completed["model_id"] = context.get("model_id", "")
        elif mode == "train" and not completed.get("dataset"):
            # A manually supplied dataset can have an ID from its manifest
            # without being present in this runtime's registry. The scan
            # already validated the concrete path, so keep using it for
            # execution and retain the ID separately for provenance.
            completed["dataset"] = (
                context.get("processed_dataset_path")
                or context.get("source_path")
                or context.get("dataset_id", "")
            )
        if mode == "train":
            completed.setdefault("model_id", context.get("model_id", ""))
            for key in ("model_name", "model_config", "training_config"):
                if key not in completed and context.get(key) not in (None, "", {}):
                    completed[key] = context[key]
        elif mode == "evaluate":
            for target, source in (
                ("model_id", "model_id"), ("task_id", "task_id"),
                ("artifact_path", "artifact_path"), ("metrics", "evaluation_metrics"),
                ("export_excel", "export_excel"), ("save_trained_model", "save_trained_model"),
            ):
                if not completed.get(target) and context.get(source) not in (None, ""):
                    completed[target] = context[source]
            if not completed.get("dataset") and context.get("processed_dataset_path"):
                completed["dataset"] = context["processed_dataset_path"]
            if context.get("dataset_id"):
                completed.setdefault("dataset_id", context["dataset_id"])
        return {key: value for key, value in completed.items() if value not in (None, "")}

    @staticmethod
    def processed_dataset_candidates() -> list[dict[str, Any]]:
        """List visible local five-channel datasets."""
        from core.resource.registry.data_registry import DataRegistry
        from core.user_context import get_current_user_id

        registry = DataRegistry()
        user_id = get_current_user_id()
        candidates = []
        for item in registry._read():
            if not registry.is_visible(item, user_id) or not registry.is_present_locally(item):
                continue
            root = registry.resolve_data_path(str(item.get("data_path", "")))
            if not all((root / name).is_file() for name in ("window_features.pkl", "window_info.pkl")):
                continue
            name = HypoxemiaState._dataset_display_name(item, root)
            candidates.append({
                "id": item["id"],
                "name": name,
                "dataset_id": item["id"],
            })
        return candidates

    @staticmethod
    def _dataset_display_name(item: dict[str, Any], root: Path) -> str:
        """Add stable, human-readable metadata to otherwise duplicate dataset names."""
        base_name = str(item.get("name") or item.get("id") or root.name)
        stats = HypoxemiaState._read_json(root / "window_stats.json")
        manifest = HypoxemiaState._read_json(root / "dataset_manifest.json")

        total_windows = stats.get("total_windows")
        manifest_stats = manifest.get("stats")
        if total_windows is None and isinstance(manifest_stats, dict):
            total_windows = manifest_stats.get("total_windows")
        try:
            total_text = (
                f"总数据量 {int(total_windows):,} 个窗口"
                if total_windows is not None
                else "总数据量未知"
            )
        except (TypeError, ValueError):
            total_text = "总量未知"

        timestamp = manifest.get("created_at") or item.get("created_at")
        time_text = HypoxemiaState._format_dataset_timestamp(timestamp)
        if time_text == "时间未知":
            try:
                timestamp = datetime.fromtimestamp(
                    (root / "window_stats.json").stat().st_mtime
                ).isoformat()
            except OSError:
                timestamp = None
            time_text = HypoxemiaState._format_dataset_timestamp(timestamp)
        return f"{base_name}（{total_text}，处理时间 {time_text}）"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _format_dataset_timestamp(value: Any) -> str:
        if value in (None, ""):
            return "时间未知"
        try:
            if isinstance(value, (int, float)):
                parsed = datetime.fromtimestamp(value).astimezone()
            else:
                text = str(value).strip()
                if text.endswith("Z"):
                    text = f"{text[:-1]}+00:00"
                parsed = datetime.fromisoformat(text)
                parsed = parsed.astimezone()
            return parsed.strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OverflowError, OSError):
            return "时间未知"

    @staticmethod
    def resolve_choice(answer: str, candidates: list[dict]) -> dict | None:
        normalized = answer.strip().lower()
        index_match = re.search(r"(?:^\s*|第|选择第?)(\d+)(?:\s*$|\s*个|\s*号|\s*项)", normalized)
        if index_match:
            index = int(index_match.group(1))
            return next((item for item in candidates if item.get("index") == index), None)
        cleaned = re.sub(
            r"^(?:我?选择|选择模型|模型选择)\s*(?:第\s*\d+\s*个模型?\s*)?[:：]?\s*",
            "",
            normalized,
        ).strip(" \t\r\n:：，,。.")
        for item in candidates:
            values = {str(item.get("id", "")).lower(), str(item.get("name", "")).lower()}
            if normalized in values or cleaned in values:
                return item
        mentions = []
        for item in candidates:
            item_id = str(item.get("id", "")).lower()
            item_name = str(item.get("name", "")).lower()
            if (item_id and item_id in normalized) or (item_name and item_name in normalized):
                mentions.append(item)
        return mentions[0] if len(mentions) == 1 else None

    @classmethod
    def resolve_model_from_candidates(cls, answer: str, candidates: list[dict]) -> dict | None:
        selected = cls.resolve_choice(answer, candidates)
        if selected:
            return selected
        normalized = answer.strip().lower()
        exact = [
            item for item in candidates
            if normalized in {str(item.get("id", "")).lower(), str(item.get("name", "")).lower()}
        ]
        return exact[0] if len(exact) == 1 else None

    @staticmethod
    def resolve_choice_alias(answer: str, pending: dict) -> dict | None:
        normalized = answer.strip().lower()
        stage = pending.get("stage", "")
        candidates = pending.get("candidates", [])
        if stage == "workflow-kind":
            wanted = (
                "evaluate" if any(word in normalized for word in ("评测", "测评", "已有模型"))
                else "train" if "训练" in normalized else ""
            )
        elif stage == "training-action":
            if any(word in normalized for word in ("直接提交", "开始训练", "默认训练", "使用默认训练")):
                wanted = "default"
            elif "修改训练" in normalized or "训练超参数" in normalized:
                wanted = "modify"
            else:
                wanted = ""
        elif stage == "architecture-action":
            if "直接提交" in normalized or any(word in normalized for word in ("使用当前架构", "默认架构")):
                wanted = "default"
            elif "修改架构" in normalized or "架构参数" in normalized:
                wanted = "modify"
            elif "注册当前" in normalized or "注册模型配置" in normalized:
                wanted = "register"
            else:
                wanted = ""
        else:
            wanted = ""
        return next((item for item in candidates if item.get("id") == wanted), None) if wanted else None

