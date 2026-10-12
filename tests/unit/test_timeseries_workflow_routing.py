import pytest

from resources.workflows.hypoxemia.adapter import HypoxemiaWorkflowAdapter as InteractiveAgent
from resources.agents.implementations.interactive_agent.agent import interactive_agent
from resources.workflows.hypoxemia.adapter import HypoxemiaWorkflowAdapter


@pytest.mark.asyncio
async def test_five_channel_workflow_actions_route_to_timeseries_tool():
    agent = InteractiveAgent()

    cases = {
        "检查五通道低氧数据集": "validate",
        "查看五通道可用模型": "list-models",
        "查看低氧血症可用模型": "list-models",
        "查看五通道数据低氧血症可用模型": "list-models",
        "注册五通道低氧数据集": "register-dataset",
        "创建 V3 五通道模型资源": "model-creation-guide",
        "我想创建一个新的低氧血症模型": "model-creation-guide",
        "开始低氧血症预测": "dataset-source",
        "以 DLinear 为基础创建五通道模型资源": "create-model",
        "基于当前五通道模型创建一个新模型": "derive-model",
        "启动 V3 五通道训练": "train",
        "评测五通道低氧模型": "evaluate",
    }

    for query, expected_mode in cases.items():
        tools, create_description = [{"id": agent.profile.tool_id}], None

        assert create_description is None
        assert [tool["id"] for tool in tools] == ["timeseries-classification-runner"], query
        assert agent._resolve_timeseries_workflow_intent(query) == expected_mode


def test_model_catalog_response_contains_only_available_models():
    response = InteractiveAgent._format_timeseries_model_catalog({
        "data": {
            "models": [
                {"model_name": "SegRNN", "available": True},
                {"model_name": "TimesNet", "available": True},
            ],
            "registered_resources": [{"dataset_id": "must-not-be-displayed"}],
        },
    })

    assert response == (
        "## 五通道低氧血症可用模型（2 个）\n\nSegRNN、TimesNet"
    )
    assert "must-not-be-displayed" not in response


def test_timeseries_context_fills_follow_up_parameters():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {
        "session-1": {
            "source_path": r"D:\data\five-channel",
            "dataset_id": "spo2-five-channel-1",
            "model_id": "model-1",
            "task_id": "task-1",
        }
    }
    tool = {"id": "timeseries-classification-runner"}

    assert agent._apply_timeseries_workflow_context("session-1", tool, "register-dataset", {}) == {
        "source_path": r"D:\data\five-channel"
    }
    assert agent._apply_timeseries_workflow_context("session-1", tool, "create-model", {}) == {
        "dataset_id": "spo2-five-channel-1"
    }
    assert agent._apply_timeseries_workflow_context("session-1", tool, "model-config", {}) == {
        "model_id": "model-1"
    }
    assert agent._apply_timeseries_workflow_context("session-1", tool, "train", {}) == {
        "dataset": r"D:\data\five-channel",
        "model_id": "model-1",
    }
    assert agent._apply_timeseries_workflow_context("session-1", tool, "evaluate", {}) == {
        "task_id": "task-1", "model_id": "model-1", "dataset_id": "spo2-five-channel-1"
    }


def test_training_prefers_scanned_dataset_path_over_manifest_only_id():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {
        "session-1": {
            "processed_dataset_path": r"D:\runtime\synthetic-windows",
            "dataset_id": "synthetic-pr-acceptance",
            "model_id": "SegRNN",
        }
    }

    result = agent._apply_timeseries_workflow_context(
        "session-1", {"id": "timeseries-classification-runner"}, "train", {}
    )

    assert result["dataset"] == r"D:\runtime\synthetic-windows"
    assert result["model_id"] == "SegRNN"


def test_dataset_source_selection_keeps_uploaded_path_for_next_step():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {
        "session-1": {
            "dataset_source_type": "raw",
            "source_path": r"D:\data\raw-monitor",
        },
    }

    assert agent._resolve_timeseries_workflow_intent(
        r"D:\data\raw-monitor", has_workflow_context=True, session_key="session-1"
    ) == "process-raw-dataset"
    assert agent._apply_timeseries_workflow_context(
        "session-1",
        {"id": "timeseries-classification-runner"},
        "process-raw-dataset",
        {},
    ) == {"source_path": r"D:\data\raw-monitor"}


