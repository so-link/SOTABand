"""子进程启动辅助 — 统一处理 Windows 控制台窗口问题。

背景：
    Windows 上启动"控制台程序"子进程时，如果父进程没有可附着的控制台
    （例如后端从 GUI / IDE / pythonw 启动），Windows 会为该子进程**新建一个
    控制台**，于是屏幕上弹出一个黑色命令行窗口。
    对"常驻"进程（工具常驻 Worker、Agent 子进程）来说，这个窗口会一直挂着，
    还会被用户误当成"卡死"而手动关闭（关掉窗口 = 杀掉常驻 Worker）。

解决：
    传 `creationflags=CREATE_NO_WINDOW` —— 进程照常运行、stdin/stdout 管道照常工作，
    只是不再分配可见的控制台窗口。

    非 Windows 平台不需要该标志，返回空 dict 即可。
"""

from __future__ import annotations

import os
import subprocess

# Windows API 常量：CREATE_NO_WINDOW = 0x08000000
_CREATE_NO_WINDOW = 0x08000000


def no_window_kwargs() -> dict:
    """给 `asyncio.create_subprocess_exec` / `subprocess.Popen` 用的附加 kwargs。

    Windows：附加 `creationflags=CREATE_NO_WINDOW`，避免弹出黑色控制台窗口。
    其他平台：返回空 dict，行为完全不变。
    """
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW)}
