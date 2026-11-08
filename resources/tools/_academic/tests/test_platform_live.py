import json
import os
import time
from pathlib import Path

import pytest

from core.executor.tool_executor import ToolExecutor
from core.resource.builder.tool_builder import ToolCodeBuilder
from resources.tools._academic.common import ROOT, command, invoke

IDS = ["github-code-search", "code-reproduction", "paper-tracker", "paper-writing"]


def test_registry_specs_and_mode_parameters_match():
    entries = json.loads((ROOT / "resources/tools/registry.json").read_text(encoding="utf-8"))
    for ident in IDS:
        entry = next(e for e in entries if e["id"] == ident)
        spec = (ROOT / "resources/tools" / entry["spec_path"]).read_text(encoding="utf-8")
        assert spec == (ROOT / "resources/tools" / entry["impl_path"] / "spec.md").read_text(
            encoding="utf-8"
        )
        assert ToolCodeBuilder.parse_modes(spec) == entry["modes"]
        parsed = ToolCodeBuilder._parse_spec_inputs(spec)
        expected = [p for p in entry["param_meta"] if p.get("mode")]
        assert [(p["name"], p["mode"], p["required"]) for p in parsed] == [
            (p["name"], p["mode"], p["required"]) for p in expected
        ]


@pytest.mark.parametrize(
    "ident,params",
    [
        ("github-code-search", {"mode": "search_code", "query": "model", "snapshot_id": "missing"}),
        ("code-reproduction", {"mode": "jobs"}),
        ("paper-tracker", {"mode": "catalog", "venues": ["ACL", "ICLR"]}),
        ("paper-writing", {"mode": "create", "title": "Isolated manuscript"}),
    ],
)
def test_actual_platform_executor(ident, params):
    response = ToolExecutor.execute_sync(
        ident, {**params, "register_output": False, "optional": None}, timeout=30
    )
    assert {"status", "output_format", "message", "data"} <= response.keys()
    assert response["status"] == ("failed" if ident == "github-code-search" else "success")


def test_worker_once_releases_lease(store):
    from resources.tools._academic.worker import control, serve

    serve(store, once=True)
    assert not control("worker_status", store)["alive"]
    with store.lock("scheduler"):
        pass


def test_detached_worker_start_stop(store):
    from resources.tools._academic.worker import control

    started = control("worker_start", store)
    try:
        for _ in range(30):
            state = control("worker_status", store)
            if state.get("pid") and state.get("launch_id") == started["launch_id"]:
                break
            time.sleep(0.2)
        assert (
            state.get("pid") and state.get("launch_id") == started["launch_id"] and state["alive"]
        )
    finally:
        control("worker_stop", store)
        for _ in range(80):
            if not control("worker_status", store)["alive"]:
                break
            time.sleep(0.2)
        assert not control("worker_status", store)["alive"]


@pytest.mark.skipif(os.getenv("ACADEMIC_LIVE_HTTP") != "1", reason="explicit external HTTP opt-in")
def test_live_github(monkeypatch):
    monkeypatch.setenv("SOTABAND_ACADEMIC_HTTP_PROXY", "direct")
    response = invoke("github", {"mode": "search", "query": "repo:pytorch/examples", "limit": 1})
    assert response["status"] == "success", response
    assert response["data"]["items"][0]["repository"] == "pytorch/examples"
    snapshot = invoke("github", {"mode": "inspect", "repository": "pytorch/examples"})
    assert snapshot["status"] == "success", snapshot
    assert len(snapshot["data"]["commit"]) == 40


@pytest.mark.skipif(os.getenv("ACADEMIC_LIVE_HTTP") != "1", reason="explicit external HTTP opt-in")
@pytest.mark.parametrize("venue,source", [("ACL", "anthology"), ("CVPR", "cvf"), ("JMLR", "jmlr")])
def test_live_official_sources(venue, source, monkeypatch):
    monkeypatch.setenv("SOTABAND_ACADEMIC_HTTP_PROXY", "direct")
    response = invoke(
        "papers",
        {
            "mode": "search",
            "venues": [venue],
            "source": source,
            "since": "2025-01-01",
            "until": "2025-12-31",
            "query": "learning",
        },
    )
    assert response["status"] == "success", response
    assert response["data"]["items"]
    assert not response["data"]["partial"]


