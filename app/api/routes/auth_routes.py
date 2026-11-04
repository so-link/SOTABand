"""认证与用户管理路由

## 谁能做什么

- ``POST /login``            —— 任何人（超管凭据来自 .env，普通用户来自用户表）
- ``GET  /me``               —— 当前身份；未登录返回 authenticated=false
- ``POST /logout``           —— 无状态令牌，客户端丢弃即可
- ``POST /password``         —— **改自己的密码**（需带原密码；超管不可，见下）
- ``PUT  /me``               —— 改自己的显示名
- ``GET  /prefs`` ``PUT /prefs`` —— 个人偏好，存自己的 platform.db
- ``GET/POST/PUT/DELETE /users`` —— **仅超级用户**：增删改用户、重置密码

超级用户的用户名/密码只在 .env 配置（``SOTABAND_ROOT_USERNAME`` /
``SOTABAND_ROOT_PASSWORD``），界面上只读——若在界面上也能改，就会出现
「界面改了、下次重启被 .env 覆盖回去」的双头写，比不能改更糟。

普通用户的 id 由系统分配（1000 起）且不可修改：它就是用户目录名
（``SB-USER/<uid>/``），改 id 等于搬走整个数据目录。

安全：密码字段永不出库（core.auth 的公开函数一律剥离 password）；所有
/users 与 /users/{id}/password 端点经 require_superuser 保护，而 /password
与 /prefs 走 require_login —— 用户只能动自己的。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import require_login, require_superuser
from core import user_prefs
from core.auth import (
    AuthError,
    authenticate,
    change_own_password,
    create_user,
    delete_user,
    get_user_by_id,
    issue_token,
    list_users,
    set_display_name,
    set_password,
    rename_user,
    superuser,
    superuser_password_configured,
)
from core.auth.session import AuthUser, get_current_user

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    oldPassword: str
    newPassword: str


class ProfileUpdate(BaseModel):
    displayName: str


class UserCreate(BaseModel):
    username: str
    password: str
    displayName: str = ""


class UserRename(BaseModel):
    username: str


class UserDisplayName(BaseModel):
    displayName: str


class AdminPasswordReset(BaseModel):
    password: str


def _error(e: AuthError):
    raise HTTPException(status_code=e.status, detail=e.message)


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
        return {
            "authenticated": False,
            "identity": "anonymous",
            # 前端据此决定登录框提示：没配超管密码时不该让人瞎猜 root
            "superuser_configured": superuser_password_configured(),
        }
    full = get_user_by_id(u.id) or {}
    return {
        "authenticated": True,
        "user": {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "displayName": full.get("display_name") or u.username,
            "builtin": bool(full.get("builtin")) or u.id == "0",
        },
    }


@router.post("/logout")
async def logout():
    # 无状态令牌：登出即客户端丢弃令牌。保留端点供前端统一调用与后续审计扩展。
    return {"ok": True}


@router.put("/me")
async def update_me(req: ProfileUpdate, me_user: AuthUser = Depends(require_login)):
    try:
        user = set_display_name(me_user.id, req.displayName)
    except AuthError as e:
        _error(e)
    return {"ok": True, "user": user}


@router.post("/password")
async def change_password(req: PasswordChange, me_user: AuthUser = Depends(require_login)):
    try:
        change_own_password(me_user.id, req.oldPassword, req.newPassword)
    except AuthError as e:
        _error(e)
    return {"ok": True}


# ── 个人偏好 ──

@router.get("/prefs")
async def get_prefs(_: AuthUser = Depends(require_login)):
    return {"prefs": user_prefs.get_all()}


@router.put("/prefs")
async def put_prefs(prefs: dict, _: AuthUser = Depends(require_login)):
    try:
        return user_prefs.set_many(prefs)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── 用户管理（仅超级用户） ──

@router.get("/users")
async def users(_: AuthUser = Depends(require_superuser)):
    return {"users": list_users()}


@router.post("/users")
async def add_user(req: UserCreate, _: AuthUser = Depends(require_superuser)):
    try:
        user = create_user(req.username, req.password, req.displayName)
    except AuthError as e:
        _error(e)
    return {"ok": True, "user": user}


@router.put("/users/{uid}")
async def modify_user(uid: str, req: UserRename, _: AuthUser = Depends(require_superuser)):
    try:
        user = rename_user(uid, req.username)
    except AuthError as e:
        _error(e)
    return {"ok": True, "user": user}


@router.put("/users/{uid}/display-name")
async def modify_display_name(
    uid: str, req: UserDisplayName, _: AuthUser = Depends(require_superuser)
):
    try:
        user = set_display_name(uid, req.displayName)
    except AuthError as e:
        _error(e)
    return {"ok": True, "user": user}


@router.put("/users/{uid}/password")
async def reset_password(
    uid: str, req: AdminPasswordReset, _: AuthUser = Depends(require_superuser)
):
    try:
        set_password(uid, req.password)
    except AuthError as e:
        _error(e)
    return {"ok": True}


@router.delete("/users/{uid}")
async def remove_user(uid: str, _: AuthUser = Depends(require_superuser)):
    try:
        result = delete_user(uid)
    except AuthError as e:
        _error(e)
    return {"ok": True, **result}


@router.get("/superuser")
async def superuser_info(_: AuthUser = Depends(require_superuser)):
    """超管自身信息（供用户管理页展示"内置账号，改动请在 .env"）。

    password_configured=false 表示当前是单用户模式：匿名即以超管身份使用，
    没有密码可登录——界面据此提示"设置密码后才会启用多用户"。
    """
    return {"superuser": superuser(), "password_configured": superuser_password_configured()}
