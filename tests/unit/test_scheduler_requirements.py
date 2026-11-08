"""资源需求：front-matter 声明的校验、静态推断、声明优先于推断"""

import pytest

from core.scheduler import requirements as rq
from core.scheduler.requirements import (
    RequirementsError,
    ResourceRequirements,
    combine,
    infer_from_code,
    parse_requirements,
    read_declared,
    resolve_tool_requirements,
)


def test_parse_full_declaration():
    req = parse_requirements({"gpu": "required", "min_vram_gb": 8, "min_ram_gb": 2.5, "cpu_cores": 4})
    assert (req.gpu, req.min_vram_gb, req.min_ram_gb, req.cpu_cores) == ("required", 8.0, 2.5, 4)
    assert req.source == "declared"


@pytest.mark.parametrize("value, expected", [(True, "required"), (False, "none")])
def test_parse_accepts_yaml_bool_for_gpu(value, expected):
    assert parse_requirements({"gpu": value}).gpu == expected


@pytest.mark.parametrize(
    "raw, fragment",
    [
        ({"gpu": "maybe"}, "resources.gpu"),
        ({"min_vram_gb": -1}, "不能为负数"),
        ({"min_vram_gb": "8GB"}, "应为数字"),
        ({"cpu_cores": 1.5}, "应为整数"),
        ({"cpu_cores": True}, "应为数字"),
        ({"gpus": 2}, "未知字段: gpus"),
        (["gpu"], "应为键值对"),
    ],
)
def test_parse_rejects_invalid(raw, fragment):
    with pytest.raises(RequirementsError, match=fragment):
        parse_requirements(raw)


SPEC_WITH_RESOURCES = """---
id: trainer
name: 训练
resources:
  gpu: required
  min_vram_gb: 12
---

# 训练
"""


def test_read_declared_from_front_matter():
    req = read_declared(SPEC_WITH_RESOURCES)
    assert req.gpu == "required" and req.min_vram_gb == 12


@pytest.mark.parametrize("md", ["---\nid: x\n---\n# x", "# 没有 front-matter", "---\nid: x\n"])
def test_read_declared_absent(md):
    assert read_declared(md) is None


def test_read_declared_rejects_bad_yaml():
    with pytest.raises(RequirementsError, match="YAML"):
        read_declared("---\nresources: [unclosed\n---\n")


@pytest.mark.parametrize(
    "code, gpu",
    [
        ("import torch\n", "optional"),
        ("from tensorflow.keras import layers\n", "optional"),
        ("def run():\n    import ultralytics\n", "optional"),
        ("x = 'cuda'\n", "optional"),
        ("import numpy as np\nimport pandas\n", "none"),
        ("from .torch import helper\n", "none"),          # 相对导入不是 torch 库
        ("import torch\ndef broken(:\n", "optional"),       # 语法错误时退化为正则
    ],
)
def test_infer_from_code(code, gpu):
    req = infer_from_code(code)
    assert req.gpu == gpu
    assert req.source == "inferred"


def test_infer_is_cached_by_code_hash():
    code = "import torch  # cache-probe\n"
    assert infer_from_code(code) is infer_from_code(code)


@pytest.fixture
def tools_dir(tmp_path, monkeypatch):
    (tmp_path / "definitions").mkdir()
    (tmp_path / "implementations" / "trainer").mkdir(parents=True)
    (tmp_path / "implementations" / "trainer" / "tool.py").write_text("import torch\n", encoding="utf-8")
    monkeypatch.setattr(rq, "TOOLS_DIR", tmp_path)
    return tmp_path


def test_resolve_prefers_declaration(tools_dir):
    (tools_dir / "definitions" / "trainer.md").write_text(SPEC_WITH_RESOURCES, encoding="utf-8")
    req = resolve_tool_requirements("trainer")
    assert req.source == "declared" and req.gpu == "required"


def test_resolve_infers_registered_code_without_declaration(tools_dir):
    (tools_dir / "definitions" / "trainer.md").write_text("---\nid: trainer\n---\n", encoding="utf-8")
    req = resolve_tool_requirements("trainer")
    assert req.source == "inferred" and req.gpu == "optional"


def test_resolve_uses_passed_code_over_registered(tools_dir):
    # 自动调试时传入的是正在修改的代码，而非已注册的版本
    assert resolve_tool_requirements("trainer", code="print(1)\n").gpu == "none"


def test_resolve_unknown_tool_defaults_to_none(tools_dir):
    req = resolve_tool_requirements("missing")
    assert req.gpu == "none" and req.source == "default"


def test_combine_takes_upper_bound():
    a = ResourceRequirements(gpu="optional", min_vram_gb=4, min_ram_gb=8)
    b = ResourceRequirements(gpu="required", min_vram_gb=2, cpu_cores=2)
    c = combine([a, b], reason="tools")
    assert (c.gpu, c.min_vram_gb, c.min_ram_gb, c.cpu_cores) == ("required", 4, 8, 2)
    assert combine([], reason="none").gpu == "none"


