"""SOTABand Engine — FastAPI 应用入口

系统启动时自动加载交互 Agent，等待用户输入。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
app.include_router(chat_router, prefix="/api/chat", tags=["chat"])
app.include_router(agent_router, prefix="/api/agent", tags=["agent"])
app.include_router(tool_router, prefix="/api/tool", tags=["tool"])
app.include_router(data_router, prefix="/api/data", tags=["data"])
app.include_router(file_router, prefix="/api/file", tags=["file"])
app.include_router(model_router, prefix="/api/model", tags=["model"])
app.include_router(config_router, prefix="/api/config", tags=["config"])
app.include_router(api_list_router, prefix="/api/apis", tags=["api"])
app.include_router(llm_router, prefix="/api/llm", tags=["llm"])
app.include_router(task_router, prefix="/api/task", tags=["task"])
app.include_router(migrate_router, prefix="/api/migrate", tags=["migrate"])

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
