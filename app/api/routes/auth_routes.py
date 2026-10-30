"""认证路由：注册 / 登录 / 当前身份 / 登出 / 用户列表

身份语义：
- 登录后返回签名令牌，前端以 ``Authorization: Bearer <token>`` 携带；
  中间件（app/main.py）解析后写入请求级上下文，资源归属随登录用户变化。
- 未登录请求仍回退实例身份（单用户本地使用不破坏）。
- 首个注册用户自动成为 admin；/users 仅 admin 可见（密码字段永不出库）。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.auth import (
    AuthError,
    authenticate,
    issue_token,
    list_users,
    register_user,
)
from core.auth.session import get_current_user

router = APIRouter()


class RegisterRequest(BaseModel):
    username: str
    password: str
    displayName: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


def _error(e: AuthError):
    raise HTTPException(status_code=e.status, detail=e.message)


@router.post("/register")
async def register(req: RegisterRequest):
    """注册新用户。当前实例已有用户时，仅 admin 可再注册（本地多人共享实例场景）。"""
    try:
        me = get_current_user()
        if list_users() and (me is None or not me.is_admin):
            raise AuthError("实例已有用户，仅管理员可注册新用户", 403)
        user = register_user(req.username, req.password, req.displayName)
    except AuthError as e:
        _error(e)
    return {"ok": True, "user": user}


@router.post("/login")
async def login(req: LoginRequest):
    user = authenticate(req.username, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = issue_token(user)
    return {"ok": True, "token": token, "user": user}


@router.get("/me")
async def me():
    u = get_current_user()
    if u is None:
        return {"authenticated": False, "identity": "instance"}
    return {"authenticated": True, "user": {"id": u.id, "username": u.username, "role": u.role}}


@router.post("/logout")
async def logout():
    # 无状态令牌：登出即客户端丢弃令牌。保留端点供前端统一调用与后续审计扩展。
    return {"ok": True}


@router.get("/users")
async def users():
    me = get_current_user()
    if me is None or not me.is_admin:
        raise HTTPException(status_code=403, detail="仅管理员可查看用户列表")
    return {"users": list_users()}
