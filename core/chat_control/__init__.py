"""对话内控制系统（Chat Platform Control）。

对外只暴露网关函数；处理器与注册表细节不对外使用：

    from core.chat_control import platform_call, confirm_op, tool_schemas

设计文档：docs/chat-platform-control-design.md
"""

from core.chat_control import handlers as _handlers  # noqa: F401  导入即注册平台工具
from core.chat_control.gateway import (
    cancel_op,
    confirm_op,
    control_enabled,
    pending_op,
    platform_call,
    tool_schemas,
)

__all__ = [
    "platform_call", "confirm_op", "cancel_op", "pending_op",
    "tool_schemas", "control_enabled", "init",
]


def init() -> None:
    """建审计表（引擎启动时调用）。"""
    from core.chat_control.gateway import init as _gw_init
    _gw_init()
