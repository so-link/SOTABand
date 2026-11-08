"""资源写权限（D2 口径，2026-11-04 用户拍板）

## 口径

| 操作 | user-generated（有 owner） | builtin（无 owner） |
|---|---|---|
| 读 / 列出 / 执行 | 登录即可（共享资产） | 登录即可 |
| 改代码 / 规范 / 标签 / 参数 / 装依赖 / 删除 | **owner 或租户 Owner/Admin** | **仅租户 Owner/Admin** |

同一租户的普通成员之间**不允许**互相编辑对方的工具/Agent（用户明确要求）。
以后若要「团队协作编辑」，应像网络 `allowMemberPublish` 一样做策略开关，
而不是放开默认值。

## 为什么用路由级依赖而不是每个端点写一遍

工具/Agent 的写端点有十几个（save-code/save-spec/tags/modify-and-debug/…）。
逐个加判断容易漏、也容易漂移。这里用**一个依赖**按「HTTP 方法 + 路径」识别
写操作，在 `app/main.py` 的 include_router 处统一挂载：

- 只拦「真正修改资源」的动作（白名单），读/执行/调试运行不受影响；
- 资源 id 从路径取（``/api/tool/{tool_id}/save-code``）；
- 失败统一 **404**（不确认"存在但不属于你"）。

## 与「工具代码执行 = 服务器进程权限」的关系

本模块只解决"谁能改/删"，不解决"工具代码本身能做什么"——后者是架构性残余
风险（PR #9 的静态审查 + 部署 hardening 另行覆盖），已在评审回复中披露。
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.api.deps import get_auth_context
from core.auth.context import AuthContext

_PRIVILEGED_TENANT_ROLES = ("owner", "admin")

#: 需要「资源写权限」的 tool 动作（末段匹配）
_TOOL_WRITE_ACTIONS = {
    "save-code",
    "save-spec",
    "sync-spec-from-code",
    "tags",
    "param-required",
    "enforce-required",
    "update-table-cell",
    "modify-code",
    "modify-and-debug",
    "install-deps",
    # 精化规范（req.save=True 时写 registry + definitions/<id>.md）
    "refine-spec-node",
    "refine-spec-node-stream",
}

#: 需要「资源写权限」的 agent 动作
_AGENT_WRITE_ACTIONS = {
    "save-spec",
    "save-code",
    "sync-spec-from-code",
    "install-deps",
    # 运行控制：避免任何成员都能停/重启他人的 Agent（DoS）
    "start",
    "stop",
    "restart",
}

#: 顶层集合端点（不是具体资源，不能按 /{id}/action 解析）
_TOP_LEVEL_SEGMENTS = {
    "workspace",
    "register",
    "generate-spec",
    "generate-code",
    "test",
    "upload-test-file",
    "check-deps",
    "auto-debug",
    "repository",
    "search",
    "list",
    "test-api",
    "modify-code",
    "import-upload",
}


def can_write_resource(ctx: AuthContext | None, entry: dict | None) -> bool:
    """能否写/删该资源（D2 口径）。

    - 资源 owner 本人 → 允许；
    - 否则租户 Owner/Admin → 允许（兜底清理，含内置无 owner 资产）；
    - 其余（含未登录）→ 拒绝。
    """
    if ctx is None:
        return False
    owner = str((entry or {}).get("owner") or "")
    if owner and owner == str(ctx.user_id):
        return True
    return getattr(ctx, "tenant_role", None) in _PRIVILEGED_TENANT_ROLES


def require_resource_write(ctx: AuthContext | None, entry: dict | None, *, kind: str = "资源") -> None:
    """写权限校验；失败抛 404（不泄露存在性）。"""
    if entry is None:
        raise HTTPException(404, f"{kind}不存在")
    if not can_write_resource(ctx, entry):
        raise HTTPException(404, f"{kind}不存在")


def _resource_id(request: Request, *, prefix: str, actions: set[str]) -> str | None:
    """从路径解析需要做写权限校验的资源 id；不需要校验时返回 None。

    ⚠️ 不能只看「第一段是不是集合端点名」：工具/Agent 的 id 可能恰好叫
    ``test`` / ``list`` / ``register``（L1）。正确顺序是先看「动作段」是否命中
    写操作，再对 DELETE 做特殊处理（唯一例外：``workspace/{id}`` 是用户自己的
    工作区清单，不属于资源本体）。
    """
    path = request.url.path
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix):].strip("/")
    if not rest:
        return None
    segments = rest.split("/")
    first = segments[0]
    method = request.method.upper()

    if method in ("POST", "PUT", "PATCH") and len(segments) >= 2:
        if segments[1] in actions:
            return first
        return None
    if method == "DELETE":
        # ``workspace/{id}`` 是"从自己的工作区移除工具"，不属于资源本体。
        # ⚠️ 必须同时要求有第二段：``DELETE /api/tool/workspace`` 会被
        # ``DELETE /{tool_id}`` 匹配成「删除名为 workspace 的工具」——
        # 若只看 first == "workspace" 就跳过，等于给了一个删他人工具的绕过口子。
        if first == "workspace" and len(segments) >= 2:
            return None
        return first
    return None


async def require_tool_write_access(
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> None:
    """工具写操作守卫（路由级依赖）。"""
    tool_id = _resource_id(request, prefix="/api/tool/", actions=_TOOL_WRITE_ACTIONS)
    if tool_id is None:
        return
    from core.resource.registry.tool_registry import ToolRegistry

    entry = await ToolRegistry().get(tool_id)
    if entry is None:
        raise HTTPException(404, f"Tool '{tool_id}' not found")
    if not can_write_resource(ctx, entry):
        raise HTTPException(404, f"Tool '{tool_id}' not found")


async def require_agent_write_access(
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> None:
    """Agent 写操作守卫（路由级依赖）。"""
    agent_id = _resource_id(request, prefix="/api/agent/", actions=_AGENT_WRITE_ACTIONS)
    if agent_id is None:
        return
    from core.resource.registry.agent_registry import AgentRegistry

    entry = await AgentRegistry().get(agent_id)
    if entry is None:
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    if not can_write_resource(ctx, entry):
        raise HTTPException(404, f"Agent '{agent_id}' not found")


__all__ = [
    "can_write_resource",
    "require_agent_write_access",
    "require_resource_write",
    "require_tool_write_access",
]
