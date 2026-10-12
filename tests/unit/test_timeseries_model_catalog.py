import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

from core.user_context import get_current_user_id


TOOL_PATH = Path(__file__).resolve().parents[2] / "resources" / "tools" / "implementations" / "timeseries-classification-runner" / "tool.py"
SPEC = importlib.util.spec_from_file_location("timeseries_classification_runner_test", TOOL_PATH)
assert SPEC and SPEC.loader
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


@pytest.fixture
def builtin_component(tmp_path, monkeypatch):
    monkeypatch.setenv("SOTABAND_HYPOXEMIA_PYTHON", sys.executable)
    monkeypatch.setenv("SPO2_HYPOXEMIA_TRUSTED_DATA_ROOTS", str(tmp_path))
    return tool.COMPONENT_ROOT


def test_catalog_contains_only_five_channel_models():
    result = tool._list_models()
    models = {item["model_name"]: item for item in result["data"]["models"]}

    assert models["SegRNNDualScaleV2"]["available"] is True
    assert models["TimesNet"]["available"] is True
    assert len(models) == 44
    assert "SegRNNParamPlethTriFusion" not in models
    for name in ("KANAD", "MultiPatchFormer", "TiDE", "WPMixer"):
        assert models[name]["available"] is False
        assert "分类" in models[name]["reason"]


@pytest.mark.parametrize("model_name", ["KANAD", "MultiPatchFormer", "TiDE", "WPMixer"])
def test_unimplemented_classification_is_rejected_before_training(model_name):
    with pytest.raises(ValueError, match="尚未支持分类任务"):
        tool._resolve_training_configuration({"model_id": model_name})


def test_registered_dataset_id_resolves_to_visible_path(tmp_path, monkeypatch):
    root = tmp_path / "windows"
    root.mkdir()
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps([{
        "id": "dataset-1", "name": "windows", "data_path": str(root),
        "owner": get_current_user_id(), "visibility": "private",
    }]), encoding="utf-8")
    monkeypatch.setattr(tool, "data_registry_path", lambda: registry)

    path, entry = tool._resolve_dataset_source("dataset-1")

    assert path == root
    assert entry["id"] == "dataset-1"


def test_model_manifest_uses_trained_window_length(tmp_path):
    (tmp_path / "best_model.pt").write_bytes(b"weights")
    (tmp_path / "scaler.pkl").write_bytes(b"scaler")
    (tmp_path / "tslib_model_config.json").write_text(
        json.dumps({"model": "SegRNN", "enc_in": 5, "seq_len": 180}), encoding="utf-8"
    )
    (tmp_path / "threshold_report_f1.json").write_text(
        json.dumps({"validation": {"threshold": 0.42}}), encoding="utf-8"
    )
    (tmp_path / "manifest.json").write_text(json.dumps({
        "feature_order": ["SpO2", "HR", "PR", "RR", "PI"],
        "sequence_length": 180, "sampling_hz": 1,
    }), encoding="utf-8")

    assert tool._inspect_artifact({"artifact_path": str(tmp_path), "register_model": False})["status"] == "success"

    (tmp_path / "manifest.json").write_text(json.dumps({
        "feature_order": ["SpO2", "HR", "PR", "RR", "PI"],
        "sequence_length": 256, "sampling_hz": 1,
    }), encoding="utf-8")
    assert tool._inspect_artifact({"artifact_path": str(tmp_path), "register_model": False})["status"] == "failed"


def test_model_configuration_is_bounded_and_uses_selected_model():
    _, model_name, configuration = tool._resolve_training_configuration({
        "model_id": "TimesNet",
        "model_config": {"d_model": 64, "dropout": 0.2},
        "training_config": {"epochs": 2, "learning_rate": 0.0001},
    })

    assert model_name == "TimesNet"
    assert configuration["architecture"]["d_model"] == 64
    assert configuration["architecture"]["dropout"] == 0.2
    assert configuration["training"]["epochs"] == 2