def test_dataset_source_and_path_are_isolated_by_session():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {
        "session-1": {
            "dataset_source_type": "raw",
            "source_path": r"D:\data\raw-monitor",
        },
        "session-2": {
            "dataset_source_type": "processed",
            "source_path": r"D:\data\processed-windows",
        },
    }
    tool = {"id": "timeseries-classification-runner"}

    assert agent._resolve_timeseries_workflow_intent(
        r"D:\data\next", has_workflow_context=True, session_key="session-1"
    ) == "process-raw-dataset"
    assert agent._resolve_timeseries_workflow_intent(
        r"D:\data\next", has_workflow_context=True, session_key="session-2"
    ) == "validate"
    assert agent._resolve_timeseries_workflow_intent(
        r"D:\data\next", session_key="session-3"
    ) is None
    assert agent._apply_timeseries_workflow_context(
        "session-1", tool, "validate", {}
    ) == {"source_path": r"D:\data\raw-monitor"}
    assert agent._apply_timeseries_workflow_context(
        "session-2", tool, "validate", {}
    ) == {"source_path": r"D:\data\processed-windows"}
    assert agent._apply_timeseries_workflow_context(
        "session-3", tool, "validate", {}
    ) == {}


def test_raw_processing_context_preserves_window_configuration():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {
        "session-1": {
            "source_path": r"D:\data\raw",
            "raw_processing_config": {
                "lookback_seconds": 180,
                "prediction_interval_seconds": 60,
                "max_non_hypo_ratio": 1.0,
            },
        }
    }

    result = agent._apply_timeseries_workflow_context(
        "session-1", {"id": "timeseries-classification-runner"}, "process-raw-dataset", {}
    )

    assert result["source_path"] == r"D:\data\raw"
    assert result["lookback_seconds"] == 180
    assert result["prediction_interval_seconds"] == 60
    assert result["max_non_hypo_ratio"] == 1.0


def test_timeseries_path_extraction_ignores_quoted_path_explanation():
    path = r"D:\fixtures\synthetic-windows"

    assert InteractiveAgent._extract_timeseries_path(
        f'"{path}"这是数据文件路径'
    ) == path
    assert InteractiveAgent._extract_timeseries_path(path) == path


def test_raw_processing_extracts_staging_root_from_json_and_labels():
    assert InteractiveAgent._extract_timeseries_staging_root(
        r'{"source_path":"D:\\demo\\data","staging_root":"E:\\sotaband_raw_staging"}'
    ) == r"E:\sotaband_raw_staging"
    assert InteractiveAgent._extract_timeseries_staging_root(
        r"原始数据目录：D:\demo\data，ZIP 临时解压目录：E:\sotaband_raw_staging"
    ) == r"E:\sotaband_raw_staging"


@pytest.mark.asyncio
async def test_raw_processing_mode_includes_staging_root():
    agent = InteractiveAgent()
    params = await agent._extract_params_for_mode(
        r"原始数据目录：D:\demo\data，staging_root=E:\sotaband_raw_staging",
        "timeseries-classification-runner",
        "process-raw-dataset",
    )
    assert params["source_path"] == r"D:\demo\data"
    assert params["staging_root"] == r"E:\sotaband_raw_staging"


@pytest.mark.asyncio
async def test_dataset_source_mode_preserves_staging_root_for_raw_processing():
    agent = InteractiveAgent()
    params = await agent._extract_params_for_mode(
        r"原始数据目录：D:\demo\data，ZIP 临时解压目录：E:\sotaband_raw_staging",
        "timeseries-classification-runner",
        "dataset-source",
    )
    assert params["source_path"] == r"D:\demo\data"
    assert params["staging_root"] == r"E:\sotaband_raw_staging"


def test_model_choice_contains_create_and_other():
    candidates = InteractiveAgent._timeseries_model_selection_candidates()

    assert candidates[-2]["id"] == "__create__"
    assert candidates[-1]["id"] == "__other__"
    assert any(item["id"] == "SegRNNDualScaleV2" for item in candidates)
    assert all(item.get("runtime_status") != "trained" for item in candidates)
    assert not {"KANAD", "MultiPatchFormer", "TiDE", "WPMixer"} & {item["id"] for item in candidates}


def test_model_choice_excludes_registered_designs_without_trainable_implementation(monkeypatch):
    from core.resource.registry.model_registry import ModelRegistry

    monkeypatch.setattr(ModelRegistry, "_read", lambda self: [
        {"id": "design", "name": "Design only", "model_name": "UnimplementedCustom",
         "associated_tool_id": "timeseries-classification-runner", "runtime_status": "configured"},
        {"id": "unsupported", "name": "Old KANAD", "model_name": "KANAD",
         "associated_tool_id": "timeseries-classification-runner", "runtime_status": "configured"},
        {"id": "trainable", "name": "Saved SegRNN", "model_name": "SegRNN",
         "associated_tool_id": "timeseries-classification-runner", "runtime_status": "configured"},
    ])

    ids = {item["id"] for item in InteractiveAgent._timeseries_model_selection_candidates()}
    assert "trainable" in ids
    assert "design" not in ids
    assert "unsupported" not in ids


