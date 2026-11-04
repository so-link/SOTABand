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
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="仅超级用户可以执行该操作")
    return user
