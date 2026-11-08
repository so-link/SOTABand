"""SOTABand Engine — FastAPI 应用入口

系统启动时自动加载交互 Agent，等待用户输入。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import get_auth_context
from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes.agent_routes import router as agent_router
from app.api.routes.api_routes import router as api_list_router

# 需求 8.1：认证与会话
from app.api.routes.auth_routes import router as auth_router
from app.api.routes.chat_routes import router as chat_router
from app.api.routes.config_routes import router as config_router
from app.api.routes.data_routes import router as data_router
from app.api.routes.file_routes import router as file_router
from app.api.routes.llm_routes import router as llm_router
from app.api.routes.device_routes import router as device_router
from app.api.routes.me_routes import router as me_router
from app.api.routes.migrate_routes import router as migrate_router
from app.api.routes.model_routes import router as model_router
from app.api.routes.task_routes import router as task_router

# 需求 8.2：虚拟协作网络
from app.api.routes.network_routes import router as network_router

# Step 5：资源发布、网络目录、安装（需求 8.2 共享侧）
from app.api.routes.sharing_routes import router as sharing_router

# Step 3：租户创建/邀请/加入（8.2 的前置）
from app.api.routes.tenant_routes import router as tenant_router
from app.api.routes.tool_routes import router as tool_router
from config.settings import settings
from core.errors import AppError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期 — 启动时拉起系统默认交互 Agent（独立进程）"""
    from app.api.routes.chat_routes import (
        DEFAULT_INTERACTIVE_AGENT_ID,
        ensure_default_interactive_agent,
    )
    from core.agent.factory import agent_factory

    # 启动系统默认交互 Agent：它是系统默认入口，随引擎自动拉起。
    # 注意：它同样支持被用户手动关闭 / 重启（见 agent_routes 的 start/stop）。
    result = await ensure_default_interactive_agent()
    status = result.get("status", "unknown")
    print(f"[SOTABand] 交互Agent 已就绪 (id={DEFAULT_INTERACTIVE_AGENT_ID}, {status})")
    print(f"[SOTABand] LLM: {settings.llm.provider}/{settings.llm.model}")
    print(f"[SOTABand] API: http://{settings.app.api_host}:{settings.app.api_port}")

    yield  # 应用运行中

    # 关闭时：清理
    from core.scheduler.monitor import close_device_monitor

    await close_device_monitor()
    # 关闭时：停止所有 Agent 子进程
    for agent_id in list(getattr(agent_factory, "_processes", {}).keys()):
        try:
            await agent_factory.stop(agent_id)
        except Exception:
            # 关停失败不能让剩余清理中断，但**必须留日志** ——
            # 静默 pass 会让"关停后子进程还活着"这种问题永远查不到原因
            logger.warning("停止 Agent 子进程失败 agent_id=%s", agent_id, exc_info=True)
    # 关闭数据库连接池
    try:
        from core.db.session import dispose_engine

        await dispose_engine()
    except Exception:
        logger.warning("关闭数据库连接池失败", exc_info=True)
    print("[SOTABand] 服务关闭")


