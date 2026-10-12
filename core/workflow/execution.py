"""Submit a validated tool task envelope through the platform task manager."""

def submit_task_result(result: dict, tool: dict) -> dict:
    data = result.get("data", {})
    request = data.get("task_request") if isinstance(data, dict) else None
    if not isinstance(request, dict) or result.get("status") != "success":
        return result
    from core.task.task_manager import TaskManager
    try:
        metadata = dict(request.get("metadata") or {})
        kind = request.get("kind", "tool")
        if kind == "hypoxemia-data-processing":
            metadata.setdefault("next_action", "继续")
            metadata.setdefault("next_action_label", "扫描处理结果")
        elif kind == "hypoxemia-training":
            # Training completion first needs the explicit save-or-discard
            # decision; that transition then offers evaluation.
            metadata.setdefault("next_action", "继续")
            metadata.setdefault("next_action_label", "保存训练模型")
        task = TaskManager().submit(
            name=request.get("name") or tool.get("name", tool["id"]),
            command=request.get("command") or [], kind=kind,
            metadata=metadata, cwd=request.get("cwd") or None,
        )
        return {
            **result, "output_format": "task", "card_type": "task-progress",
            "message": "任务已提交，可在对话卡片中查看真实进度",
            "data": {**{k: v for k, v in data.items() if k != "task_request"},
                     "task": task, "task_id": task["id"]},
        }
    except Exception as exc:  # noqa: BLE001 - task submission can fail in platform backends
        return {"status": "failed", "output_format": "text",
                "message": f"任务提交失败: {exc}", "data": {}}


def persist_workflow_result(result: dict, tool: dict, service=None) -> dict:
    """Store evaluation outputs without making the platform understand metrics."""
    if result.get("status") != "success" or result.get("card_type") != "evaluation-result":
        return result
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if data.get("result_id"):
        return result
    if service is None:
        from core.timeseries import JsonResultStore
        store = JsonResultStore()
        saved = store.save(data, model_id=data.get("model_resource_id", ""),
                           dataset_id=data.get("dataset_id", ""), task_id=data.get("task_id", ""),
                           tool_id=tool.get("id", ""), report_path=data.get("report_path", ""))
    else:
        saved = service.save_result(
            data, model_id=data.get("model_resource_id", ""),
            dataset_id=data.get("dataset_id", ""), task_id=data.get("task_id", ""),
            tool_id=tool.get("id", ""), report_path=data.get("report_path", ""),
        )
    return {**result, "data": {**data, "result_id": saved["result_id"],
                                "result_path": saved["path"]}}
