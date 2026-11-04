import json

import pytest

from core.timeseries import (
    DatasetRef,
    JsonModelCatalog,
    JsonResultStore,
    LocalArtifactStore,
    LocalDatasetProcessor,
    ModelRef,
)
from core.timeseries.manifests import read_manifest, write_dataset_manifest, write_model_manifest


def test_local_dataset_processor_validates_reference(tmp_path):
    source = tmp_path / "dataset"
    source.mkdir()
    result = LocalDatasetProcessor().validate(DatasetRef(id="data-1", path=source))
    assert result["dataset_id"] == "data-1"
    assert result["is_directory"] is True
    with pytest.raises(FileNotFoundError):
        LocalDatasetProcessor().validate(tmp_path / "missing")
    with pytest.raises(ValueError):
        LocalDatasetProcessor().validate(DatasetRef(id="no-path"))


def test_result_store_round_trips_payload(tmp_path):
    store = JsonResultStore(tmp_path)
    saved = store.save({"auc": 0.91}, result_id="evaluation-1", model_id="model-1")
    loaded = store.load("evaluation-1")
    assert saved["result_id"] == "evaluation-1"
    assert loaded["result"] == {"auc": 0.91}
    assert loaded["metadata"] == {"model_id": "model-1"}
    with pytest.raises(ValueError):
        store.save({}, result_id="../outside")
    with pytest.raises(ValueError):
        store.load("../outside")


def test_manifests_round_trip_compatibility_metadata(tmp_path):
    dataset = write_dataset_manifest(
        tmp_path / "dataset", dataset_id="data-1",
        feature_order=["SpO2", "HR", "PR", "RR", "PI"], sequence_length=256,
    )
    model = write_model_manifest(
        tmp_path / "model", model_id="model-1",
        feature_order=["SpO2", "HR", "PR", "RR", "PI"], sequence_length=256,
    )
    assert read_manifest(dataset.parent, "dataset_manifest.json")["dataset_id"] == "data-1"
    assert read_manifest(model.parent)["model_id"] == "model-1"


def test_artifact_store_and_model_catalog_are_local(tmp_path):
    source = tmp_path / "artifact"
    source.mkdir()
    (source / "weights.bin").write_bytes(b"weights")
    destination = LocalArtifactStore().save(source, destination=tmp_path / "saved")
    assert (tmp_path / "saved" / "weights.bin").read_bytes() == b"weights"
    with pytest.raises(FileExistsError):
        LocalArtifactStore().save(source, destination=tmp_path / "saved")

    registry = tmp_path / "registry.json"
    registry.write_text("[]", encoding="utf-8")
    catalog = JsonModelCatalog(registry)
    model = catalog.create({"name": "generic-model", "type": "model"})
    assert catalog.list_models()[0]["id"] == model.id
    assert json.loads(registry.read_text(encoding="utf-8"))[0]["name"] == "generic-model"
    assert LocalArtifactStore().load(ModelRef(artifact_path=tmp_path / "saved")) == (tmp_path / "saved").resolve()
