"""领域异常 —— 让 ``core`` 层不依赖 FastAPI，同时保留 HTTP 语义。

## 为什么需要这么一层

授权与业务逻辑里有个绕不开的问题：**401 / 403 / 404 的区分本身就是业务语义**，
不是 HTTP 适配层能猜出来的。

- 「网络不存在」→ 404
- 「网络存在但你不是成员」→ **也是 404**（不能泄露存在性，契约 §4）
- 「你是成员但角色不允许」→ 403
- 「网络已归档」→ 409（暂时性冲突，不是权限不足）

如果在 ``core`` 里直接抛 ``HTTPException``，核心层就被 FastAPI 绑架了。所以定
一层带 ``status_code`` 的领域异常，在 ``app/main.py`` 用一个异常处理器统一转换：

```python
raise NotFoundError("网络不存在")      # core 层
        ↓ 全局处理器
HTTP 404 {"detail": "网络不存在"}      # HTTP 层
```
"""

from __future__ import annotations


class AppError(Exception):
    """带 HTTP 语义的领域异常基类。"""

    status_code: int = 500
    default_message: str = "服务器内部错误"

    def __init__(self, message: str | None = None, *, status_code: int | None = None) -> None:
        self.message = message or self.default_message
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)


class BadRequestError(AppError):
    """请求不合法（400）。"""

    status_code = 400
    default_message = "请求不合法"


class UnauthorizedError(AppError):
    """未认证 / 会话失效（401）。"""

    status_code = 401
    default_message = "未认证或会话已失效"


class ForbiddenError(AppError):
    """已认证但无权限（403）。

    ⚠️ 只在"调用者有权知道资源存在"时使用。若资源本身不该被看见，
    用 :class:`NotFoundError` —— 403 会确认资源存在。
    """

    status_code = 403
    default_message = "权限不足"


class NotFoundError(AppError):
    """不存在，**或存在但调用者无权知晓其存在**（404）。

    这两者刻意合并：区分它们就等于泄露存在性（IDOR 防护，契约 §4）。
    """

    status_code = 404
    default_message = "资源不存在"


class ConflictError(AppError):
    """并发冲突或状态不允许（409）。

    典型场景：网络已归档、角色调整的 version 不匹配、移除最后一位 Owner。
    前端应提示"刷新重试"，而不是"权限不足" —— 后者会让人去改角色。
    """

    status_code = 409
    default_message = "操作与当前状态冲突，请刷新后重试"


class GoneError(AppError):
    """资源曾经存在但已失效（410）。典型：邀请已过期/已使用。"""

    status_code = 410
    default_message = "资源已失效"


__all__ = [
    "AppError",
    "BadRequestError",
    "ConflictError",
    "ForbiddenError",
    "GoneError",
    "NotFoundError",
    "UnauthorizedError",
]
