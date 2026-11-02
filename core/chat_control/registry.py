"""平台工具注册表 — 对话内可控能力的唯一清单。

设计要点（见 docs/chat-platform-control-design.md 第 4/6 节）：

- **白名单式注册**：LLM 只能调用本表里显式登记的工具。模型幻觉出的工具名
  在这里查不到 → 网关直接拒绝，不存在"模型自己发明能力"的可能。
- **Pydantic 严格校验**：每个工具绑定一个参数模型，`extra="forbid"` 让多余键
  立即报错；错误文本会原样回填给 LLM，让它自我修正而不是静默吞掉。
- **风险分级**：read（L0 只读直通）/ write（L1 确认卡）/ danger（L2 强确认）。
  风险级由**注册时静态声明**，LLM 无法通过参数降级。
- **描述即约束**：description 与字段 Field.description 会进入 tools schema，
  是给模型看的唯一说明书，务必写清边界（如"路径必须在允许目录内"）。

注册表本身不执行安全策略（确认/审计在 gateway），它只负责"有什么、参数长什么样"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, ValidationError

# ── 风险级常量 ──
RISK_READ = "read"      # L0：只读，直接执行
RISK_WRITE = "write"    # L1：写操作，确认卡片后执行
RISK_DANGER = "danger"  # L2：高危，强确认（勾选/复述）后执行

RISK_LEVELS = (RISK_READ, RISK_WRITE, RISK_DANGER)


@dataclass
class CallContext:
    """一次平台调用的上下文（由网关构造，处理器只读使用）。

    owner 是"这轮对话的登录用户"，处理器必须带着它去做可见性过滤，
    这样对话里的权限边界与手工操作完全一致。
    """

    owner: str
    session_id: str = "default"
    # 进度回调（生成类长任务用；P2 起使用，P0 允许为 None）
    emit: Callable[[dict], Awaitable[None]] | None = None

    async def progress(self, payload: dict) -> None:
        if self.emit is not None:
            await self.emit(payload)


@dataclass
class PlatformTool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[..., Awaitable[Any]]
    risk: str = RISK_READ
    # summary(args) -> 一句人话，用于确认卡标题；缺省用工具名+参数摘要
    summary_fn: Callable[[dict], str] | None = None
    # 确认卡里展示的参数视图；缺省展示全量参数
    args_view_fn: Callable[[dict], dict] | None = None
    # 该工具是否对 LLM 可见（熔断/灰度放开时用）
    enabled: bool = True


_REGISTRY: dict[str, PlatformTool] = {}


def platform_tool(
    name: str,
    description: str,
    args_model: type[BaseModel],
    risk: str = RISK_READ,
    summary_fn: Callable[[dict], str] | None = None,
    args_view_fn: Callable[[dict], dict] | None = None,
):
    """注册一个平台工具（装饰器）。

    重复注册同名工具直接抛错——清单冲突属于开发期错误，不能留到运行时。
    """
    if risk not in RISK_LEVELS:
        raise ValueError(f"未知风险级: {risk}")
    if name in _REGISTRY:
        raise ValueError(f"平台工具重复注册: {name}")

    def deco(fn):
        _REGISTRY[name] = PlatformTool(
            name=name, description=description, args_model=args_model,
            handler=fn, risk=risk, summary_fn=summary_fn, args_view_fn=args_view_fn,
        )
        return fn

    return deco


def get(name: str) -> PlatformTool | None:
    return _REGISTRY.get(name)


def names() -> list[str]:
    return sorted(_REGISTRY)


def is_known(name: str) -> bool:
    return name in _REGISTRY


def tool_names(risk: str | None = None) -> list[str]:
    return sorted(t.name for t in _REGISTRY.values()
                  if t.enabled and (risk is None or t.risk == risk))


def validate_args(tool: PlatformTool, args: Any) -> tuple[BaseModel | None, str]:
    """严格校验参数。返回 (模型实例, "") 或 (None, 错误文本)。

    错误文本面向 LLM：告诉它哪个字段出了问题，让它自我修正。
    """
    if not isinstance(args, dict):
        return None, f"参数必须是 JSON 对象，收到 {type(args).__name__}"
    try:
        return tool.args_model.model_validate(args), ""
    except ValidationError as e:  # pydantic v2
        parts = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err.get("loc", [])) or "<root>"
            parts.append(f"{loc}: {err.get('msg', 'invalid')}")
        return None, "参数校验失败 → " + "; ".join(parts)


def schemas(risk: str | None = None) -> list[dict]:
    """导出 OpenAI function-calling 格式的 tools 声明。"""
    out = []
    for t in _REGISTRY.values():
        if not t.enabled or (risk is not None and t.risk != risk):
            continue
        schema = t.args_model.model_json_schema()
        # 去掉顶层多余元数据，保持 schema 干净
        schema.pop("title", None)
        out.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": schema,
            },
        })
    return out


def summary_for(tool: PlatformTool, args: dict) -> str:
    if tool.summary_fn:
        try:
            return tool.summary_fn(args) or f"执行 {tool.name}"
        except Exception as e:  # 摘要生成失败不能阻塞确认流程
            return f"{tool.name}（摘要生成失败: {e}）"
    brief = ", ".join(f"{k}={_brief(v)}" for k, v in list(args.items())[:6])
    return f"{tool.name}({brief})" if brief else tool.name


def args_view_for(tool: PlatformTool, args: dict) -> dict:
    if tool.args_view_fn:
        try:
            return tool.args_view_fn(args)
        except Exception:
            pass
    return dict(args)


def _brief(v: Any, limit: int = 60) -> str:
    s = v if isinstance(v, str) else str(v)
    s = s.replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


class StrictArgs(BaseModel):
    """所有平台工具参数模型的基类：多余键一律拒绝。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
