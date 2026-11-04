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
    runtime = os.environ.get("SOTABAND_RUNTIME_ROOT", "").strip()
    if runtime:
        return Path(runtime).expanduser().resolve() / "resources-private"
    return PROJECT_ROOT.parent / "resources-private"


PRIVATE_ROOT = Path(_PRIVATE_ENV) if _PRIVATE_ENV else _default_private_root()


def resource_roots() -> list[Path]:
    """返回所有资源根目录（内置在前、私有在后），用于「读」时合并。

    顺序有语义：``iter_tool_entries()`` 等做法是「后面的覆盖前面的」，
    因此私有根排在后面 = 私有条目覆盖内置条目。**不要翻转这个顺序**，
    否则会变成内置覆盖私有，注册表合并整体失效。

    需要「定位实际存在的文件」时用 :func:`read_roots`（私有优先）。
    """
    roots = [BUILTIN_ROOT]
    if PRIVATE_ROOT not in roots:
        roots.append(PRIVATE_ROOT)
    return roots


def read_roots() -> list[Path]:
    """定位文件时的查找顺序：私有在前、内置在后。

    为什么与 :func:`resource_roots` 相反：写入一律落私有根（新建 / 编辑 /
    导入都是），而 ``register`` 的合并语义也是「私有覆盖内置」。若查找时
    内置优先，「同 id 同时存在于两个根」的工具就会永远读到内置那份旧文件 ——
    表现为「改了代码 / MD，重新打开还是旧的」（历史上真实出现过：
    b5cb0eb 引入内置优先读取后，save-code 写入私有根的文件被内置副本遮蔽）。

    私有根没有对应文件时自然回落到内置，因此未编辑过的内置资源不受影响。
    """
    return list(reversed(resource_roots()))


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