def test_trained_model_candidates_are_reserved_for_evaluation(tmp_path, monkeypatch):
    from core.resource.registry.model_registry import ModelRegistry
    from core.runtime import paths as runtime_paths

    runs_root = tmp_path / "runs"
    artifact = runs_root / "SegRNN-1" / "runs" / "run-1"
    artifact.mkdir(parents=True)
    for name in (
        "best_model.pt", "scaler.pkl", "tslib_model_config.json",
        "threshold_report_f1.json", "manifest.json",
    ):
        (artifact / name).touch()
    monkeypatch.setattr(runtime_paths, "timeseries_runs_dir", lambda: runs_root)

    monkeypatch.setattr(ModelRegistry, "_read", lambda self: [
        {"id": "configured", "associated_tool_id": "timeseries-classification-runner",
         "runtime_status": "configured", "model_path": ""},
        {"id": "trained", "name": "Synthetic model",
         "associated_tool_id": "timeseries-classification-runner",
         "runtime_status": "trained", "model_path": str(artifact / "best_model.pt")},
        {"id": "stale", "name": "Stale model",
         "associated_tool_id": "timeseries-classification-runner",
         "runtime_status": "trained", "model_path": str(tmp_path / "old" / "best_model.pt")},
    ])
    candidates = InteractiveAgent._timeseries_trained_model_candidates()

    assert [item["id"] for item in candidates] == ["trained"]
    assert all(item.get("runtime_status") == "trained" for item in candidates)
    assert all(item.get("model_path") for item in candidates)


def test_workflow_choice_aliases_accept_direct_train_and_evaluate_commands():
    assert InteractiveAgent._resolve_timeseries_choice_alias(
        "直接提交当前模型开始训练",
        {"stage": "training-action", "candidates": [{"id": "default"}]},
    )["id"] == "default"
    assert InteractiveAgent._resolve_timeseries_choice_alias(
        "选择已训练模型进行评测",
        {"stage": "workflow-kind", "candidates": [{"id": "evaluate"}]},
    )["id"] == "evaluate"


@pytest.mark.asyncio
async def test_other_model_choice_enters_custom_model_input_stage():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {"session-1": {}}
    agent._pending_timeseries_input = {}

    events = [
        event async for event in agent._handle_timeseries_choice(
            "session-1",
            {"stage": "model", "candidates": [{"id": "__other__"}]},
            {"id": "__other__"},
        )
    ]
    assert agent._pending_timeseries_input["session-1"]["stage"] == "custom-model-description"
    assert any("请描述新模型" in event.get("data", {}).get("text", "") for event in events)


def test_dataset_source_choice_includes_other_llm_branch():
    candidates = InteractiveAgent._dataset_source_choice_card()["data"]["data"]["candidates"]

    assert [item["id"] for item in candidates] == ["raw", "processed", "__other__"]


def test_workflow_summary_explains_default_configuration_without_empty_objects():
    summary = InteractiveAgent._workflow_ready_summary({"model_id": "DLinear"})

    assert "未修改，使用所选模型当前架构参数" in summary
    assert "未修改，使用默认训练超参数" in summary
    assert "{}" not in summary


def test_timeseries_choice_accepts_frontend_sentence():
    candidates = [
        {"id": "SegRNNDualScaleV2", "name": "SegRNNDualScaleV2", "index": 24},
        {"id": "__other__", "name": "其它（交给大模型）", "index": 2},
    ]

    selected = InteractiveAgent._resolve_timeseries_choice(
        "我选择第24个模型：SegRNNDualScaleV2", candidates
    )

    assert selected and selected["id"] == "SegRNNDualScaleV2"


def test_timeseries_choice_accepts_model_name_with_selection_prefix():
    candidates = [
        {"id": "SegRNNDualScaleV2", "name": "SegRNNDualScaleV2", "index": 24},
        {"id": "__other__", "name": "其它（交给大模型）", "index": 2},
    ]

    selected = InteractiveAgent._resolve_timeseries_choice(
        "我选择：SegRNNDualScaleV2", candidates
    )

    assert selected and selected["id"] == "SegRNNDualScaleV2"


def test_model_configuration_follow_up_routes_with_active_workflow():
    agent = InteractiveAgent()

    assert agent._resolve_timeseries_workflow_intent(
        "查看或修改模型架构参数", has_workflow_context=True
    ) == "model-config"
    assert agent._resolve_timeseries_workflow_intent("查看或修改模型架构参数") is None
    assert agent._resolve_timeseries_workflow_intent(
        "这些都不符合我的要求，我想创建一个新的结构", has_workflow_context=True
    ) == "model-creation-guide"


