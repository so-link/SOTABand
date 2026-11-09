"""系统根 / 用户目录解析（多用户路径规划的单一实现）

规划（2026-11 确认）：

- **SYSTEM_ROOT**（默认 = 项目根 /root/SOTABand）：系统资产——内置工具、
  内置模型/Agent/示例数据集（resources/）、代码、.env 配置。随 git 分发。
- **USER_HOME**（默认根 /root/SB-USER，可用环境变量 SOTABAND_HOME 覆盖）：
  用户数据一律落在 ``<USER_HOME_ROOT>/<uid>/`` 下：

      platform.db          会话/消息/任务历史/cron/审计/个人偏好（SQLite）
      resources-private/   用户自建的工具/Agent/数据集/模型
      uploads/             对话附件与工作区上传
      plots/               工具绘图产物
      workspace_tools.json 工具空间清单
      workspace_tree.json  工作区文件树
      agent_logs/          Agent 执行步骤日志

- **超级用户 uid = "0"**（目录 /root/SB-USER/0/）：类比 Linux root，
  沿用迁移脚本已经落地的 0 号目录，历史数据（对话/私有资源/上传/工作区
  清单）天然归属超管，无需再搬一次。密码来自 .env
  ``SOTABAND_ROOT_PASSWORD``；用户名/显示名在用户表（见 core.auth）。
- **系统用户 uid = "100"**（目录 /root/SB-USER/100/）：类比 Linux 的
  root/bin 等系统账号。公共资源（内置数据集/工具/模型/Agent、系统定时
  任务）一律归属它；**不可登录、无密码**，仅超管可代管（领取短时令牌，
  见 POST /api/auth/act-as-system）。
- **普通用户 uid 从 1000 起**（类比 Linux 普通账号），由系统顺序分配、
  不可修改，目录如 /root/SB-USER/1000/。uid < 1000 的号位是保留位，
  永远不会分配给普通用户。

登录后（core/auth）取登录用户 id；无登录态读环境变量 SOTABAND_USER_ID；
都没有就是超管 "0"。

**系统级例外（不属于任何用户）**：
- ``users.json`` / ``secret.key``（登录用户表与令牌密钥）：平台级实体，
  验证任何用户的令牌都需要同一把密钥 → USER_HOME_ROOT/_platform/auth/。
  源码目录（SYSTEM_ROOT）只放代码，不放运行数据。
- 公共资源归属系统用户 "100"（内置资源运行期安装进系统目录后，
  owner 统一为 "100"）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# 系统根：core/user_paths.py → core → 项目根
SYSTEM_ROOT = Path(__file__).resolve().parent.parent
# 兼容旧名（paths.py / guards.py 等以 PROJECT_ROOT 引用）
PROJECT_ROOT = SYSTEM_ROOT

# 用户目录根：默认 /root/SB-USER；可用 SOTABAND_HOME 覆盖
USER_HOME_ROOT = Path(os.environ.get("SOTABAND_HOME", "/root/SB-USER"))

# 超级用户 uid（无登录态时的回退值，见 current_uid）
DEFAULT_UID = "0"

# 系统用户 uid：公共资源（内置数据集/工具/模型/Agent、系统定时任务）的归属。
# 类比 Linux 系统账号，落在普通用户段（≥1000）之下的保留位，不可登录、无密码，
# 仅超管可代管。见 core.auth。
SYSTEM_UID = "100"

# uid 消毒：只允许安全字符，防 token/env 注入路径穿越
_UID_RE = re.compile(r"[^A-Za-z0-9_.-]")


def sanitize_uid(uid: str) -> str:
    uid = _UID_RE.sub("_", str(uid or "").strip())
    return uid or DEFAULT_UID


def current_uid() -> str:
    """当前用户 ID：登录身份 > SOTABAND_USER_ID > "0"。"""
    try:
        from core.auth.session import current_identity
        rid = current_identity()
        if rid:
            return sanitize_uid(rid)
    except Exception:
        pass
    env = os.environ.get("SOTABAND_USER_ID", "").strip()
    return sanitize_uid(env) if env else DEFAULT_UID


def user_home(uid: str | None = None) -> Path:
    """用户目录（不自动创建；写操作前各自 mkdir）。"""
    return USER_HOME_ROOT / (sanitize_uid(uid) if uid is not None else current_uid())


def platform_db(uid: str | None = None) -> Path:
    """该用户的 SQLite（会话/任务/cron/审计同库）。"""
    return user_home(uid) / "platform.db"


def runtime_env(extra: dict | None = None) -> dict:
    """子进程（工具脚本 / Agent / 调试沙箱）统一环境变量。

    生成的工具与 Agent 代码**只能靠这些变量定位目录**——不允许绝对路径，
    也不允许 ``Path(__file__).parents[N]`` 反推根目录（资源搬到任何深度、
    换用户目录、复制到系统目录或换机器，反推都会算错）。

    - ``SOTABAND_ROOT``      系统根（代码 + 内置资源），只读用途
    - ``SOTABAND_USER_HOME`` 当前用户目录，一切产物写这里
    - ``SOTABAND_USER_ID``   当前用户 ID
    """
    env = {
        "SOTABAND_ROOT": str(SYSTEM_ROOT),
        "SOTABAND_USER_HOME": str(user_home()),
        "SOTABAND_USER_ID": current_uid(),
    }
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


def auth_dir() -> Path:
    """系统级认证目录（用户表 + 令牌密钥）：平台实体，不随用户走。

    放用户根下的 _platform/（非数字目录一律不是用户），不落源码目录。
    """
    return USER_HOME_ROOT / "_platform" / "auth"
