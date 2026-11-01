"""SOTABand Engine — FastAPI 应用入口

系统启动时自动加载交互 Agent，等待用户输入。
"""

from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
from app.api.routes.auth_routes import router as auth_router
from core.auth import list_users, verify_token
from core.auth.session import AuthUser, reset_current_user, set_current_user


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
    print(f"[SOTABand] 交互Agent 已就绪 (id={DEFAULT_INTERACTIVE_AGENT_ID}, {status})")
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

# CORS — 默认仅同源；跨域来源经 SOTABAND_CORS_ORIGINS 显式配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.app.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
# 读类接口里涉及密钥/配置/文件读写的路径；其余 GET 仍按只读放行
_READ_SENSITIVE_PREFIXES = ("/api/file", "/api/config", "/api/llm", "/api/apis")


def _is_loopback_host(host: str | None) -> bool:
    return (host or "").strip().lower() in _LOOPBACK_HOSTS


def _origin_hostname(origin: str) -> str:
    try:
        return (urlsplit(origin).hostname or "").lower()
    except ValueError:
        return ""


def _has_users() -> bool:
    try:
        return bool(list_users())
    except Exception:
        return False


def _forwarded_hostname(request: Request) -> str:
    """经本机反向代理（Vite dev server 等）转发时，返回浏览器侧的原始 Host。

    仅在直连方为本机回环时才信任该头——远端请求无法借此伪装来源。
    """
    client_host = request.client.host if request.client else ""
    if not _is_loopback_host(client_host):
        return ""
    xfh = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    if not xfh:
        return ""
    return _origin_hostname(f"http://{xfh}")


def _is_sensitive_endpoint(path: str, method: str) -> bool:
    """写/执行类接口一律敏感；读类仅文件/配置/密钥相关。"""
    if not path.startswith("/api/"):
        return False
    if path.startswith("/api/auth/") or path == "/api/health":
        return False
    if method in ("GET", "HEAD", "OPTIONS"):
        return path.startswith(_READ_SENSITIVE_PREFIXES)
    return True


def _browser_guard(request: Request, method: str):
    """浏览器侧攻击防护（与是否登录无关）。

    - 本机监听时拒绝非回环 Host：DNS rebinding 会把攻击者域名的请求打到
      127.0.0.1，但 Host/Origin 里仍是攻击者域名。
    - 状态变更请求校验 Origin：CORS 只挡「读响应」，挡不住简单请求送达。
    """
    if _is_loopback_host(settings.app.api_host) and not _is_loopback_host(request.url.hostname):
        return JSONResponse(status_code=403, content={"detail": "仅接受本机 Host 的请求"})

    if method not in ("GET", "HEAD", "OPTIONS"):
        origin = (request.headers.get("origin") or "").strip()
        if origin:
            if origin.lower() == "null":
                return JSONResponse(status_code=403, content={"detail": "不允许的来源"})
            origin_host = _origin_hostname(origin)
            same_host = origin_host == (request.url.hostname or "").lower()
            forwarded_host = _forwarded_hostname(request)
            if not (
                same_host
                or _is_loopback_host(origin_host)
                or (forwarded_host and origin_host == forwarded_host)
                or origin in settings.app.cors_origins
            ):
                return JSONResponse(status_code=403, content={"detail": "跨站来源被拒绝"})
    return None


@app.middleware("http")
async def auth_identity_middleware(request: Request, call_next):
    """解析 Bearer 令牌写入请求级身份，并做请求防护。

    - 携带了**无效/过期**令牌则直接 401——静默降级成匿名会让过期会话
      以实例身份写数据，归属反而更难追踪。
    - 已注册用户的实例：写/执行类接口必须登录。
    - 无用户的实例：写/执行类接口只允许本机回环客户端（远端一律拒绝）。
    """
    path = request.url.path
    method = request.method.upper()
    header = request.headers.get("authorization", "")
    payload = None

    if header.lower().startswith("bearer "):
        payload = verify_token(header[7:].strip())
        if payload is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "登录凭证无效或已过期，请重新登录"},
            )

    guard = _browser_guard(request, method)
    if guard is not None:
        return guard

    if payload is None and _is_sensitive_endpoint(path, method):
        if _has_users():
            return JSONResponse(status_code=401, content={"detail": "该操作需要登录"})
        client_host = request.client.host if request.client else ""
        if not _is_loopback_host(client_host):
            return JSONResponse(status_code=403, content={"detail": "未登录时仅允许本机访问"})

    if payload is None:
        return await call_next(request)

    token = set_current_user(AuthUser(
        id=payload["uid"], username=payload.get("username", ""),
        role=payload.get("role", "user"),
    ))
    try:
        return await call_next(request)
    finally:
        reset_current_user(token)


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
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])


@app.get("/")
async def root():
    return {"name": settings.app.app_name, "version": "0.1.0", "status": "running"}


@app.get("/api/health")
async def health():
    return {"status": "healthy"}