def test_explicit_workflow_action_precedes_generic_predict_mode_match():
    agent = InteractiveAgent()
    assert agent._resolve_timeseries_workflow_intent("开始低氧血症预测") == "dataset-source"


def test_evaluation_synonyms_route_to_timeseries_evaluation():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {"session-1": {"artifact_path": r"D:\\model"}}

    assert agent._resolve_timeseries_workflow_intent(
        "开始测评", has_workflow_context=True, session_key="session-1"
    ) == "evaluate"


@pytest.mark.asyncio
async def test_empty_timeseries_workflow_context_routes_new_structure_to_guide():
    agent = InteractiveAgent()
    agent.state_for("session-1")

    from core.workflow.base import WorkflowContext
    assert agent.detect("这些都不符合我的要求，我想创建一个新的结构", WorkflowContext("session-1"))
    assert agent._resolve_timeseries_workflow_intent(
        "这些都不符合我的要求，我想创建一个新的结构", has_workflow_context=True
    ) == "model-creation-guide"


def test_natural_language_model_parameters_are_extracted():
    agent = InteractiveAgent()

    params = agent._extract_timeseries_model_params(
        "以 DLinear 为基础，隐藏维度 128，dropout 0.2，训练 80 轮，批次大小 32，学习率 0.0001"
    )

    assert params == {
        "model_name": "DLinear",
        "model_config": {"d_model": 128, "dropout": 0.2},
        "training_config": {"epochs": 80, "batch_size": 32, "learning_rate": 0.0001},
    }


def test_architecture_natural_language_modifiers_are_extracted():
    agent = InteractiveAgent()

    params = agent._extract_timeseries_model_params(
        "把 dropout 改为 0.2，编码器层数改为 4 层，分类头隐藏维度改为 [256,128]。"
    )

    assert params["model_config"] == {
        "e_layers": 4,
        "dropout": 0.2,
        "p_hidden_dims": [256, 128],
    }


def test_architecture_and_training_action_aliases_are_resolved():
    assert InteractiveAgent._resolve_timeseries_choice_alias(
        "我选择：修改架构参数",
        {"stage": "architecture-action", "candidates": [{"id": "modify"}]},
    )["id"] == "modify"
    assert InteractiveAgent._resolve_timeseries_choice_alias(
        "我选择：修改训练超参数",
        {"stage": "training-action", "candidates": [{"id": "modify"}]},
    )["id"] == "modify"


def test_derived_model_configuration_identifies_its_source():
    response = InteractiveAgent._format_timeseries_model_configuration({
        "data": {
            "model_id": "model-derived",
            "model_name": "DLinear",
            "creation_type": "derived-from-resource",
            "derived_from_model_id": "model-source",
            "training_config": {"architecture": {}, "training": {}},
        }
    })

    assert "派生来源：`model-source`" in response


def test_generic_ml_candidates_include_the_low_oxygen_tool():
    agent = InteractiveAgent()
    candidates = [
        {"id": "meal-replacement-shape-prediction-training"},
        {"id": "electricity-data-forecast-model-training"},
    ]
    active = [
        *candidates,
        {"id": "timeseries-classification-runner", "name": "通用时序分类训练评测"},
    ]

    result = interactive_agent.workflow_registry.augment_candidates("训练模型", candidates, active)

    assert [item["id"] for item in result] == [
        "meal-replacement-shape-prediction-training",
        "electricity-data-forecast-model-training",
        "timeseries-classification-runner",
    ]


@pytest.mark.asyncio
async def test_custom_model_description_is_passed_to_design_mode():
    agent = InteractiveAgent()
    description = "创建自定义模型 transformer_gru_v1。\n编码器：3 层 Transformer。"

    params = await agent._extract_params_for_mode(
        description, "timeseries-classification-runner", "custom-model-design"
    )

    assert params == {"description": description}


CUSTOM_DUAL_SCALE_DESCRIPTION = """请创建并注册一个名为 HypoxemiaDualScaleCustom 的可训练模型。
模型来源：基于现有模型 SegRNNDualScaleV2。
输入数据：256 秒窗口、5 个通道（SpO2、HR、PR、RR、PI）。
编码器：保留 SegRNNDualScaleV2 的双尺度时序编码器。
时序聚合：保留原有双尺度时序聚合方式。
分类头：使用两层全连接分类头，隐藏维度为 [128, 128]。
模型 dropout：0.15。
训练设置：
- 优化器：AdamW
- 学习率：0.00025
- 权重衰减：0.00005
- 批次大小：32
- 训练轮数：140
- 随机种子：42
- 早停 patience：35
- 早停开始轮数：25
- 阈值选择指标：F1
- 训练监控指标：AUC"""


