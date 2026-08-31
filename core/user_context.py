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

## 这里提供什么

一个稳定的本地用户标识，充当"这个安装实例属于谁"。取值的优先级：

1. 环境变量 ``SOTABAND_USER_ID``  —— 多人/多环境显式区分时使用
2. ``storage/user.json`` 中持久化的标识 —— 首次运行自动生成
3. 兜底 ``local``

``storage/`` 通常在版本控制之外，因此复制项目不会带走过别人的身份标识，
天然实现"每个安装实例是独立用户"。
"""

import json
import os
import time
import uuid
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

# 进程内缓存：避免每次请求都读写文件
_cached_user_id: str | None = None


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


def get_current_user_id() -> str:
    """获取当前用户 / 工作区标识（带进程内缓存）"""
    global _cached_user_id
    if _cached_user_id:
        return _cached_user_id

    # 1) 环境变量优先，便于显式指定
    env_uid = os.getenv("SOTABAND_USER_ID", "").strip()
    if env_uid:
        _cached_user_id = env_uid
        return _cached_user_id

    # 2) 已持久化的标识
    persisted = _load_persisted()
    if persisted:
        _cached_user_id = persisted
        return _cached_user_id

    # 3) 首次运行：生成一个稳定的本地标识并持久化
    uid = f"local-{uuid.uuid4().hex[:8]}"
    _persist(uid)
    _cached_user_id = uid
    return _cached_user_id


def reset_cache() -> None:
    """清空缓存（供测试或切换用户时使用）"""
    global _cached_user_id
    _cached_user_id = None
