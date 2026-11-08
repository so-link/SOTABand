"""Agent 角色解析与 web-service 启动探测测试。

覆盖的线上缺陷：`AgentFactory._is_web_service()` 曾用「agent.py 文本里有没有
`web-service` 关键字」猜角色，而生成器会把 **agent id** 写进首行 docstring——
于是判定实际等价于「id 里是否含 web-service」：

- id 叫 `greeting-web-service` 的恰好命中 → 服务信息正常
- id 叫 `action-description-agent` 的 web-service Agent 判定失败 → 不抓
  `service_ready` → 服务起在端口上了，界面却显示"未运行"

修复后角色按权威来源解析（registry → spec.md → definitions MD → 代码兜底），
并且探测只对 web-service 生效（交互/任务型绝不能读 stdout）。
"""

import asyncio
import json

import pytest

from core.agent.factory import AgentFactory
from core.resource import paths


@pytest.fixture()
def tmp_roots(tmp_path, monkeypatch):
    """构造一个临时资源根（内置=私有=同一目录，便于覆盖两类读取）。"""
    root = tmp_path / "resources"
    (root / "agents").mkdir(parents=True)
    monkeypatch.setattr(paths, "resource_roots", lambda: [root])
    return root


def _write_registry(root, entries):
    (root / "agents" / "registry.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )


def _write_spec(impl_dir, role):
    impl_dir.mkdir(parents=True, exist_ok=True)
    (impl_dir / "spec.md").write_text(
        f"---\nid: x\nname: x\nrole: {role}\n---\n\n# x\n", encoding="utf-8"
    )


def _write_agent_py(impl_dir, code=""):
    impl_dir.mkdir(parents=True, exist_ok=True)
    (impl_dir / "agent.py").write_text(code or "class A: pass\n", encoding="utf-8")


# ── 角色解析优先级 ────────────────────────────────────────

def test_registry_role_wins_over_id_text(tmp_roots, tmp_path):
    """关键回归：id 含 `web-service` 但 registry 说 task，就必须是 task。"""
    _write_registry(tmp_roots, [{"id": "greeting-web-service", "role": "task"}])
    impl = tmp_path / "impl-greeting"
    _write_agent_py(impl, '"""问候 Web 服务 Agent — greeting-web-service"""\n')

    f = AgentFactory()
    assert f._resolve_role("greeting-web-service", str(impl)) == "task"
    assert f._is_web_service("greeting-web-service", str(impl)) is False


def test_registry_role_detects_web_service_without_keyword_in_id(tmp_roots, tmp_path):
    """id 不含 web-service 也必须是 web-service（本次修复的核心场景）。"""
    _write_registry(tmp_roots, [{"id": "action-description-agent", "role": "web-service"}])
    impl = tmp_path / "impl-action"
    _write_agent_py(impl, '"""Agent: 人物行为描述 Agent — action-description-agent"""\n')

    f = AgentFactory()
    assert f._resolve_role("action-description-agent", str(impl)) == "web-service"
    assert f._is_web_service("action-description-agent", str(impl)) is True


def test_falls_back_to_impl_spec_md(tmp_roots, tmp_path):
    _write_registry(tmp_roots, [])          # registry 无该条目
    impl = tmp_path / "impl-spec"
    _write_spec(impl, "web-service")
    _write_agent_py(impl)

    assert AgentFactory()._resolve_role("x-agent", str(impl)) == "web-service"


def test_falls_back_to_definitions_md(tmp_roots, tmp_path):
    _write_registry(tmp_roots, [])
    defs = tmp_roots / "agents" / "definitions"
    defs.mkdir(parents=True)
    (defs / "x-agent.md").write_text(
        "---\nid: x-agent\nname: x\nrole: interactive\n---\n\n# x\n", encoding="utf-8"
    )

    assert AgentFactory()._resolve_role("x-agent", None) == "interactive"


def test_falls_back_to_code_keyword_for_handwritten_agents(tmp_roots, tmp_path):
    _write_registry(tmp_roots, [])
    impl = tmp_path / "impl-code"
    _write_agent_py(impl, "from core.agent.base import AgentRole\nROLE = AgentRole.WEB_SERVICE\n")

    assert AgentFactory()._resolve_role("x-agent", str(impl)) == "web-service"


def test_defaults_to_task(tmp_roots, tmp_path):
    _write_registry(tmp_roots, [])
    impl = tmp_path / "impl-none"
    _write_agent_py(impl)
    assert AgentFactory()._resolve_role("x-agent", str(impl)) == "task"


def test_normalize_role_variants():
    n = AgentFactory._normalize_role
    assert n("web-service") == n("web_service") == n("WEBSERVICE") == "web-service"
    assert n("interactive") == "interactive"
    assert n("task") == "task"
    assert n("") == "" and n(None) == ""


# ── service_ready 探测 ────────────────────────────────────

class _FakeStdout:
    def __init__(self, lines, timeouts: int = 0):
        self._lines = list(lines)
        self._timeouts = timeouts

    async def readline(self):
        if self._timeouts > 0:
            self._timeouts -= 1
            raise asyncio.TimeoutError()
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _FakeProc:
    def __init__(self, stdout, returncode=None):
        self.stdout = stdout
        self.returncode = returncode


@pytest.mark.asyncio
async def test_probe_retries_until_service_ready():
    """第一次读取超时（依赖导入慢）不应直接放弃。"""
    f = AgentFactory()
    line = json.dumps({"event": "service_ready",
                       "data": {"host": "127.0.0.1", "port": 49933, "apis": ["ActionDes"]}}).encode() + b"\n"
    f._processes["a1"] = _FakeProc(_FakeStdout([line], timeouts=1))

    svc = await f._probe_service_ready("a1", timeout=3)
    assert svc["port"] == 49933
    assert f.get_service("a1")["apis"] == ["ActionDes"]


@pytest.mark.asyncio
async def test_probe_records_startup_error():
    f = AgentFactory()
    line = json.dumps({"event": "error", "data": {"message": "加载 spec 失败"}}).encode() + b"\n"
    f._processes["a2"] = _FakeProc(_FakeStdout([line]))

    assert await f._probe_service_ready("a2", timeout=2) is None
    assert f._startup_errors["a2"] == "加载 spec 失败"
    assert f.get_service("a2") is None


@pytest.mark.asyncio
async def test_probe_returns_none_on_eof():
    f = AgentFactory()
    f._processes["a3"] = _FakeProc(_FakeStdout([]))
    assert await f._probe_service_ready("a3", timeout=2) is None


@pytest.mark.asyncio
async def test_probe_without_process_returns_none():
    assert await AgentFactory()._probe_service_ready("missing") is None
