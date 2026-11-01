"""资源根目录解析

SOTABand 的资源分为两类：

1. **内置资源**（``resources/``）：随项目仓库分发，可提交到 git。
2. **私有资源**（``resources-private/``）：用户在本机客户端新创建的资源
   （数据集 / 工具集 / 智能体集），**不提交 git**，仅存于本地环境。

平台加载时**同时读取**两个目录下的同类资源（后者覆盖 / 追加），
而**新建的资源一律写入私有目录**——这样「公共模板」与「个人资产」自然隔离。

目录位置：私有目录位于项目根目录的同级位置（与 resources/ 平级），
默认路径为 ``<项目根>/resources-private``，可通过环境变量
``SOTABAND_PRIVATE_RESOURCES`` 覆盖。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# 项目根：core/resource/paths.py → core/resource → core → 项目根
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 内置资源根（随仓库分发）
BUILTIN_ROOT = PROJECT_ROOT / "resources"

# 私有资源根（本地新建，不入 git）
# 需求指定位置为「SOTABand 目录下」——即项目根的父目录（/hdd/sdc1/jmlv/SOTABand）下的 resources-private。
# 兼容两种约定：优先使用环境变量；否则用项目根同级目录。
_PRIVATE_ENV = os.environ.get("SOTABAND_PRIVATE_RESOURCES", "").strip()


def _default_private_root() -> Path:
    # 优先：<项目根>/resources-private（若存在或可创建）
    # 备选：<项目根父目录>/resources-private（按用户指定的“SOTABand 目录下”）
    return PROJECT_ROOT.parent / "resources-private"


PRIVATE_ROOT = Path(_PRIVATE_ENV) if _PRIVATE_ENV else _default_private_root()


# 资源 id 命名约束：小写字母/数字开头，仅含小写字母、数字、-、_，长度 ≤100。
# 资源 id 会被直接拼进实现目录路径（implementations/<id>），必须拒绝
# "../" 等穿越字符——否则注册接口可把代码写到任意目录（路径穿越）。
RESOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")


def is_valid_resource_id(value: str) -> bool:
    """判断资源 id 是否安全可用作目录名。"""
    return isinstance(value, str) and bool(RESOURCE_ID_RE.fullmatch(value))


def resource_roots() -> list[Path]:
    """返回所有资源根目录（内置 + 私有），用于「读」时合并。"""
    roots = [BUILTIN_ROOT]
    if PRIVATE_ROOT not in roots:
        roots.append(PRIVATE_ROOT)
    return roots


def space_dirs(space: str) -> list[Path]:
    """返回某资源空间（tools/agents/data/models）在所有根下的目录列表。"""
    return [root / space for root in resource_roots()]


def builtin_space_dir(space: str) -> Path:
    """返回某资源空间的内置目录（唯一）。"""
    return BUILTIN_ROOT / space


def private_space_dir(space: str) -> Path:
    """返回某资源空间的私有目录（唯一，新建资源写入此处）。"""
    return PRIVATE_ROOT / space


def ensure_private_dirs(space: str) -> Path:
    """确保私有空间目录存在并返回。"""
    d = private_space_dir(space)
    d.mkdir(parents=True, exist_ok=True)
    return d