def test_custom_model_registration_preserves_requested_resource_name(monkeypatch):
    captured = {}

    def fake_call(api_name, **params):
        captured.update(params)
        return {"model_id": "model-custom", "name": params["name"]}

    monkeypatch.setattr(tool, "_call_api", fake_call)
    result = tool._analyze_custom_model_design({
        "description": "模型名称：HypoxemiaDualScaleCustom\n编码器：SegRNNDualScaleV2",
        "base_model": "SegRNNDualScaleV2",
        "model_resource_name": "HypoxemiaDualScaleCustom",
        "register_model": True,
    })

    assert result["status"] == "success"
    assert captured["name"] == "HypoxemiaDualScaleCustom"
    assert captured["preserve_name"] is True


def test_custom_model_registration_prefers_explicit_design_over_llm_summary(monkeypatch):
    captured = {}

    def fake_call(_api_name, **params):
        captured.update(params)
        return {"model_id": "model-custom", "name": params["name"]}

    monkeypatch.setattr(tool, "_call_api", fake_call)
    result = tool._analyze_custom_model_design({
        "description": """请创建并注册一个名为 HypoxemiaDualScaleCustom 的可训练模型。
输入数据：256 秒窗口、5 个通道。
编码器：保留 SegRNNDualScaleV2 的双尺度时序编码器。
时序聚合：保留原有双尺度时序聚合方式。
分类头：使用两层全连接分类头，隐藏维度为 [128, 128]。
训练设置：
- 优化器：AdamW""",
        "structured_description": "模型名称：custom_model\n编码器：\n时序聚合：分类头：\n分类头：训练：",
        "base_model": "SegRNNDualScaleV2",
        "model_resource_name": "HypoxemiaDualScaleCustom",
        "model_config": {"dropout": 0.15, "p_hidden_dims": [128, 128], "p_hidden_layers": 2},
        "training_config": {"optimizer": "adamw", "epochs": 140},
        "register_model": True,
    })

    assert result["status"] == "success"
    assert result["data"]["design"]["name"] == "HypoxemiaDualScaleCustom"
    assert result["data"]["design"]["encoder"].startswith("保留 SegRNNDualScaleV2")
    assert result["data"]["design"]["temporal_module"].startswith("保留原有双尺度")
    assert result["data"]["design"]["classification_head"].startswith("使用两层")
    assert result["data"]["missing_sections"] == []
    assert captured["training_config"]["model_name"] == "SegRNNDualScaleV2"
    assert captured["training_config"]["architecture"]["p_hidden_layers"] == 2


def test_model_configuration_rejects_unknown_or_out_of_range_fields():
    with pytest.raises(ValueError, match="unsupported configuration fields"):
        tool._resolve_training_configuration({
            "model_id": "TimesNet",
            "model_config": {"arbitrary_python": "not allowed"},
        })

    with pytest.raises(ValueError, match="d_model must be"):
        tool._resolve_training_configuration({
            "model_id": "TimesNet",
            "model_config": {"d_model": 4096},
        })


def test_model_creation_requires_an_explicit_catalog_model():
    result = tool._create_model({"dataset_id": "spo2-five-channel-1"})

    assert result["status"] == "failed"
    assert "请指定" in result["message"]


def test_model_creation_guide_distinguishes_derived_and_new_architectures():
    result = tool._model_creation_guide()
    text = result["data"]["text"]

    assert result["status"] == "success"
    assert "已有模型" in text
    assert "全新结构" in text
    assert "模型来源" in text
    assert "自定义结构" in text


