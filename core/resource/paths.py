"""资源根目录解析

【归属】SOTABand 的资源分两类（2026-11 资源归属改造后）：

1. **公共资源**（``user_home("100")`` = 系统用户目录）：内置数据集 / 工具 /
   模型 / Agent / 定时任务的归属层（owner="100"、visibility=public，
   全员可见）。运行期可写，但**只有系统身份（act-as 令牌，见
   POST /api/auth/act-as-system）能写**。
2. **用户私有资源**（``user_home(uid)``，扁平布局，空间目录直接落家目录
   顶层）：用户自建资源，**不提交 git**，仅自己可见。

运行期解析顺序 ``resource_roots()`` = **[私有根, 系统根]**，先命中者胜出
（覆盖层语义：fork 出的私有副本覆盖公共资源）。

``SYSTEM_ROOT/resources/`` 降级为**出厂种子包**（git 分发，经
scripts/seed_system_resources.py 幂等导入系统用户目录），不再参与运行期
解析；``BUILTIN_ROOT`` 常量保留仅作为种子包定位与 fork 升级哈希的参照。

解析逻辑的唯一实现在 ``core.user_paths``，本模块只是薄封装。
"""

from __future__ import annotations

import re
from pathlib import Path

from core.user_paths import SYSTEM_ROOT, SYSTEM_UID, current_uid, user_home

# 项目根：保留旧名以兼容既有引用（语义 = SYSTEM_ROOT）
PROJECT_ROOT = SYSTEM_ROOT

# 出厂种子包根（git 分发；运行期不解析，仅导入/哈希比对时引用）
BUILTIN_ROOT = SYSTEM_ROOT / "resources"


def private_root(uid: str | None = None) -> Path:
    """用户私有资源根 = 家目录本身（扁平布局，无中间层）。

    动态：登录态下不同用户不同目录。uid="100"（系统用户）时它就是系统根。
    """
    return user_home(uid)


def system_root() -> Path:
    """公共资源根：系统用户（uid=100）的家目录，全员可读、仅系统身份可写。"""
    return user_home(SYSTEM_UID)


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
    """资源根搜索顺序（读）：**私有根优先**，系统根兜底。

    2026-11 资源归属改造：[私有, _shared, 内置] 收敛为 [私有, 系统]。
    公共资源（原内置 + 原超管发布的共享资源）统一落系统用户目录
    （SB-USER/100/），_shared 根与 git 内置根的运行期解析一并退役。

    覆盖层语义不变（方案 C：显式 fork + 覆盖层）：用户「复制为我的副本」
    后编辑落私有根，同名资源私有优先命中。写路径一律落可写层。
    系统用户自己（uid=100）调用时两层是同一目录，去重只保留一个。
    """
    roots: list[Path] = [private_root(), system_root()]
    if roots[1] == roots[0]:
        roots.pop()
    return roots


def space_dirs(space: str) -> list[Path]:
    """返回某资源空间（tools/agents/data/models）在所有根下的目录列表。"""
    return [root / space for root in resource_roots()]


def builtin_space_dir(space: str) -> Path:
    """返回某资源空间的出厂种子目录（唯一；运行期不解析，仅导入/哈希参照）。"""
    return BUILTIN_ROOT / space


def private_space_dir(space: str) -> Path:
    """返回当前用户某资源空间的私有目录（新建资源写入此处）。"""
    return private_root() / space


def system_space_dir(space: str) -> Path:
    """返回某资源空间的公共（系统用户）目录。"""
    return system_root() / space


def ensure_private_dirs(space: str) -> Path:
    """确保私有空间目录存在并返回。"""
    d = private_space_dir(space)
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_system_dirs(space: str) -> Path:
    """确保公共空间目录存在并返回（仅系统身份写路径使用）。"""
    d = system_space_dir(space)
    d.mkdir(parents=True, exist_ok=True)
    return d


def source_of(root: Path) -> str:
    """命中根 → 派生 source 字段：私有 / 公共（系统用户）。

    不落盘——每次读时按当前身份与解析顺序现算，供前端徽章与闸门判定。
    """
    return "private" if root == private_root() else "system"
