"""The API and domain tools must share upstream's private resource catalog."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_isolated_runtime_uses_one_private_catalog_across_processes(tmp_path):
    root = Path(__file__).resolve().parents[2]
    builtin = root / "resources/models/registry.json"
    before = builtin.read_bytes()
    env = {**os.environ, "SOTABAND_LOAD_DOTENV": "0",
           "SOTABAND_RUNTIME_ROOT": str(tmp_path)}
    env.pop("SOTABAND_PRIVATE_RESOURCES", None)
    create = """
import asyncio
from core.resource.paths import PRIVATE_ROOT
from core.resource.registry.model_registry import ModelRegistry
from core.runtime.paths import MODEL_DIR, model_registry_path
from core.timeseries.components import JsonModelCatalog
assert MODEL_DIR == PRIVATE_ROOT / 'models'
assert model_registry_path() == MODEL_DIR / 'registry.json'
asyncio.run(ModelRegistry().register({'id': 'integration-config', 'name': 'integration-config'}))
assert any(m['id'] == 'integration-config' for m in JsonModelCatalog().list_models())
"""
    subprocess.run([sys.executable, "-c", create], cwd=root, env=env, check=True,
                   capture_output=True, text=True)
    load = """
import asyncio,json
from core.resource.registry.model_registry import ModelRegistry
print(json.dumps(asyncio.run(ModelRegistry().get('integration-config'))))
"""
    result = subprocess.run([sys.executable, "-c", load], cwd=root, env=env,
                            check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)["id"] == "integration-config"
    assert builtin.read_bytes() == before
    assert (tmp_path / "resources-private/models/registry.json").is_file()


def test_explicit_private_root_takes_precedence(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "SOTABAND_LOAD_DOTENV": "0",
           "SOTABAND_RUNTIME_ROOT": str(tmp_path / "runtime"),
           "SOTABAND_PRIVATE_RESOURCES": str(tmp_path / "private")}
    code = """
from core.resource.paths import PRIVATE_ROOT
from core.runtime.paths import DATA_DIR, MODEL_DIR
assert DATA_DIR == PRIVATE_ROOT / 'data'
assert MODEL_DIR == PRIVATE_ROOT / 'models'
"""
    subprocess.run([sys.executable, "-c", code], cwd=root, env=env, check=True,
                   capture_output=True, text=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled"])
async def test_training_continuation_never_falls_back_to_dataset_scan(monkeypatch, status):
    from core.agent.base import AgentContext
    from core.task.task_manager import TaskManager
    from resources.workflows.hypoxemia.adapter import HypoxemiaWorkflowAdapter

    workflow = HypoxemiaWorkflowAdapter()
    workflow.workflow_states["training"] = {
        "dataset_source_type": "processed", "source_path": "synthetic-windows",
        "task_id": "training-task",
    }
    monkeypatch.setattr(TaskManager, "get", lambda self, task_id: {"status": status})
    ctx = AgentContext(agent_id="interactive-agent", session_id="training")
    events = [e async for e in workflow._execute_impl(ctx, "继续", [])]
    assert len(events) == 1
    assert events[0]["event"] == "content"
    assert "训练任务" in events[0]["data"]["text"]
    assert "training" not in workflow.pending_choices


def test_interactive_agent_alias_preserves_private_precedence(tmp_path, monkeypatch):
    from core.agent.factory import AgentFactory
    from core.resource import paths

    builtin, private = tmp_path / "builtin", tmp_path / "private"
    for root, name in [(builtin, "interactive_agent"), (private, "interactive-agent")]:
        directory = root / "agents/implementations" / name
        directory.mkdir(parents=True)
        (directory / "agent.py").write_text("# fixture", encoding="utf-8")
    monkeypatch.setattr(paths, "BUILTIN_ROOT", builtin)
    monkeypatch.setattr(paths, "PRIVATE_ROOT", private)
    assert Path(AgentFactory._resolve_impl_path("interactive-agent")) == private / "agents/implementations/interactive-agent"
    (private / "agents/implementations/interactive-agent/agent.py").unlink()
    assert Path(AgentFactory._resolve_impl_path("interactive-agent")) == builtin / "agents/implementations/interactive_agent"