app = FastAPI(
    title=settings.app.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — 允许前端跨域。
# ⚠️ origins 必须是显式列表（settings.app.cors_origins），不能是 `*`：
#    浏览器规范下 `Access-Control-Allow-Origin: *` 与 allow_credentials 不能共存，
#    通配符会让 Cookie 会话静默失败（ADR-08-05）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.app.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 请求上下文（分配 request_id 贯穿审计与日志）。
# 用纯 ASGI 实现而非 BaseHTTPMiddleware —— 后者会干扰 SSE 流式响应。
app.add_middleware(RequestContextMiddleware)


@app.exception_handler(AppError)
async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """把 ``core`` 层的领域异常统一转成 HTTP 响应。

    这样 ``core/`` 不需要 import FastAPI，同时 404/403/409 的业务语义
    （尤其是「不存在 vs 不可见」的区分）仍然准确落到 HTTP 状态码上。

    响应体固定 ``{"detail": ...}``，与 FastAPI ``HTTPException`` 的形状一致，
    前端可以用同一个解析逻辑。
    """
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

# 注册路由
#
# ── 鉴权基线（D1，2026-11-04 决策）──────────────────────────────────
# 业务 API 一律要求登录：除「认证生命周期」与「健康检查」外，全部路由在
# include_router 处统一挂 ``get_auth_context`` 依赖。
#
# 为什么放在 include_router 而不是中间件：
# - 依赖注入是 FastAPI 原生语义，路由内再声明 ctx 会命中同一请求内依赖缓存，
#   不会重复查库；
# - 公开端点白名单显式可见（就是下面不带 _auth 的那几行），评审时一眼能核对。
#
# 历史背景：8.1 之前引擎是单机无登录形态，工具/数据/文件等接口全部匿名可用。
# 多用户部署（一台服务器多人共用）下这是严重越权面，故统一收口。
_auth = [Depends(get_auth_context)]

#: 资源写权限守卫（D2）：工具/Agent 的改代码/改规范/标签/装依赖/删除
#: 需要 owner 或租户 Owner/Admin；内置资产仅租户管理员。读/执行不受影响。
from core.auth.ownership import (  # noqa: E402 - 需在 app 创建后集中注册依赖
    require_agent_write_access,
    require_tool_write_access,
)

_tool_deps = [Depends(get_auth_context), Depends(require_tool_write_access)]
_agent_deps = [Depends(get_auth_context), Depends(require_agent_write_access)]

app.include_router(device_router, prefix="/api/devices", tags=["device"], dependencies=_auth)
app.include_router(chat_router, prefix="/api/chat", tags=["chat"], dependencies=_auth)
app.include_router(agent_router, prefix="/api/agent", tags=["agent"], dependencies=_agent_deps)
app.include_router(tool_router, prefix="/api/tool", tags=["tool"], dependencies=_tool_deps)
app.include_router(data_router, prefix="/api/data", tags=["data"], dependencies=_auth)
app.include_router(file_router, prefix="/api/file", tags=["file"], dependencies=_auth)
app.include_router(model_router, prefix="/api/model", tags=["model"], dependencies=_auth)
app.include_router(config_router, prefix="/api/config", tags=["config"], dependencies=_auth)
app.include_router(api_list_router, prefix="/api/apis", tags=["api"], dependencies=_auth)
app.include_router(llm_router, prefix="/api/llm", tags=["llm"], dependencies=_auth)
app.include_router(task_router, prefix="/api/task", tags=["task"], dependencies=_auth)
app.include_router(migrate_router, prefix="/api/migrate", tags=["migrate"], dependencies=_auth)

# 需求 8.1：认证与会话。
# 认证接口挂在 /api/auth/* 而不是 PRD 的 /auth/* —— 只有 /api 前缀走 Vite 代理，
# 换成 /auth 会变成跨域请求，Cookie 静默失效（ADR-08-05）。
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(me_router, prefix="/api/v1", tags=["me"])
# 需求 8.2：网络。与 /api/v1/me 同一前缀，按契约 §3.2 的路径组织。
app.include_router(network_router, prefix="/api/v1", tags=["network"])
# Step 3：租户。8.2 的多用户流程依赖"加入他人租户 + 切换激活租户"。
app.include_router(tenant_router, prefix="/api/v1", tags=["tenant"])
# Step 5：共享。注意带有 `{resource_type}-releases` 路径段，与上面几个 v1 路由
# 不冲突（`agent-releases` / `tool-releases` / `data-releases` 各自独立）。
app.include_router(sharing_router, prefix="/api/v1", tags=["sharing"])


@app.get("/")
async def root():
    return {"name": settings.app.app_name, "version": "0.1.0", "status": "running"}


@app.get("/api/health")
async def health():
    return {"status": "healthy"}