def test_custom_model_natural_language_extracts_architecture_and_training():
    agent = InteractiveAgent()
    parsed = agent._extract_timeseries_model_params(CUSTOM_DUAL_SCALE_DESCRIPTION)

    assert parsed["model_name"] == "SegRNNDualScaleV2"
    assert parsed["model_config"] == {
        "dropout": 0.15, "p_hidden_dims": [128, 128], "p_hidden_layers": 2,
    }
    assert parsed["training_config"] == {
        "optimizer": "adamw", "learning_rate": 0.00025, "weight_decay": 0.00005,
        "batch_size": 32, "epochs": 140, "seed": 42,
        "early_stop_patience": 35, "early_stop_start_epoch": 25,
        "threshold_metric": "f1", "selection_monitor": "auc",
    }


@pytest.mark.asyncio
async def test_custom_model_registration_keeps_executable_model_name():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {"session-1": {"processed_dataset_path": r"D:\data\windows"}}
    agent._pending_timeseries_choice = {}
    agent._pending_timeseries_input = {}
    captured = {}

    async def llm_description(_description):
        return "模型名称：custom_model\n编码器：\n时序聚合：分类头：\n分类头：训练：", "SegRNNDualScaleV2"

    async def execute_tool(_tool, _query, _paths, params):
        captured.update(params)
        return {
            "status": "success",
            "data": {
                "model_id": "model-custom", "name": "HypoxemiaDualScaleCustom",
                "model_name": "SegRNNDualScaleV2", "trainable": True,
                "design": {"name": "HypoxemiaDualScaleCustom"}, "missing_sections": [],
            },
        }

    agent._llm_complete_custom_model_description = llm_description
    agent._execute_tool = execute_tool
    events = [event async for event in agent._handle_timeseries_input(
        "session-1", {"stage": "custom-model-description"}, CUSTOM_DUAL_SCALE_DESCRIPTION
    )]

    assert captured["model_resource_name"] == "HypoxemiaDualScaleCustom"
    assert captured["description"] == CUSTOM_DUAL_SCALE_DESCRIPTION
    assert captured["model_config"]["p_hidden_layers"] == 2
    assert captured["training_config"]["optimizer"] == "adamw"
    assert agent._timeseries_workflows["session-1"]["model_name"] == "SegRNNDualScaleV2"
    assert agent._pending_timeseries_choice["session-1"]["stage"] == "architecture-action"
    assert events


@pytest.mark.asyncio
async def test_architecture_edit_prompt_accepts_current_configuration():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {"session-1": {"model_id": "model-custom"}}
    agent._pending_timeseries_choice = {}
    events = [event async for event in agent._handle_timeseries_input(
        "session-1", {"stage": "architecture-config"}, "我选择：使用当前架构参数"
    )]

    assert agent._pending_timeseries_choice["session-1"]["stage"] == "architecture-save"
    assert any(event.get("data", {}).get("type") == "model-confirm" for event in events)


@pytest.mark.asyncio
async def test_architecture_save_choice_separates_config_registration_from_training():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {"session-1": {"model_id": "SegRNN", "model_trainable": True}}
    agent._pending_timeseries_choice = {}
    agent._pending_timeseries_input = {}
    calls = []

    async def create_resource(workflow):
        calls.append(workflow["model_id"])
        return {"status": "success", "data": {"model_id": "saved-architecture"}}

    agent._create_timeseries_model_resource = create_resource
    await_events = [event async for event in agent._handle_timeseries_choice(
        "session-1", {"stage": "architecture-action"}, {"id": "default"}
    )]
    assert agent._pending_timeseries_choice["session-1"]["stage"] == "architecture-save"
    assert await_events
    assert calls == []

    _ = [event async for event in agent._handle_timeseries_choice(
        "session-1", {"stage": "architecture-save"}, {"id": "save"}
    )]
    assert calls == ["SegRNN"]
    assert agent._timeseries_workflows["session-1"]["model_id"] == "saved-architecture"
    assert agent._pending_timeseries_choice["session-1"]["stage"] == "training-action"

    agent._timeseries_workflows["session-2"] = {"model_id": "DLinear", "model_trainable": True}
    _ = [event async for event in agent._handle_timeseries_choice(
        "session-2", {"stage": "architecture-save"}, {"id": "skip"}
    )]
    assert calls == ["SegRNN"]
    assert agent._pending_timeseries_choice["session-2"]["stage"] == "training-action"


