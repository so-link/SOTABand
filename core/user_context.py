"""用户 / 工作区上下文 + 资源归属模型

## 归属模型（重要）

SOTABand 的三类资源可见性**不同**，改动前务必确认：

| 资源 | 模型 | 说明 |
|------|------|------|
| **数据集** | **私有** | 数据目录在本机，天然属于当前使用者。按 owner 隔离。 |
| **工具** | **共享** | 代码资产，所有使用者看到同一批，不做归属隔离。 |
| **Agent** | **共享** | 同上，代码资产 + 编排逻辑，全局可见。 |

**为什么只有数据集私有？**
数据目录指向本机路径，从他人环境复制项目时会连同对方的注册记录一起
带过来，而那些路径在本机并不存在——展示出来就是"能选中、但一运行就
报路径不存在"的幽灵数据集。而工具/Agent 的实现代码在
``resources/{tools,agents}/implementations/`` 下，随项目目录一起复制，
代码本身是完整的，不存在幽灵问题。

**关键推论：共享资源访问私有数据时，必须在 API 层做归属过滤。**
工具是共享的，它通过 ``core/api/implementations/api_data.py`` 访问数据集。
若该 API 不做过滤，数据隔离就只在界面层生效，任何共享工具一调用即可绕过。
因此 ``api_data.py`` 内部强制按 owner 过滤，且不接受调用方传入 owner。

## 两个正交概念（务必区分）

- ``owner``      —— 谁创建的，决定谁能**改/删**
- ``visibility`` —— ``private`` / ``public``，决定谁能**看**

未来数据集上云后，``owner`` 仍是上传者，但 ``visibility`` 可设为 ``public``
让所有人访问；同理工具/Agent 进公共池就是 ``visibility=public``（当前默认），
使用者也可改 ``private`` 保留为私有。

**数据集私有是物理约束（数据在本机），不是权限偏好。上云后这个约束消失，
数据集会自然演化为可共享**——所以预留 visibility，而不是把"私有"写死。

## 身份解析（重要，多用户隔离的基础）

``owner`` 键的取值按优先级解析（:func:`current_owner_id`）：

1. **显式上下文**（``ContextVar``）—— 后端请求/后台任务通过 :func:`use_owner`
   设置，进程内多处调用拿到同一个身份；
2. **环境变量 ``SOTABAND_USER_ID``** —— **每次调用现取，不缓存**。Agent 子进程
   在每次 ``execute`` 前由 entrypoint 设置/清除；工具子进程由 ``ToolExecutor``
   在 spawn 时显式注入。这样可以避免「同一进程连续服务两个用户时缓存串号」；
3. **``storage/user.json`` 中持久化的机器标识** —— 仅未登录 / 无会话兜底，
   只缓存这一层。

历史上第 2 层结果也被缓存，导致同一 Agent 进程先服务 A 再服务 B 时，
B 的工具调用仍按 A 的 owner 落库（= A 能看到 B 的数据集）。现在环境变量层
永远实时读取，彻底消除该问题。

``storage/`` 通常在版本控制之外，因此复制项目不会带走过别人的身份标识，
天然实现"每个安装实例是独立用户"。
"""

import contextlib
import json
import os
import time
import uuid
from contextvars import ContextVar
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_USER_FILE = _PROJECT_ROOT / "storage" / "user.json"

_DEFAULT_USER_ID = "local"

# ── 可见性 ──
VISIBILITY_PRIVATE = "private"
VISIBILITY_PUBLIC = "public"

# 各类资源在「历史条目缺少 visibility 字段」时的默认可见性。
# 之所以不同，是为了在不改变现有行为的前提下平滑迁移：
# - 数据集：默认私有。数据在本机，他人本就访问不到，私有是物理事实。
# - 工具/Agent：默认共享。当前就是全局共享，不能因为加字段而让它们消失。
DEFAULT_VISIBILITY = {
    "data": VISIBILITY_PRIVATE,
    "tool": VISIBILITY_PUBLIC,
    "agent": VISIBILITY_PUBLIC,
}

# 显式身份覆盖（ContextVar）：后端请求 / 后台任务用，优先级最高。
_CURRENT_OWNER: ContextVar[str | None] = ContextVar(
    "sotaband_current_owner", default=None
)

# 进程内缓存：**只缓存机器身份兜底值**。
# 环境变量层不缓存 —— 否则子进程内切换用户会串号（见模块文档）。
_cached_machine_id: str | None = None


def get_visibility(entry: dict, resource_type: str) -> str:
    """读取条目的可见性，缺失时按资源类型取默认值。"""
    v = entry.get("visibility")
    if v in (VISIBILITY_PRIVATE, VISIBILITY_PUBLIC):
        return v
    return DEFAULT_VISIBILITY.get(resource_type, VISIBILITY_PUBLIC)


def is_visible_to(entry: dict, resource_type: str, user_id: str) -> bool:
    """判断某条目对指定用户是否可见。

    - public  → 所有人可见（工具/Agent 当前默认；未来云上共享数据集）
    - private → 仅 owner 可见

    历史条目缺 visibility 时按 DEFAULT_VISIBILITY 处理，因此
    工具/Agent 保持共享、数据集保持私有，行为与加字段前一致。
    """
    if get_visibility(entry, resource_type) == VISIBILITY_PUBLIC:
        return True
    return entry.get("owner") == user_id