def test_custom_model_design_extracts_submitted_architecture():
    result = tool._analyze_custom_model_design({"description": """创建自定义模型 transformer_gru_v1。
输入：256x5 五通道生理信号。
编码器：3 层 Transformer，隐藏维度 128，4 个注意力头，dropout 0.2。
时序聚合：单层 GRU，隐藏维度 64。
分类头：GRU 最后时刻输出接二分类全连接层。
训练：120 轮，批次大小 32，学习率 0.0002。"""})

    assert result["status"] == "success"
    assert result["data"]["design"]["name"] == "transformer_gru_v1"
    assert result["data"]["design"]["temporal_module"] == "单层 GRU，隐藏维度 64"
    assert result["data"]["missing_sections"] == []


def test_operation_guide_derived_model_example_registers_trainable_configuration(monkeypatch):
    captured = {}

    def fake_call(api_name, **params):
        assert api_name == "api-model-register"
        captured.update(params)
        return {"model_id": "model-guide-example", "name": params["name"]}

    monkeypatch.setattr(tool, "_call_api", fake_call)
    result = tool._analyze_custom_model_design({
        "description": """请创建并注册一个名为 GuideSegRNNDerived 的可训练模型。
模型来源：SegRNN。
输入：256x5 五通道生理信号，通道顺序为 SpO2、HR、PR、RR、PI。
编码器：沿用 SegRNN 编码器，隐藏维度 d_model=32。
时序聚合：沿用 SegRNN 的时序分段聚合。
分类头：二分类头，输出低氧和非低氧两类。
训练：使用 AdamW，1 轮，批次大小 8，学习率 0.00025。""",
        "base_model": "SegRNN",
        "model_resource_name": "GuideSegRNNDerived",
        "model_config": {"d_model": 32},
        "training_config": {"epochs": 1, "batch_size": 8, "learning_rate": 0.00025, "use_ema": False},
        "register_model": True,
    })

    assert result["status"] == "success"
    assert result["data"]["trainable"] is True
    assert result["data"]["model_id"] == "model-guide-example"
    assert result["data"]["missing_sections"] == []
    assert captured["name"] == "GuideSegRNNDerived"
    assert captured["training_config"]["model_name"] == "SegRNN"
    assert captured["training_config"]["architecture"]["d_model"] == 32
    assert captured["training_config"]["training"]["epochs"] == 1


def test_custom_model_design_can_reparse_its_rendered_summary():
    result = tool._analyze_custom_model_design({"description": """自定义模型设计：transformer_gru_v1
- 输入：256x5 五通道生理信号
- 编码器：3 层 Transformer，隐藏维度 128，4 个注意力头，dropout 0.2
- 时序聚合：单层 GRU，隐藏维度 64
- 分类头：GRU 最后时刻输出接二分类全连接层
- 训练：120 轮，批次大小 32，学习率 0.0002"""})

    assert result["data"]["design"]["name"] == "transformer_gru_v1"
    assert result["data"]["design"]["encoder"].startswith("3 层 Transformer")
    assert result["data"]["missing_sections"] == []


def test_dataset_source_selection_has_two_explicit_branches():
    raw = tool._select_dataset_source({"dataset_source_type": "原始数据"})
    processed = tool._select_dataset_source({"dataset_source_type": "处理好的数据"})
    processed_alias = tool._select_dataset_source({"dataset_source_type": "已处理数据"})

    assert raw["data"]["dataset_source_type"] == "raw"
    assert raw["data"]["next_mode"] == "process-raw-dataset"
    assert processed["data"]["dataset_source_type"] == "processed"
    assert processed["data"]["next_mode"] == "validate"
    assert processed_alias["data"]["dataset_source_type"] == "processed"


def test_dataset_source_selection_preserves_provided_path():
    path = r"D:\data\processed-windows"
    result = tool._select_dataset_source({
        "dataset_source_type": "processed", "source_path": path,
    })

    assert result["data"]["source_path"] == path
    assert result["data"]["next_mode"] == "validate"
    assert "继续" in result["data"]["next_step"]