@pytest.mark.asyncio
async def test_training_artifact_is_registered_only_after_save_choice():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {"session-1": {"model_name": "SegRNN", "dataset_id": "data-1"}}
    agent._pending_timeseries_choice = {}
    calls = []

    async def execute_tool(tool, query, attachments, params):
        calls.append(params)
        return {"status": "success", "data": {"model_id": "trained-1"}}

    agent._execute_tool = execute_tool
    events = agent._queue_training_artifact_choice(
        "session-1", {"metadata": {"artifact_path": "D:/runs/model-1"}}
    )
    assert events[0]["data"]["data"]["candidates"][0]["id"] == "save"
    assert agent._timeseries_workflows["session-1"]["artifact_path"] == "D:/runs/model-1"
    assert calls == []

    _ = [event async for event in agent._handle_timeseries_choice(
        "session-1", {"stage": "training-artifact"}, {"id": "skip"}
    )]
    assert calls == []
    assert agent._pending_timeseries_choice["session-1"]["stage"] == "evaluation-metrics"

    _ = [event async for event in agent._handle_timeseries_choice(
        "session-1", {"stage": "training-artifact"}, {"id": "save"}
    )]
    assert calls[0]["mode"] == "inspect-model"
    assert calls[0]["artifact_path"] == "D:/runs/model-1"
    assert agent._timeseries_workflows["session-1"]["trained_model_id"] == "trained-1"
    assert agent._timeseries_workflows["session-1"]["model_id"] == "trained-1"


@pytest.mark.asyncio
async def test_training_artifact_skip_evaluates_current_artifact_without_config_resource():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {
        "session-1": {"model_id": "configured-1", "artifact_path": "D:/runs/model-1"}
    }
    agent._pending_timeseries_choice = {}
    agent._pending_timeseries_input = {}

    _ = [event async for event in agent._handle_timeseries_choice(
        "session-1", {"stage": "training-artifact"}, {"id": "skip"}
    )]

    assert agent._timeseries_workflows["session-1"]["model_id"] == ""
    assert agent._timeseries_workflows["session-1"]["artifact_path"] == "D:/runs/model-1"


@pytest.mark.asyncio
async def test_training_artifact_save_requires_registered_model_id():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {
        "session-1": {"model_id": "configured-1", "artifact_path": "D:/runs/model-1"}
    }
    agent._pending_timeseries_choice = {}

    async def execute_tool(*_args, **_kwargs):
        return {"status": "success", "data": {}}

    agent._execute_tool = execute_tool
    events = [event async for event in agent._handle_timeseries_choice(
        "session-1", {"stage": "training-artifact"}, {"id": "save"}
    )]

    assert agent._timeseries_workflows["session-1"]["model_id"] == "configured-1"
    assert agent._pending_timeseries_choice["session-1"]["stage"] == "training-artifact"
    assert any("模型 ID" in event.get("data", {}).get("text", "") for event in events)


@pytest.mark.asyncio
async def test_failed_evaluation_does_not_claim_completion():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {
        "session-1": {
            "dataset_id": "data-1", "processed_dataset_path": "D:/data/windows",
            "model_id": "trained-1", "artifact_path": "D:/runs/model-1",
            "evaluation_metrics": ["f1"], "evaluation_ready": True,
        }
    }

    async def no_missing(*_args, **_kwargs):
        return []

    async def no_params(*_args, **_kwargs):
        return {}

    async def fail(*_args, **_kwargs):
        return {"status": "failed", "output_format": "text", "message": "missing artifact", "data": {}}

    agent._check_missing_params = no_missing
    agent._extract_params_for_mode = no_params
    agent._execute_tool = fail
    ctx = type("Context", (), {"session_id": "session-1"})()

    events = [event async for event in agent._execute_impl(ctx, "开始评测", [], [])]

    assert any(event.get("data", {}).get("summary") == "missing artifact" for event in events)
    assert not any("评测已完成" in event.get("data", {}).get("text", "") for event in events)
    assert agent._timeseries_workflows["session-1"]["model_id"] == "trained-1"


@pytest.mark.asyncio
async def test_registered_processed_dataset_choice_validates_and_continues():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {"session-1": {"dataset_source_type": "processed"}}
    agent._pending_timeseries_choice = {}
    calls = []

    async def execute_tool(tool, query, attachments, params):
        calls.append(params)
        return {"status": "success", "card_type": "dataset-summary", "data": {
            "dataset_id": "data-1", "source_path": "D:/data/windows",
        }}

    agent._execute_tool = execute_tool
    events = [event async for event in agent._handle_timeseries_choice(
        "session-1", {"stage": "processed-data-source"}, {"id": "data-1"}
    )]
    assert calls == [{"mode": "validate", "source_path": "data-1"}]
    assert agent._timeseries_workflows["session-1"]["processed_dataset_path"] == "D:/data/windows"
    assert agent._pending_timeseries_choice["session-1"]["stage"] == "workflow-kind"
    assert [event["event"] for event in events] == ["card", "card", "content"]


