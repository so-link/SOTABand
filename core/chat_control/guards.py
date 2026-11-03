"""网关侧安全约束 — 路径白名单、输出截断。

原则：**不信任 LLM 输出**。模型给出的路径可能是幻觉，也可能是注入诱导的
越界读取（如 /etc/passwd、~/.ssh）。所有 path 类参数必须经过
`resolve_allowed_path()`：realpath 解析后再做前缀匹配，符号链接逃逸一并挡掉。
"""

from __future__ import annotations

import os
from pathlib import Path

from core.resource import paths


class PathNotAllowed(Exception):
    """路径越界（面向 LLM 的错误文本）。"""


def allowed_roots() -> list[Path]:
    """允许读写的根目录：当前用户数据区（USER_HOME）+ 系统工作区（SYSTEM_ROOT）。

    多用户规划（2026-11）：用户数据在 USER_HOME=<SOTABAND_HOME>/<uid>/，
    这里按当前请求身份动态解析——只放行当前用户自己的目录，其他用户目录
    即使在同一台机器上也算越界。系统资产（resources/、storage/）整体放行
    是刻意的——平台的资源与数据本就托管在这里；真正要挡的是这两者之外
    的任意文件系统。
    """
    from core.user_paths import user_home

    roots = [
        user_home(),
        paths.PROJECT_ROOT,
    ]
    out = []
    for r in roots:
        try:
            out.append(Path(os.path.realpath(r)))
        except OSError:
            continue
    return out


def resolve_allowed_path(raw: str, *, must_exist: bool = True) -> Path:
    """把 LLM 给的字符串路径解析为受约束的绝对路径。

    相对路径按项目根解析（模型常省略前缀，如 "resources/tools"）。
    越界或不存在抛 PathNotAllowed，错误文本会回给 LLM 让它换路径。
    """
    if not raw or not str(raw).strip():
        raise PathNotAllowed("路径不能为空")
    p = Path(str(raw).strip()).expanduser()
    if not p.is_absolute():
        p = paths.PROJECT_ROOT / p
    real = Path(os.path.realpath(str(p)))
    roots = allowed_roots()
    if not any(real == r or real.is_relative_to(r) for r in roots):
        raise PathNotAllowed(
            f"路径越界：{real} 不在允许范围内。可用范围："
            + "、".join(str(r) for r in roots)
        )
    if must_exist and not real.exists():
        raise PathNotAllowed(f"路径不存在：{raw}")
    return real


def clip(text: str, limit: int) -> str:
    """只读输出截断，限制上下文注入面与 token 成本。"""
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（已截断，原长 {len(text)} 字符）"


# 只读输出的统一截断阈值
SPEC_MAX_CHARS = 4000
LIST_MAX_ITEMS = 80