def test_dataset_source_selection_preserves_raw_staging_root():
    result = tool._select_dataset_source({
        "dataset_source_type": "raw",
        "source_path": r"D:\data\raw",
        "staging_root": r"E:\sotaband_raw_staging",
    })

    assert result["data"]["staging_root"] == r"E:\sotaband_raw_staging"


def test_raw_processing_request_uses_monitor_pipeline(tmp_path, builtin_component):
    source = tmp_path / "raw-data"
    source.mkdir()

    result = tool._build_raw_processing_request({"source_path": str(source)})

    assert result["status"] == "success"
    request = result["data"]["task_request"]
    assert "raw_zip_pipeline.py" in request["command"][1]
    assert request["command"][2] == "--pipeline"
    assert "run_dedup_pipeline.py" in request["command"][3]
    assert request["command"][-2:] == ["--configs", "256:30"]
    assert result["data"]["processed_dataset_path"].endswith(
        "balanced_windows_256s_30s_all"
    )


def test_raw_processing_request_accepts_zip_archive(tmp_path, builtin_component):
    archive = tmp_path / "monitor-export.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("batch/patient/ParameterData/monitor.csv", "Time,SpO2,HR,PR,RR,PI\n")

    result = tool._build_raw_processing_request({"source_path": str(archive)})

    assert result["status"] == "success"
    assert result["data"]["archive_support"] is True
    assert result["data"]["archive_count"] == 1


def test_raw_processing_request_passes_custom_staging_root(tmp_path, builtin_component):
    archive = tmp_path / "monitor-export.zip"
    staging = tmp_path / "raw-staging"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("ParameterData/monitor.csv", "Time,SpO2,HR,PR,RR,PI\n")

    result = tool._build_raw_processing_request({
        "source_path": str(archive),
        "staging_root": str(staging),
    })

    assert result["status"] == "success"
    command = result["data"]["task_request"]["command"]
    assert command[-2:] == ["--staging-root", str(staging.resolve())]
    assert result["data"]["staging_root"] == str(staging)


@pytest.mark.parametrize("nested", [False, True])
def test_raw_processing_request_rejects_staging_root_overlapping_source(
    tmp_path, builtin_component, nested
):
    source = tmp_path / "raw-data"
    source.mkdir()
    archive = source / "monitor-export.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("ParameterData/monitor.csv", "Time,SpO2,HR,PR,RR,PI\n")

    staging = source if not nested else source / "staging"
    result = tool._build_raw_processing_request({
        "source_path": str(source),
        "staging_root": str(staging),
    })

    assert result["status"] == "failed"
    assert "staging_root" in result["message"]
    assert "原始数据目录" in result["message"]


def test_raw_processing_request_accepts_window_and_balance_settings(tmp_path, builtin_component):
    source = tmp_path / "raw-data"
    source.mkdir()
    result = tool._build_raw_processing_request({
        "source_path": str(source),
        "lookback_seconds": 180,
        "prediction_interval_seconds": 60,
        "max_non_hypo_ratio": 1.0,
        "spo2_threshold": 92.0,
    })

    assert result["status"] == "success"
    data = result["data"]
    assert data["window_config"] == "180:60"
    assert data["processing_config"]["max_non_hypo_ratio"] == 1.0
    command = data["task_request"]["command"]
    assert "180:60" in command
    assert "--max-non-hypo-ratio" in command


def test_dataset_validation_reports_window_statistics(tmp_path):
    dataset = tmp_path / "balanced_windows_180s_60s_all"
    dataset.mkdir()
    (dataset / "window_features.pkl").write_bytes(b"placeholder")
    (dataset / "window_info.pkl").write_bytes(b"placeholder")
    (dataset / "window_stats.json").write_text(
        '{"lookback_seconds":180,"interval_seconds":60,"total_windows":12,'
        '"hypoxemia_windows":4,"non_hypoxemia_windows":8}',
        encoding="utf-8",
    )

    result = tool._validate_dataset(str(dataset))

    assert result["status"] == "success"
    assert result["data"]["sequence_length"] == 180
    assert result["data"]["prediction_interval_seconds"] == 60
    assert result["data"]["window_count"] == 12
    assert result["data"]["hypoxemia_window_count"] == 4
    assert result["data"]["non_hypoxemia_window_count"] == 8


