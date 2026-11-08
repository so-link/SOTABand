"""``AuthContext`` —— 服务端生成的唯一可信请求主体上下文。

冻结契约见 ``docs/contract-08-auth-network.md`` §1.1。

## 为什么需要一个专门的类型

在这之前，``user_id`` 由客户端在请求体里提交（``chat_routes.py:104``），任何人
改一个字段就能以他人身份操作。``AuthContext`` 的作用是**把身份断言从客户端
声明变成服务端证据**：它只能由认证中间件从会话表构造，业务代码拿到的必须是
已经验证过的值。

## 下游怎么用

```python
@router.get("/api/v1/me")
async def me(ctx: AuthContext = Depends(get_auth_context)):
    return ctx.user_id      # ✅ 唯一可信来源
    # request.query_params["user_id"]   # ❌ 禁止
```

**网络角色不在这个类型里** —— 一个用户在 N1 是 admin、在 N2 是 member，
网络角色是"针对某个 network_id"的量，必须按需解析
（``core/auth/authorize.py::resolve_network_role``，Step 4 落地）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuthContext:
    """认证中间件生成的**唯一可信**请求主体。

    冻结字段（变更是破坏性变更，需走新 ADR）：

    - ``user_id`` / ``session_id`` / ``tenant_id`` / ``tenant_role`` / ``request_id``

    身份类型（Phase 5 启用）：

    - ``actor_kind``：``user`` / ``workload``。Agent 间通信使用独立的工作负载
      身份，**绝不复用用户会话 Cookie**（PRD FR-N7）。
    """

    #: ``user.id``（UUID 字符串）
    user_id: str
    #: ``session.id``（UUID 字符串）
    session_id: str
    #: 当前激活租户 ``tenant.id``
    tenant_id: str
    #: 该用户在该租户中的角色：owner / admin / member / viewer
    tenant_role: str
    #: 贯穿审计与日志的请求 id
    request_id: str

    #: 展示用，可为空
    email: str | None = None
    display_name: str | None = None

    #: 主体类型（Phase 5）
    actor_kind: str = "user"
    #: ``actor_kind == "workload"`` 时的安装实例 id
    workload_id: str | None = None
    #: 预留扩展位（如后续加入的 scope / permissions 快照）
    extra: dict = field(default_factory=dict)

    @property
    def is_workload(self) -> bool:
        return self.actor_kind == "workload"
