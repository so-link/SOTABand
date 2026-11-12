"""路由层鉴权依赖

三类接口对应三种要求（多用户系统 2026-11）：

- ``require_login``：必须是已登录用户。中间件已经把身份放进 contextvar，
  这里只是把它取出来并挡掉匿名——普通用户和超管都算通过。
- ``require_superuser``：只有超级用户（.env 里那个）可以管理用户、改系统配置。
- 数据隔离**不靠这些依赖**：对话/任务/私有资源的存在位置就是
  ``<USER_HOME>/<uid>/``，各存储层按当前身份选库，天然拿不到别人的数据。

不用 FastAPI 的 ``Depends(HTTPException)`` 包一层是为了让错误信息与
main.py 中间件的 401 语义完全一致（"该操作需要登录"），前端只需认一种文案。
"""

from __future__ import annotations

from fastapi import HTTPException

from core.auth.session import AuthUser, get_current_user


def require_login() -> AuthUser:
    user = get_current_user()
    if user is None:
        raise HTTPException(status_code=401, detail="该操作需要登录")
    return user


def require_superuser() -> AuthUser:
    user = require_login()
    from core.auth import is_superuser_uid

    # 唯一 admin = .env 超管（uid=0）。不看令牌/表里的 role：老库首用户
    # role=admin 的历史遗留与旧令牌里烙下的 role 都不再是权限依据。
    if not is_superuser_uid(user.id):
        raise HTTPException(status_code=403, detail="仅超级用户可以执行该操作")
    return user


def current_role() -> str:
    """当前请求角色；匿名（单用户模式已在中间件注入超管）按 admin 处理。

    唯一 admin = .env 超管（uid=0）：按 uid 判定，不回用户表查 role——
    表内遗留的 role=admin（老库首用户）不再授予任何权限。
    """
    from core.auth import is_superuser_uid

    u = get_current_user()
    if u is None:
        return "admin"
    return "admin" if is_superuser_uid(u.id) else "user"


def is_system_identity() -> bool:
    """当前请求是否以系统用户（uid=100）身份执行。

    系统身份只能经 ``POST /api/auth/act-as-system`` 领取的短时令牌获得
    （超管专属入口）。uid=0 超管令牌本身**不算**系统身份——写公共层的
    唯一通道是 act-as 换发的 uid=100 令牌，这样公共层写入在审计里始终以
    系统用户署名，超管本人也不例外。
    """
    from core.auth import is_system_uid

    u = get_current_user()
    return u is not None and is_system_uid(u.id)


def ensure_can_touch_builtin(layer_hit: str | None, *, action: str = "修改") -> None:
    """公共层资源的写/删闸门：必须系统身份（act-as 令牌）才能修改或删除。

    ``layer_hit`` 传 registry 合并视图命中层（条目 ``source`` 派生字段）：
    - ``"private"``：个人定制版，任何登录用户都可以改自己的副本，直接放行；
    - ``"system"``（及旧名 ``"builtin"``/``"shared"``、未标注）：公共层版本，
      非系统身份一律 403——超管令牌也不例外（严格闸门，2026-11 定）；
      单用户模式匿名即 uid=0，同样需要先 act-as。

    普通用户的出路是「复制为我的副本」——fork 端点写的是私有根，不受本
    闸门限制。超管代管公共层的正道是 act-as-system 领 1 小时令牌。
    ``action`` 只影响提示文案（修改/删除）。
    """
    if layer_hit == "private":
        return
    if is_system_identity():
        return
    raise HTTPException(
        status_code=403,
        detail=f"公共资源仅系统身份可以{action}；超级用户请在「用户管理」切换到系统身份"
               "（或调用 POST /api/auth/act-as-system），普通用户可以「复制为我的副本」后修改自己的版本",
    )