# ── Agent ──

AGENT_CALLING_TOOL = '''
class A:
    def _call_tool_trainer(self, **params):
        impl_path = PROJECT_ROOT / "resources" / "tools" / "implementations" / "trainer" / "tool.py"
'''


@pytest.fixture
def agents_dir(tmp_path, monkeypatch, tools_dir):
    root = tmp_path / "agents"
    (root / "definitions").mkdir(parents=True)
    (root / "implementations" / "bot").mkdir(parents=True)
    (root / "registry.json").write_text('[{"id": "bot", "tools": []}]', encoding="utf-8")
    monkeypatch.setattr(rq, "AGENTS_DIR", root)
    return root


def _agent_code(agents_dir, code):
    (agents_dir / "implementations" / "bot" / "agent.py").write_text(code, encoding="utf-8")


def test_agent_declaration_wins(agents_dir):
    (agents_dir / "definitions" / "bot.md").write_text(
        "---\nresources:\n  gpu: required\n  min_vram_gb: 6\n---\n", encoding="utf-8")
    _agent_code(agents_dir, AGENT_CALLING_TOOL)
    req = rq.resolve_agent_requirements("bot")
    assert req.source == "declared" and req.min_vram_gb == 6


def test_agent_inherits_requirements_of_tools_referenced_in_code(agents_dir, tools_dir):
    (tools_dir / "definitions" / "trainer.md").write_text(SPEC_WITH_RESOURCES, encoding="utf-8")
    _agent_code(agents_dir, AGENT_CALLING_TOOL)
    req = rq.resolve_agent_requirements("bot")
    assert (req.gpu, req.min_vram_gb) == ("required", 12)
    assert "trainer(required)" in req.reason


def test_agent_inherits_tools_listed_in_registry(agents_dir):
    (agents_dir / "registry.json").write_text('[{"id": "bot", "tools": ["trainer"]}]', encoding="utf-8")
    _agent_code(agents_dir, "print('no tool helpers')\n")
    assert rq.resolve_agent_requirements("bot").gpu == "optional"  # trainer 代码引用了 torch


def test_agent_own_code_is_inferred(agents_dir):
    _agent_code(agents_dir, "import transformers\n")
    req = rq.resolve_agent_requirements("bot")
    assert req.gpu == "optional" and "transformers" in req.reason


def test_plain_agent_needs_nothing(agents_dir):
    _agent_code(agents_dir, "import json\n")
    assert rq.resolve_agent_requirements("bot").gpu == "none"


def test_private_tool_requirements_override_builtin(monkeypatch, tmp_path):
    from core.resource import paths

    builtin = tmp_path / "builtin"
    private = tmp_path / "private"
    monkeypatch.setattr(rq, "TOOLS_DIR", builtin)
    monkeypatch.setattr(paths, "PRIVATE_ROOT", private)
    (builtin / "definitions").mkdir(parents=True)
    (builtin / "definitions" / "trainer.md").write_text(SPEC_WITH_RESOURCES)
    impl = private / "tools" / "implementations" / "trainer"
    impl.mkdir(parents=True)
    (impl / "spec.md").write_text("---\nresources:\n  gpu: none\n---\n")
    assert resolve_tool_requirements("trainer").gpu == "none"
    (impl / "spec.md").write_text("# 无显式声明")
    (impl / "tool.py").write_text("import torch\n")
    assert resolve_tool_requirements("trainer").gpu == "optional"


def test_private_agent_requirements_include_private_tools(monkeypatch, tmp_path):
    from core.resource import paths

    monkeypatch.setattr(paths, "PRIVATE_ROOT", tmp_path)
    monkeypatch.setattr(rq, "AGENTS_DIR", tmp_path / "builtin-agents")
    monkeypatch.setattr(rq, "TOOLS_DIR", tmp_path / "builtin-tools")
    impl = tmp_path / "agents" / "implementations" / "private-agent"
    impl.mkdir(parents=True)
    (impl / "spec.md").write_text("---\nresources:\n  gpu: required\n---\n")
    assert rq.resolve_agent_requirements("private-agent").gpu == "required"
    (impl / "spec.md").write_text("# 无显式声明")
    (impl / "agent.py").write_text("# CPU code\n")
    (tmp_path / "agents" / "registry.json").write_text(
        '[{"id": "private-agent", "tools": ["private-tool"]}]')
    tool = tmp_path / "tools" / "implementations" / "private-tool"
    tool.mkdir(parents=True)
    (tool / "tool.py").write_text("import torch\n")
    assert rq.resolve_agent_requirements("private-agent").gpu == "optional"