def detect_storage(data_path: str) -> str:
    """根据 data_path 判断数据集存储位置：local / remote。

    未来数据集上云后，data_path 会是 http(s)://、s3:// 等地址。
    这类条目不能用本地文件系统去校验，否则会被误判为"不可用"而过滤掉。
    """
    p = str(data_path or "").strip().lower()
    if p.startswith(("http://", "https://", "s3://", "gs://", "oss://", "ftp://")):
        return "remote"
    return "local"


def _load_persisted() -> str | None:
    try:
        if _USER_FILE.exists():
            data = json.loads(_USER_FILE.read_text(encoding="utf-8"))
            uid = data.get("user_id")
            if isinstance(uid, str) and uid.strip():
                return uid.strip()
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def _persist(uid: str) -> None:
    try:
        _USER_FILE.parent.mkdir(parents=True, exist_ok=True)
        _USER_FILE.write_text(
            json.dumps(
                {"user_id": uid, "created_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        # 持久化失败不影响使用，退化为仅本次进程有效
        pass


# ── 显式身份覆盖（ContextVar）───────────────────────────────────────


def set_current_owner(owner_id: str | None) -> None:
    """设置当前执行上下文的资源归属标识（None = 清除）。"""
    _CURRENT_OWNER.set(str(owner_id) if owner_id else None)


def get_current_owner() -> str | None:
    """读取 ContextVar 中显式设置的归属标识（未设置返回 None）。"""
    return _CURRENT_OWNER.get()


@contextlib.contextmanager
def use_owner(owner_id: str | None):
    """以指定归属身份执行一段代码（退出后自动恢复）。

    典型用法：后台任务 / 请求中间件里包住整个处理过程，
    这样深层代码（registry、api_data 等）拿到的都是同一个身份。
    """
    token = _CURRENT_OWNER.set(str(owner_id) if owner_id else None)
    try:
        yield
    finally:
        _CURRENT_OWNER.reset(token)


# ── 解析 ────────────────────────────────────────────────────────────


def _machine_identity() -> str:
    """机器身份兜底（只缓存这一层，见模块文档）。"""
    global _cached_machine_id
    if _cached_machine_id:
        return _cached_machine_id

    persisted = _load_persisted()
    if persisted:
        _cached_machine_id = persisted
        return _cached_machine_id

    # 首次运行：生成一个稳定的本地标识并持久化
    uid = f"local-{uuid.uuid4().hex[:8]}"
    _persist(uid)
    _cached_machine_id = uid
    return _cached_machine_id


def current_owner_id() -> str:
    """解析当前执行上下文的资源归属标识。

    优先级（见模块文档"身份解析"）：

    1. ContextVar 显式设置（:func:`use_owner` / :func:`set_current_owner`）；
    2. 环境变量 ``SOTABAND_USER_ID``（**每次现取，不缓存**）；
    3. ``storage/user.json`` 持久化的机器身份（仅未登录兜底）。
    """
    explicit = _CURRENT_OWNER.get()
    if explicit:
        return explicit

    env_uid = os.getenv("SOTABAND_USER_ID", "").strip()
    if env_uid:
        return env_uid

    return _machine_identity()


def get_current_user_id() -> str:
    """兼容别名 —— 等价于 :func:`current_owner_id`。

    历史代码大量调用本函数；统一转调后，「系统 API」与 HTTP 路由共用同一套
    身份解析语义（这是管理员评审意见 1 的修复点之一）。
    """
    return current_owner_id()


def reset_cache() -> None:
    """清空身份缓存（供测试或身份切换时使用）。

    注意：环境变量层本来就不缓存，这里主要清机器身份兜底值。
    进程内切换执行身份请优先使用 :func:`use_owner` / :func:`set_current_owner`。
    """
    global _cached_machine_id
    _cached_machine_id = None


def resolve_owner_id(ctx=None) -> str:
    """解析资源的归属标识（owner 键）。

    8.1/8.2 引入「人身份」（``users.id``，UUID）后，资源归属应统一使用该身份，
    否则会出现「机器身份 ``local-xxxx``」与「人身份 UUID」两套键互不相通的问题：

    - 多用户共用一台机器时，彼此能看到 / 删除对方的数据集；
    - 共享数据集时，接收方按 UUID 身份安装，而数据集过滤仍按 ``local-xxxx``，
      导致接收方看不到已共享的数据集。

    规则：

    - 已认证请求（传入 ``AuthContext``）→ 使用 ``ctx.user_id``（UUID）；
    - 未认证 / 无会话（CLI、Agent 子进程、后台任务）→ 回退到
      :func:`current_owner_id`（其优先读显式上下文与环境变量
      ``SOTABAND_USER_ID``，因此工具/Agent 子进程若被注入该变量，
      会自然拿到发起方的 UUID）。
    """
    if ctx is not None and getattr(ctx, "user_id", None):
        return str(ctx.user_id)
    return current_owner_id()


__all__ = [
    "DEFAULT_VISIBILITY",
    "VISIBILITY_PRIVATE",
    "VISIBILITY_PUBLIC",
    "current_owner_id",
    "detect_storage",
    "get_current_owner",
    "get_current_user_id",
    "get_visibility",
    "is_visible_to",
    "reset_cache",
    "resolve_owner_id",
    "set_current_owner",
    "use_owner",
]
