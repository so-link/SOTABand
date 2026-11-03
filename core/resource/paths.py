"""资源根目录解析

【归属】SOTABand 的资源分为两类：

1. **内置资源**（``SYSTEM_ROOT/resources/``）：随项目仓库分发，可提交到 git。
2. **用户私有资源**（``USER_HOME/resources-private/``）：用户创建的资源
   （数据集 / 工具集 / 智能体集），**不提交 git**，仅存于用户目录。

平台加载时**同时读取**两类资源（私有覆盖 / 追加内置），
而**新建的资源一律写入用户私有目录**。

多用户路径规划（2026-11）：系统资产在 SYSTEM_ROOT（项目根），用户数据
在 USER_HOME = ``<SOTABAND_HOME>/<当前uid>/``（默认 /root/SB-USER/0/，
暂未启用多用户，默认 uid="0"）。解析逻辑的唯一实现在 ``core.user_paths``，
本模块只是薄封装并保持 ``PRIVATE_ROOT`` 兼容别名。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from core.user_paths import SYSTEM_ROOT, current_uid, user_home

# 项目根：保留旧名以兼容既有引用（语义 = SYSTEM_ROOT）
PROJECT_ROOT = SYSTEM_ROOT

# 内置资源根（随仓库分发）
BUILTIN_ROOT = SYSTEM_ROOT / "resources"


def private_root(uid: str | None = None) -> Path:
    """用户私有资源根（动态：登录态下不同用户不同目录）。"""
    return user_home(uid) / "resources-private"


# 兼容别名：**仅供模块导入期默认值使用**（如 registry 构造时）。
# 运行期读写一律走 private_root()（按当前请求身份动态解析），
# 不要用本常量——登录态下它会固定到导入时刻的用户。
PRIVATE_ROOT = private_root()


# 资源 id 命名约束：小写字母/数字开头，仅含小写字母、数字、-、_，长度 ≤100。
# 资源 id 会被直接拼进实现目录路径（implementations/<id>），必须拒绝
# "../" 等穿越字符——否则注册接口可把代码写到任意目录（路径穿越）。
RESOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")


def is_valid_resource_id(value: str) -> bool:
    """判断资源 id 是否安全可用作目录名。"""
    return isinstance(value, str) and bool(RESOURCE_ID_RE.fullmatch(value))


def resource_roots() -> list[Path]:
    """返回所有资源根目录（内置 + 当前用户私有），用于「读」时合并。"""
    roots = [BUILTIN_ROOT]
    pr = private_root()
    if pr not in roots:
        roots.append(pr)
    return roots


def space_dirs(space: str) -> list[Path]:
    """返回某资源空间（tools/agents/data/models）在所有根下的目录列表。"""
    return [root / space for root in resource_roots()]


def builtin_space_dir(space: str) -> Path:
    """返回某资源空间的内置目录（唯一）。"""
    return BUILTIN_ROOT / space


def private_space_dir(space: str) -> Path:
    """返回当前用户某资源空间的私有目录（新建资源写入此处）。"""
    return private_root() / space


def ensure_private_dirs(space: str) -> Path:
    """确保私有空间目录存在并返回。"""
    d = private_space_dir(space)
    d.mkdir(parents=True, exist_ok=True)
    return d
