"""SOTABand Engine — FastAPI 应用入口

系统启动时自动加载交互 Agent，等待用户输入。
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from app.api.routes.chat_routes import router as chat_router
from app.api.routes.agent_routes import router as agent_router
from app.api.routes.tool_routes import router as tool_router
from app.api.routes.data_routes import router as data_router
from app.api.routes.file_routes import router as file_router
from app.api.routes.model_routes import router as model_router
from app.api.routes.config_routes import router as config_router
from app.api.routes.api_routes import router as api_list_router
from app.api.routes.llm_routes import router as llm_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期 — 启动时拉起系统默认交互 Agent（独立进程）"""
    from core.agent.factory import agent_factory
    from app.api.routes.chat_routes import (
        ensure_default_interactive_agent,
        DEFAULT_INTERACTIVE_AGENT_ID,
    )

    # 启动系统默认交互 Agent：它是系统默认入口，随引擎自动拉起。
    # 注意：它同样支持被用户手动关闭 / 重启（见 agent_routes 的 start/stop）。
    result = await ensure_default_interactive_agent()
    status = result.get("status", "unknown")
    if status in {"started", "already_running"}:
        print(f"[SOTABand] 交互Agent 已就绪 (id={DEFAULT_INTERACTIVE_AGENT_ID}, {status})")
    else:
        print(f"[SOTABand] 交互Agent 启动失败: {result.get('error', status)}")
    print(f"[SOTABand] LLM: {settings.llm.provider}/{settings.llm.model}")
    print(f"[SOTABand] API: http://{settings.app.api_host}:{settings.app.api_port}")

    yield  # 应用运行中

    # 关闭时：停止所有 Agent 子进程
    for agent_id in list(getattr(agent_factory, "_processes", {}).keys()):
        try:
            await agent_factory.stop(agent_id)
        except Exception:
            pass
    print("[SOTABand] 服务关闭")


app = FastAPI(
    title=settings.app.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.app.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.get("/")
async def root():
    return {"name": settings.app.app_name, "version": "0.1.0", "status": "running"}


@app.get("/api/health")
async def health():
    return {"status": "healthy"}