def gpu_plan(source, command_args, timeout=120):
    response = invoke(
        "reproduction",
        {
            "mode": "plan",
            "source_path": str(source),
            "plan_json": {
                "image": os.getenv("ACADEMIC_GPU_IMAGE", "sotaband-academic-gpu:1.0"),
                "command": command_args,
                "timeout_seconds": timeout,
                "memory_gb": 4,
                "min_free_mib": 512,
                "targets": {
                    "mse": {
                        "value": 0.0001,
                        "tolerance": 0.0001,
                        "direction": "lower",
                        "source": "Synthetic fixture",
                        "protocol": "Not a paper benchmark",
                    }
                },
            },
        },
    )
    assert response["status"] == "success", response
    return response["data"]["id"]


def wait_job(ident):
    for _ in range(90):
        response = invoke("reproduction", {"mode": "status", "job_id": ident})
        assert response["status"] == "success", response
        if response["data"]["state"] in ("succeeded", "failed", "timed_out", "cancelled"):
            return response["data"]
        time.sleep(2)
    raise AssertionError("GPU job did not finish")


@pytest.mark.skipif(os.getenv("ACADEMIC_LIVE_GPU") != "1", reason="explicit real GPU opt-in")
def test_real_gpu_training_timeout_and_cancel(store):
    source = ROOT / "resources/tools/implementations/code-reproduction/tests/gpu_smoke"
    started = []
    try:
        plan_id = gpu_plan(source, ["python", "train.py"])
        response = invoke(
            "reproduction", {"mode": "run", "plan_id": plan_id, "idempotency_key": "gpu-smoke"}
        )
        assert response["status"] == "success", response
        ident = response["data"]["id"]
        started.append(ident)
        job = wait_job(ident)
        assert job["state"] == "succeeded", job
        assert job["evaluation"]["conclusion"] == "metric_matched", job
        assert job["evaluation"]["metrics"]["device"]
        assert (store.folder("runs", ident) / "output/checkpoint.pt").is_file()
        print(json.dumps({"gpu": job["devices"], "evaluation": job["evaluation"]}))
        report = invoke("reproduction", {"mode": "report", "job_id": ident})
        assert Path(report["data"]["file_path"]).is_file()
        manuscript = invoke(
            "writing", {"mode": "create", "title": "GPU integration record", "job_ids": [ident]}
        )
        exported = invoke("writing", {"mode": "export", "project_id": manuscript["data"]["id"]})
        text = Path(exported["data"]["file_path"]).read_text(encoding="utf-8")
        assert str(job["evaluation"]["metrics"]["mse"]) in text
        resume_plan = gpu_plan(
            source, ["python", "train.py", "--resume", "/checkpoint/checkpoint.pt"]
        )
        response = invoke(
            "reproduction", {"mode": "resume", "job_id": ident, "plan_id": resume_plan}
        )
        assert response["status"] == "success", response
        started.append(response["data"]["id"])
        resumed = wait_job(started[-1])
        assert resumed["state"] == "succeeded" and resumed["evaluation"]["metrics"]["resumed"]
        for action, timeout in [("timeout", 2), ("cancel", 120)]:
            plan_id = gpu_plan(source, ["python", "-c", "import time; time.sleep(60)"], timeout)
            response = invoke("reproduction", {"mode": "run", "plan_id": plan_id})
            ident = response["data"]["id"]
            started.append(ident)
            assert (
                invoke("reproduction", {"mode": "status", "job_id": ident})["status"] == "success"
            )
            if action == "cancel":
                response = invoke("reproduction", {"mode": "cancel", "job_id": ident})
                assert response["data"]["state"] == "cancelled"
            else:
                assert wait_job(ident)["state"] == "timed_out"
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM gpu_leases").fetchone()[0] == 0
    finally:
        for ident in started:
            invoke("reproduction", {"mode": "cancel", "job_id": ident})
            record = store.get("job", ident)
            if record.get("container_id"):
                command(["docker", "rm", record["container_name"]])
