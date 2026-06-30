"""MAIA Engine — FastAPI 应用入口

系统启动时自动加载交互 Agent，等待用户输入。
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from app.api.routes.chat_routes import router as chat_router
from app.api.routes.agent_routes import router as agent_router
from app.api.routes.tool_routes import router as tool_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期 — 启动时初始化 Agent"""
    # 启动时：导入交互Agent（触发单例初始化）
    from app.api.routes.chat_routes import interactive_agent as _ia

    print(f"[MAIA] 交互Agent 已就绪 (id={_ia.agent_id})")
    print(f"[MAIA] LLM: {settings.llm.provider}/{settings.llm.model}")
    print(f"[MAIA] API: http://{settings.app.api_host}:{settings.app.api_port}")

    yield  # 应用运行中

    # 关闭时：清理
    print("[MAIA] 服务关闭")


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


@app.get("/")
async def root():
    return {"name": settings.app.app_name, "version": "0.1.0", "status": "running"}


@app.get("/api/health")
async def health():
    return {"status": "healthy"}