@pytest.mark.asyncio
async def test_processed_dataset_file_choice_preserves_path_prompt():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {"session-1": {}}
    agent._pending_timeseries_choice = {}
    agent._pending_calls = {}
    events = [event async for event in agent._handle_timeseries_choice(
        "session-1", {"stage": "processed-data-source"}, {"id": "__upload__"}
    )]
    assert agent._pending_calls["session-1"]["mode"] == "validate"
    assert agent._pending_calls["session-1"]["missing"][0]["name"] == "source_path"
    assert "window_features.pkl" in events[0]["data"]["text"]


def test_processed_dataset_candidates_only_include_visible_window_data(tmp_path, monkeypatch):
    from core.resource.registry import data_registry
    from core import user_context

    ready = tmp_path / "ready"
    incomplete = tmp_path / "incomplete"
    hidden = tmp_path / "hidden"
    for root in (ready, incomplete, hidden):
        root.mkdir()
        (root / "window_features.pkl").touch()
    for root in (ready, hidden):
        (root / "window_info.pkl").touch()
    (ready / "window_stats.json").write_text(
        '{"total_windows": 163}', encoding="utf-8"
    )
    (ready / "dataset_manifest.json").write_text(
        '{"created_at": "2026-10-10T14:11:36Z"}', encoding="utf-8"
    )
    entries = [
        {"id": "ready", "name": "Ready", "data_path": str(ready), "owner": "user-1"},
        {"id": "incomplete", "data_path": str(incomplete), "owner": "user-1"},
        {"id": "hidden", "data_path": str(hidden), "owner": "user-2"},
    ]
    monkeypatch.setattr(data_registry.DataRegistry, "claim_local_orphans", lambda self: 0)
    monkeypatch.setattr(data_registry.DataRegistry, "_read", lambda self: entries)
    monkeypatch.setattr(user_context, "get_current_user_id", lambda: "user-1")

    assert HypoxemiaWorkflowAdapter.processed_dataset_candidates() == [
        {"id": "ready", "name": "Ready（总数据量 163 个窗口，处理时间 2026-10-10 22:11）", "dataset_id": "ready"}
    ]


def test_processed_dataset_candidates_disambiguate_same_name_and_fallback_metadata(tmp_path, monkeypatch):
    from core.resource.registry import data_registry
    from core import user_context

    first = tmp_path / "first"
    second = tmp_path / "second"
    legacy = tmp_path / "legacy"
    for root in (first, second, legacy):
        root.mkdir()
        (root / "window_features.pkl").touch()
        (root / "window_info.pkl").touch()
    (first / "window_stats.json").write_text('{"total_windows": 48}', encoding="utf-8")
    (first / "dataset_manifest.json").write_text(
        '{"created_at": "2026-10-10T13:00:00Z"}', encoding="utf-8"
    )
    (second / "window_stats.json").write_text('{"total_windows": 60}', encoding="utf-8")
    (second / "dataset_manifest.json").write_text(
        '{"created_at": "2026-10-10T14:00:00Z"}', encoding="utf-8"
    )
    entries = [
        {"id": "first", "name": "balanced_windows_256s_30s_all", "data_path": str(first), "owner": "user-1"},
        {"id": "second", "name": "balanced_windows_256s_30s_all", "data_path": str(second), "owner": "user-1"},
        {"id": "legacy", "name": "balanced_windows_256s_30s_all", "data_path": str(legacy),
         "created_at": "2026-10-10T15:22:00Z", "owner": "user-1"},
    ]
    monkeypatch.setattr(data_registry.DataRegistry, "claim_local_orphans", lambda self: 0)
    monkeypatch.setattr(data_registry.DataRegistry, "_read", lambda self: entries)
    monkeypatch.setattr(user_context, "get_current_user_id", lambda: "user-1")

    candidates = HypoxemiaWorkflowAdapter.processed_dataset_candidates()
    assert [item["id"] for item in candidates] == ["first", "second", "legacy"]
    assert candidates[0]["name"] == "balanced_windows_256s_30s_all（总数据量 48 个窗口，处理时间 2026-10-10 21:00）"
    assert candidates[1]["name"] == "balanced_windows_256s_30s_all（总数据量 60 个窗口，处理时间 2026-10-10 22:00）"
    assert candidates[2]["name"] == "balanced_windows_256s_30s_all（总数据量未知，处理时间 2026-10-10 23:22）"


