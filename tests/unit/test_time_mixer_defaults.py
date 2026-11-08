"""The platform must not flatten TimeMixer's required multiscale defaults."""

import importlib.util
import json
from pathlib import Path

import pytest

from resources.workflows.hypoxemia.conversation import HypoxemiaConversation


TOOL_PATH = Path(__file__).resolve().parents[2] / "resources/tools/implementations/timeseries-classification-runner/tool.py"
SPEC = importlib.util.spec_from_file_location("time_mixer_default_test_tool", TOOL_PATH)
assert SPEC and SPEC.loader
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


@pytest.mark.parametrize("model_name", ["TimeMixer", "TimeMixerClsPool", "SegRNNTimeMixerFusion"])
def test_catalog_model_uses_multiscale_defaults_in_card_and_training(model_name):
    card = tool._model_parameter_schema({"model_id": model_name})["data"]["architecture"]
    _, _, resolved = tool._resolve_training_configuration({"model_id": model_name})

    assert card["down_sampling_layers"] == resolved["architecture"]["down_sampling_layers"] == 2
    assert card["down_sampling_window"] == resolved["architecture"]["down_sampling_window"] == 2


def test_registered_time_mixer_config_preserves_effective_defaults(monkeypatch):
    captured = {}

    def register(_api, **values):
        captured.update(values)
        return {"model_id": "model-test"}

    monkeypatch.setattr(tool, "_call_api", register)
    result = tool._create_model({"model_name": "TimeMixer", "model_config": {"d_model": 32}})

    assert result["status"] == "success"
    architecture = captured["training_config"]["architecture"]
    assert architecture["d_model"] == 32
    assert architecture["down_sampling_layers"] == 2
    assert architecture["down_sampling_window"] == 2


def test_legacy_invalid_time_mixer_config_fails_before_task_submission(monkeypatch):
    legacy = {"id": "model-legacy", "model_name": "TimeMixer", "training_config": {
        "model_name": "TimeMixer", "architecture": dict(tool.DEFAULT_MODEL_CONFIG), "training": {},
    }}
    monkeypatch.setattr(tool, "_resolve_model_resource", lambda _id: legacy)

    with pytest.raises(ValueError, match="down_sampling_layers >= 1"):
        tool._resolve_training_configuration({"model_id": "model-legacy"})

    _, _, corrected = tool._resolve_training_configuration({
        "model_id": "model-legacy",
        "model_config": {"down_sampling_layers": 2, "down_sampling_window": 2},
    })
    assert corrected["architecture"]["down_sampling_layers"] == 2


def test_workflow_summary_matches_time_mixer_card():
    summary = HypoxemiaConversation._workflow_ready_summary({
        "model_id": "TimeMixer", "model_name": "TimeMixer",
    })
    architecture = json.loads(summary.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert architecture["down_sampling_layers"] == 2
    assert architecture["down_sampling_window"] == 2
