"""Agent 基类 — 所有 Agent 的抽象"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncGenerator


class AgentRole(Enum):
    """Agent 类型 — 三类体系

    - INTERACTIVE  交互式    ：与用户对话，理解意图并调用工具完成任务
    - TASK         任务式    ：按既定流程依次调用工具完成任务
    - WEB_SERVICE  Web 服务式：以 HTTP API 形式对外提供服务，组合调用工具

    所有类型运行于独立进程，均支持手动启动 / 关闭。
    """

    INTERACTIVE = "interactive"      # 交互式 — 对话式完成任务
    TASK = "task"                    # 任务式 — 按既定流程执行工具
    WEB_SERVICE = "web-service"      # Web 服务式 — 以 HTTP API 对外提供服务

    @classmethod
    def _missing_(cls, value):
        """容忍历史/未知取值，避免旧数据导致解析崩溃。

        orchestrator（编排型）并入 task；observer（观测型）并入 web-service；
        其余未知值一律回退为 TASK。
        """
        if not isinstance(value, str):
            return None
        v = value.strip().lower()
        if v == "orchestrator":
            return cls.TASK
        if v == "observer":
            return cls.WEB_SERVICE
        if v in ("web_service", "webservice", "web-service"):
            return cls.WEB_SERVICE
        return cls.TASK


@dataclass
class AgentContext:
    """每次调用的执行上下文"""

    agent_id: str
    session_id: str
    user_id: str = "default"
    metadata: dict = field(default_factory=dict)


@dataclass
class AgentSpec:
    """从 MD 规范文档解析出的结构化规格"""

    id: str
    name: str
    version: str = "0.1.0"
    role: AgentRole = AgentRole.TASK
    description: str = ""
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    required_tools: list = field(default_factory=list)
    optional_tools: list = field(default_factory=list)
    config: dict = field(default_factory=dict)
    communication: dict = field(default_factory=dict)
    raw_md: str = ""

    # ── 任务式（task）专有：既定流程 ──
    # 形如 [{"step": 1, "tool": "<tool-id>", "params": {...}, "output_bind": "step1"}]
    flow: list = field(default_factory=list)

    # ── Web 服务式（web-service）专有 ──
    # server: {"host": "127.0.0.1", "port": null}（port 为空则运行时动态分配）
    server: dict = field(default_factory=dict)
    # apis: [{"name": "...", "method": "POST", "path": "/...", "params": [...], "flow": [...], "response": [...]}]
    apis: list = field(default_factory=list)

    def parse_structured_fields(self) -> None:
        """从 raw_md 中解析 task 的 flow / web-service 的 server+apis。

        约定：MD 中使用带标记的 fenced code block 声明结构化配置：
            ```flow        （任务式：步骤列表，JSON 数组）
            ```server      （Web 服务式：服务配置，JSON 对象）
            ```apis        （Web 服务式：API 列表，JSON 数组）

        解析失败时保持原值不变（不抛异常），保证 Agent 仍可加载。
        """
        if not self.raw_md:
            return
        flow = _extract_fenced_json(self.raw_md, "flow")
        if isinstance(flow, list):
            self.flow = flow
        server = _extract_fenced_json(self.raw_md, "server")
        if isinstance(server, dict):
            self.server = server
        apis = _extract_fenced_json(self.raw_md, "apis")
        if isinstance(apis, list):
            self.apis = apis


class BaseAgent(ABC):
    """Agent 基类 — 所有 Agent 必须继承"""

    def __init__(self, spec: AgentSpec, config: dict = None):
        self.spec = spec
        self.config = config or spec.config
        self._running = False
        # 工具常驻运行时（懒加载；Web 服务式 Agent 用它把依赖工具常驻化）
        self._tool_runtime = None
        # Agent 间通信客户端（entrypoint 启动时绑定；见 core/agent/bus.py）
        # 用法：await self._bus.subscribe([...]) / publish(ch, msg) /
        #       await self._bus.call_agent(target, method, params)
        self._bus = None

    @property
    def agent_id(self) -> str:
        return self.spec.id

    @property
    def role(self) -> AgentRole:
        return self.spec.role

    # ── 工具调用辅助（任务式 / Web 服务式 Agent 共用） ──

    def get_tool_runtime(self):
        """获取本 Agent 的工具常驻运行时（懒加载）。"""
        if self._tool_runtime is None:
            from core.agent.tool_runtime import ToolRuntime
            from pathlib import Path

            project_root = Path(__file__).resolve().parent.parent.parent
            self._tool_runtime = ToolRuntime(project_root)
        return self._tool_runtime

    def resident_tool_ids(self) -> list[str]:
        """返回需要常驻化的工具 id 列表。

        默认：Web 服务式 Agent 将其**声明的工具集全部常驻化**
        （依据 MD 规范中的 【【工具名】】 引用，或在 registry 的 tools 字段）。
        子类可覆写以自定义常驻范围。
        """
        if self.role != AgentRole.WEB_SERVICE:
            return []
        try:
            from core.resource.registry.tool_registry import ToolRegistry

            reg = ToolRegistry()
            all_tools = reg._read()
            by_name = {t.get("name", ""): t.get("id", "") for t in all_tools}
            by_id = {t.get("id", ""): t.get("id", "") for t in all_tools}

            ids: list[str] = []
            # 1) MD 规范中的 【【工具名】】 引用
            import re as _re

            raw = getattr(self.spec, "raw_md", "") or ""
            for nm in _re.findall(r"【【([^】]+)】】", raw):
                tid = by_name.get(nm.strip()) or by_id.get(nm.strip())
                if tid and tid not in ids:
                    ids.append(tid)
            # 2) 流程 / API 声明中引用的工具
            for step in (getattr(self.spec, "flow", None) or []):
                tid = step.get("tool")
                if tid and tid not in ids:
                    ids.append(tid)
            for api in (getattr(self.spec, "apis", None) or []):
                for step in (api.get("flow") or []):
                    tid = step.get("tool")
                    if tid and tid not in ids:
                        ids.append(tid)
            return ids
        except Exception:
            return []

    async def warmup_resident_tools(self) -> dict:
        """预热本 Agent 声明的常驻工具（Web 服务式 Agent 启动时调用）。"""
        tool_ids = self.resident_tool_ids()
        if not tool_ids:
            return {}
        return await self.get_tool_runtime().ensure_started(tool_ids)

    async def call_tool(self, tool_id: str, params: dict | None = None) -> dict:
        """统一调用已注册工具。

        优先走本 Agent 的**常驻 Worker**（若该工具已常驻且就绪）——
        省去每次调用重复 import 重型库与加载模型的开销；
        常驻不可用时回退到 ToolExecutor 的一次性子进程执行，保证可用性。
        """
        params = dict(params or {})

        # 1) 尝试常驻路径
        runtime = self._tool_runtime
        if runtime is not None:
            result = await runtime.call(tool_id, params)
            if result is not None and not result.get("_runtime_error"):
                return result
            # 常驻失败 → 回退（并清除 runtime 错误标记）

        # 2) 回退：一次性子进程执行
        from core.executor.tool_executor import ToolExecutor

        return await ToolExecutor.execute(tool_id, params)

    @staticmethod
    def resolve_refs(value, scope: dict):
        """解析参数中的 "{...}" 引用（供流程步骤之间传递数据）。

        支持：
          - 整体引用："{step1.data.model_path}" → 直接取该值（保留原始类型）
          - 内嵌引用："prefix-{input.name}"  → 字符串插值
        """
        if isinstance(value, str):
            # 整体引用：整个字符串就是一个 {..} 表达式 → 返回原始值
            stripped = value.strip()
            if stripped.startswith("{") and stripped.endswith("}") and stripped.count("{") == 1:
                ref = stripped[1:-1].strip()
                return BaseAgent._lookup(ref, scope)
            # 内嵌引用：逐个替换
            import re as _re

            def _sub(m):
                return str(BaseAgent._lookup(m.group(1).strip(), scope))

            return _re.sub(r"\{([^{}]+)\}", _sub, value)
        if isinstance(value, dict):
            return {k: BaseAgent.resolve_refs(v, scope) for k, v in value.items()}
        if isinstance(value, list):
            return [BaseAgent.resolve_refs(v, scope) for v in value]
        return value

    @staticmethod
    def _lookup(ref: str, scope: dict):
        """按点分路径在 scope 中取值，缺失时返回空字符串。"""
        cur = scope
        for part in ref.split("."):
            part = part.strip()
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return ""
        return cur

    @abstractmethod
    async def execute(
        self, ctx: AgentContext, **kwargs
    ) -> AsyncGenerator[dict, None]:
        """
        执行 Agent 核心任务。

        Args:
            ctx: 执行上下文
            **kwargs: 输入参数（与 spec.inputs 对应）

        Yields:
            dict: 输出事件，格式 {"event": "content"|"card"|"done"|"error", "data": {...}}
        """
        ...

    async def on_start(self):
        """Agent 启动时的初始化（子类可覆盖）"""
        self._running = True

    async def on_stop(self):
        """Agent 停止时的清理。

        框架级行为：回收本 Agent 的常驻工具进程（若有）。
        常驻 Worker 退出后，其占用的 GPU 显存 / 主机内存会由操作系统自动释放，
        从而实现「Agent 退出 → 其加载的所有工具一并卸载」的通用能力。
        子类如需额外清理，可覆写并调用 super().on_stop()。
        """
        self._running = False
        # 回收常驻工具运行时（停止所有为其启动的 Worker 进程）
        if self._tool_runtime is not None:
            try:
                await self._tool_runtime.stop_all()
            except Exception:
                pass
            self._tool_runtime = None

    async def health_check(self) -> bool:
        """健康检查（子类可覆盖）"""
        return self._running


def _extract_fenced_json(md: str, tag: str):
    """从 markdown 中提取 ```<tag> ... ``` 代码块并解析为 JSON。

    支持 ```flow / ```server / ```apis 三种标记。解析失败返回 None。
    """
    import json as _json
    import re as _re

    # 匹配 ```tag 后到下一个 ``` 之间的内容（tag 大小写不敏感，允许尾随空白）
    pattern = _re.compile(r"```" + _re.escape(tag) + r"\s*\n(.*?)```", _re.DOTALL | _re.IGNORECASE)
    m = pattern.search(md)
    if not m:
        return None
    body = m.group(1).strip()
    if not body:
        return None
    try:
        return _json.loads(body)
    except Exception:
        # 容忍 YAML 风格的注释与单引号：尝试用 yaml 兜底解析
        try:
            import yaml as _yaml

            return _yaml.safe_load(body)
        except Exception:
            return None