@pytest.mark.asyncio
async def test_training_parameter_prompt_accepts_default_and_relative_learning_rate():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {"session-1": {"model_id": "DLinear"}}

    events = [
        event async for event in agent._handle_timeseries_input(
            "session-1", {"stage": "training-config"}, "我选择第1个模型：使用默认训练超参数"
        )
    ]
    assert agent._timeseries_workflows["session-1"]["workflow_ready"] is True
    assert any("默认训练超参数" in event.get("data", {}).get("text", "") for event in events)

    agent._timeseries_workflows["session-1"] = {"model_id": "DLinear"}
    events = [
        event async for event in agent._handle_timeseries_input(
            "session-1", {"stage": "training-config"}, "学习率变成原来一半"
        )
    ]
    assert agent._timeseries_workflows["session-1"]["training_config"] == {
        "learning_rate": 0.000125
    }


def test_continue_after_raw_task_uses_validation_mode_without_resubmitting():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {
        "session-1": {
            "dataset_source_type": "raw",
            "source_path": r"D:\data\raw",
            "processing_task_id": "task-1",
        }
    }

    assert agent._resolve_timeseries_workflow_intent(
        "继续", has_workflow_context=True, session_key="session-1"
    ) == "validate"


def test_raw_source_with_zip_is_detected_for_staging_prompt(tmp_path):
    (tmp_path / "input.zip").write_bytes(b"PK")
    agent = InteractiveAgent()

    assert agent._raw_source_contains_zip(str(tmp_path)) is True
    assert agent._raw_source_contains_zip(str(tmp_path / "input.zip")) is True


def test_raw_processing_compact_config_form_keeps_window_and_interval():
    parsed = InteractiveAgent._extract_raw_processing_params(
        'configs=["180:60"]、spo2_threshold=92'
    )
    assert parsed["configs"] == ["180:60"]
    assert parsed["spo2_threshold"] == 92.0


def test_raw_processing_compact_config_form_accepts_chinese_window_labels():
    parsed = InteractiveAgent._extract_raw_processing_params("窗口长度改为180秒，预测间隔设置为60秒，低氧阈值92")
    assert parsed["configs"] == ["180:60"]
    assert parsed["spo2_threshold"] == 92.0


@pytest.mark.asyncio
async def test_empty_raw_processing_parameters_restore_defaults():
    agent = InteractiveAgent()
    agent._timeseries_workflows = {"session-1": {"raw_processing_config": {"spo2_threshold": 92}}}
    agent._pending_calls = {
        "session-1": {
            "params": {"source_path": r"D:\data\raw", "spo2_threshold": 92},
            "await_raw_processing_start": False,
        }
    }
    agent._pending_timeseries_input = {}
    events = [event async for event in agent._handle_timeseries_input(
        "session-1", {"stage": "raw-processing-config"}, "原始处理参数：{}"
    )]
    assert agent._timeseries_workflows["session-1"]["raw_processing_config"] == {}
    assert "spo2_threshold" not in agent._pending_calls["session-1"]["params"]
    assert agent._pending_calls["session-1"]["await_raw_processing_start"] is True
    assert any("恢复默认值" in event.get("data", {}).get("text", "") for event in events)


@pytest.mark.asyncio
async def test_bare_staging_path_does_not_replace_raw_source():
    agent = InteractiveAgent()
    agent._pending_calls = {
        "session-1": {
            "tool_id": "timeseries-classification-runner",
            "tool_name": "通用时序分类训练评测",
            "mode": "process-raw-dataset",
            "params": {"source_path": r"D:\data\raw"},
            "missing": [{"name": "staging_root", "type": "string"}],
        }
    }
    agent._pending_confirm = {}
    agent._pending_modes = {}
    agent._pending_timeseries_choice = {}
    agent._pending_timeseries_input = {}
    agent._sessions = {}
    agent._timeseries_workflows = {
        "session-1": {"dataset_source_type": "raw", "source_path": r"D:\data\raw"}
    }
    async def no_missing(*_args, **_kwargs):
        return []

    agent._check_missing_params = no_missing
    ctx = type("Context", (), {"session_id": "session-1"})()
    events = [event async for event in agent._execute_impl(ctx, r"D:\data\staging", [], [])]

    assert agent._pending_calls["session-1"]["params"]["source_path"] == r"D:\data\raw"
    assert agent._pending_calls["session-1"]["params"]["staging_root"] == r"D:\data\staging"
    assert agent._pending_timeseries_choice["session-1"]["stage"] == "raw-processing-config-action"
    assert events