def test_dataset_validation_does_not_report_missing_statistics_as_zero(tmp_path):
    dataset = tmp_path / "uploaded_window_batch"
    dataset.mkdir()
    (dataset / "window_features.pkl").write_bytes(b"placeholder")
    (dataset / "window_info.pkl").write_bytes(b"placeholder")

    result = tool._validate_dataset(str(dataset))

    assert result["status"] == "success"
    assert result["data"]["window_count"] is None
    assert result["data"]["hypoxemia_window_count"] is None
    assert result["data"]["hypoxemia_ratio"] is None
    assert "未提供 window_stats.json" in result["data"]["stats_status"]


def test_model_parameter_schema_includes_advanced_architecture_and_optimizer():
    result = tool._model_parameter_schema({"model_id": "SegRNNDualScaleV2"})

    assert result["status"] == "success"
    assert "e_layers" in result["data"]["architecture_groups"]["层与注意力"]
    assert "gcn_depth" in result["data"]["architecture_groups"]["图与状态空间"]
    assert result["data"]["optimizer_options"] == ["adamw", "adam", "sgd", "rmsprop"]


def test_training_configuration_accepts_optimizer_and_advanced_architecture():
    _, _, configuration = tool._resolve_training_configuration({
        "model_id": "TimesNet",
        "model_config": {"e_layers": 4, "factor": 3, "distil": False},
        "training_config": {"optimizer": "sgd", "seed": 7},
    })

    assert configuration["architecture"]["e_layers"] == 4
    assert configuration["architecture"]["distil"] is False
    assert configuration["training"]["optimizer"] == "sgd"


def test_training_request_maps_learning_rate_and_optimizer_to_script_flags(tmp_path, builtin_component):
    dataset = tmp_path / "windows"
    dataset.mkdir()
    (dataset / "window_features.pkl").write_bytes(b"fixture")
    (dataset / "window_info.pkl").write_bytes(b"fixture")
    (dataset / "window_stats.json").write_text(
        '{"lookback_seconds":256,"sampling_hz":1}', encoding="utf-8"
    )
    result = tool._build_train_request({
        "dataset": str(dataset),
        "model_id": "TimesNet",
        "project_path": str(builtin_component),
        "python_executable": sys.executable,
        "training_config": {"optimizer": "adam", "learning_rate": 0.0001, "epochs": 1},
    })

    assert result["status"] == "success"
    command = result["data"]["task_request"]["command"]
    assert result["data"]["task_request"]["metadata"]["save_trained_model"] is False
    assert "--optimizer" in command and command[command.index("--optimizer") + 1] == "adam"
    assert "--lr" in command and command[command.index("--lr") + 1] == "0.0001"
    assert "--learning-rate" not in command


def test_external_dataset_paths_do_not_require_a_trusted_root(tmp_path, monkeypatch):
    external = tmp_path / "external-data"
    uploaded = external / "batch-1" / "window_features.pkl"
    uploaded.parent.mkdir(parents=True)
    uploaded.write_bytes(b"test")
    monkeypatch.setenv("SPO2_HYPOXEMIA_TRUSTED_DATA_ROOTS", "")
    assert tool._is_trusted_pickle(uploaded)


def test_excel_export_creates_valid_workbook(tmp_path):
    path = tmp_path / "evaluation.xlsx"
    tool._write_metrics_xlsx(path, {
        "validation": {"auc": 0.8}, "test": {"f1": 0.7}, "test_at_0_5": {},
    })

    with zipfile.ZipFile(path) as workbook:
        assert workbook.testzip() is None
        sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "validation" in sheet
        assert "0.8" in sheet
