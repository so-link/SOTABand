"""SOTABand Engine — FastAPI 应用入口

系统启动时自动加载交互 Agent，等待用户输入。
"""

import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
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
from app.api.routes.task_routes import router as task_router
from app.api.routes.cron_routes import router as cron_router
from core.auth import (
    SUPERUSER_UID,
    superuser,
    superuser_password_configured,
    verify_token,
)
from core.auth.session import AuthUser, reset_current_user, set_current_user
from app.api.deps import require_superuser

# 单用户兼容模式下的匿名身份 = 超级用户（uid=0）。
# 显式注入而不是"不设置身份"，是为了让 require_superuser、资源 owner、
# 用户目录解析三条链路读到同一个值——否则各处会各自回退，行为难以推断。
ANONYMOUS_SUPERUSER = AuthUser(
    id=SUPERUSER_UID,
    username=os.getenv("SOTABAND_ROOT_USERNAME", "").strip() or "root",
    role="admin",
)


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

    # 身份层模式：影响是否需要登录才能用，必须在启动日志里说清楚。
    # 判据是"是否配置了超管密码"而不是 superuser()——后者在单用户模式下
    # 同样返回身份（用户管理页需要展示超管那一行），用它判断模式会永远为真。
    if superuser_password_configured():
        su = superuser()
        print(f"[SOTABand] 多用户模式：已启用（超级用户 {su['username']} uid={su['id']}，凭据在 .env）")
    else:
        print(
            f"[SOTABand] 单用户模式：未配置 SOTABAND_ROOT_PASSWORD，"
            f"匿名访问即以超级用户（uid={SUPERUSER_UID}）身份使用。"
            f"配置该密码并重启即可启用多用户（强制登录 + 用户管理）"
        )

    # 定时任务模块：建表 + 载入启用任务开始调度（失败不阻塞主服务启动）
    try:
        from core.cron import cron_scheduler
        from core.cron import store as cron_store
        cron_store.init()
        await cron_scheduler.start()
        print("[SOTABand] 定时任务调度器已启动")
    except Exception as exc:
        print(f"[SOTABand] 定时任务调度器启动失败（不影响其它功能）: {exc}")

    # 对话内控制系统：建审计表 chat_ops（失败不阻塞主服务启动）
    try:
        from core.chat_control import init as chat_control_init
        chat_control_init()
        print("[SOTABand] 平台控制网关已就绪")
    except Exception as exc:
        print(f"[SOTABand] 平台控制网关初始化失败（不影响其它功能）: {exc}")

    yield  # 应用运行中

    # 关闭时：先停调度器（不再触发新任务），再停止所有 Agent 子进程
    try:
        from core.cron import cron_scheduler
        await cron_scheduler.shutdown()
    except Exception:
        pass
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


def _is_loopback_host(host: str | None) -> bool:
    return (host or "").strip().lower() in _LOOPBACK_HOSTS


def _origin_hostname(origin: str) -> str:
    try:
        return (urlsplit(origin).hostname or "").lower()
    except ValueError:
        return ""


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


# 匿名可访问的端点：登录入口与探活。
_ANONYMOUS_PATHS = ("/api/health",)
_ANONYMOUS_PREFIXES = ("/api/auth/login", "/api/auth/me", "/api/auth/logout")


def _is_anonymous_allowed(path: str) -> bool:
    if path in _ANONYMOUS_PATHS or path == "/":
        return True
    return any(path.startswith(p) for p in _ANONYMOUS_PREFIXES)


def _multiuser_enabled() -> bool:
    """多用户是否启用 = 是否在 .env 配置了超级用户密码。

    未配置 = **单用户兼容模式**：匿名请求直接以超级用户（uid=0）身份执行，
    行为与多用户上线前完全一致（本机打开就能用，不必先登录）。
    配置了密码 = 强制登录：除登录/探活外一律要求携带有效令牌。
    """
    return superuser_password_configured()


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

    两种模式（由 .env 是否配置 SOTABAND_ROOT_PASSWORD 决定，见 _multiuser_enabled）：

    - **单用户兼容模式**（未配置密码）：匿名请求直接注入超级用户身份放行，
      与多用户上线前的行为一致——本机打开就用，不必先登录。
    - **多用户模式**（已配置密码）：除登录/探活外一律要求携带有效令牌。
      否则等于把 uid=0（超级用户）的全部数据开放给任何未登录访客。

    携带了**无效/过期**令牌一律直接 401：静默降级成匿名会让过期会话继续写数据，
    在单用户模式下还会把一个失效的普通用户会话悄悄提权成超管。
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

    if payload is None:
        if _multiuser_enabled():
            # 非 /api 路径（静态资源、根路径）不受影响；/api 下只放行登录相关
            if path.startswith("/api/") and not _is_anonymous_allowed(path):
                return JSONResponse(status_code=401, content={"detail": "该操作需要登录"})
        else:
            # 单用户模式：匿名即以超管身份执行（require_superuser 也据此放行）
            token = set_current_user(ANONYMOUS_SUPERUSER)
            try:
                return await call_next(request)
            finally:
                reset_current_user(token)
    else:
        token = set_current_user(AuthUser(
            id=payload["uid"], username=payload.get("username", ""),
            role=payload.get("role", "user"),
        ))
        try:
            return await call_next(request)
        finally:
            reset_current_user(token)

    return await call_next(request)


# 注册路由
# 系统级配置路由整体限超管：config 直接读写 .env（LLM Key、超时等），
# llm 回显 Key 脱敏值与服务商清单——这些是平台资产，普通用户看不到也不该猜。
_system_only = [Depends(require_superuser)]
app.include_router(config_router, prefix="/api/config", tags=["config"], dependencies=_system_only)
app.include_router(llm_router, prefix="/api/llm", tags=["llm"], dependencies=_system_only)

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
app.include_router(task_router, prefix="/api/task", tags=["task"])
app.include_router(cron_router, prefix="/api/cron", tags=["cron"])


@app.get("/")
async def root():
    return {"name": settings.app.app_name, "version": "0.1.0", "status": "running"}


@app.get("/api/health")
async def health():
    return {"status": "healthy"}
