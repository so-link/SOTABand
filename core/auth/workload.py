"""工作负载身份（Workload Identity）—— PRD FR-N7 / FR-N8。

## 为什么需要它

PRD FR-N7 的核心要求：

> Agent 之间的通信必须使用**独立的工作负载身份**，而不是复用某个用户的会话
> Cookie；凭证要**短期有效、可审计**；成员退出后要在 SLA 内失去权限。

如果 Agent 直接拿用户的 Cookie 去调别的 Agent，会带来三个问题：

1. **无法追溯**：审计里只能写"某个用户发起"，分不清是用户本人还是他装的 Agent；
2. **权限过宽**：Agent 会继承用户的**全部**权限，而它本该只能做被授权的那件事；
3. **无法撤权**：吊销用户会话会连用户本人一起踢下线。

所以 Agent 通信要用 `installation` 派生的独立身份。

## ⚠️ 为什么这里能用签名令牌，而会话令牌不行

本项目在 ``core/auth/tokens.py`` 里明确写过「**不用 JWT，因为无法即时吊销**」。
那条结论针对的是**会话令牌**：会话的有效性**只由令牌本身**决定，签名一旦签发就
无法收回，所以必须查库。

工作负载令牌**不是**这样用的：

- 它只是**身份指针** —— 声称"我是 installation X"；
- **每一次调用都重新从数据库推导授权**（installation 是否 active、publication 是否
  仍发布、成员关系是否还在、角色是否有 ``network.agent.invoke``）；
- 签名的作用**仅仅是防止伪造** ``installation_id`` 而不查表，不承载任何权限。

因此撤权语义与查库方案完全一致：**吊销 installation / 撤回 publication / 移除成员，
下一次调用立刻失效**。这正是 FR-N8 想要的即时撤权。

换句话说：
- 会话令牌 = 「通行证」，必须查库确认没被吊销；
- 工作负载令牌 = 「工牌」，只用来认人，**能不能进门每次都看门禁系统**。

## 令牌格式

```text
w1.<installation_id>.<exp 的 unix 秒>.<jti>.<HMAC-SHA256 十六进制>
```

``jti`` 是随机串，进入审计日志，用于把"签发的凭证"和"用它发起的调用"关联起来。

## 有效期

默认 **600 秒**（10 分钟）。PRD 要求"短期"，且即使不主动吊销，泄露的窗口也很小。
可用 ``SOTABAND_WORKLOAD_TTL`` 调整；签发时**强制夹取到 [60, 3600] 秒** ——
调用方显式传 ``ttl_seconds=10**9``（或配置写成超大值）不能把短期凭证变成
事实上的永久凭证。

## 生产配置要求

``SOTABAND_WORKLOAD_SECRET`` 生产环境**必须显式配置且足够长（建议 ≥ 32 字节）**。
未配置时进程内随机生成（重启即失效）；配置了但短于 32 时只告警、不阻断启动
（单机开发常有短密钥；生产由部署清单保证）。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from dataclasses import dataclass

from config.settings import settings
from core.db.base import utcnow

logger = logging.getLogger(__name__)

#: 令牌版本前缀（将来改格式时用于平滑过渡）
WORKLOAD_TOKEN_PREFIX = "w1"

#: 签名算法标识
_SIG_HEX_LEN = 64

_runtime_secret: bytes | None = None

#: 生产建议的最小密钥长度（只告警，不阻断启动）
MIN_SECRET_LENGTH = 32

#: 已就"密钥过短"告警过一次（避免每次签名都刷日志）
_short_secret_warned = False


class WorkloadTokenError(ValueError):
    """工作负载令牌无效（格式错 / 签名错 / 过期）。对外一律映射为 401。"""


def _secret() -> bytes:
    """取 HMAC 密钥。

    优先用 ``SOTABAND_WORKLOAD_SECRET``。未配置时**在进程内随机生成**并告警：

    - 不写死默认密钥 —— 那等于把签名密钥提交进仓库；
    - 代价是进程重启后旧凭证全部失效。对**短期**凭证（默认 10 分钟）可以接受，
      而且这本身就是一次强制轮换。

    生产环境必须显式配置，否则多 worker / 重启会互相不认。
    """
    global _runtime_secret, _short_secret_warned

    configured = (settings.workload.secret or "").strip()
    if configured:
        if len(configured) < MIN_SECRET_LENGTH and not _short_secret_warned:
            _short_secret_warned = True
            logger.warning(
                "SOTABAND_WORKLOAD_SECRET 长度 %d < %d，签名强度不足；"
                "生产环境请配置至少 %d 字节的随机密钥。",
                len(configured),
                MIN_SECRET_LENGTH,
                MIN_SECRET_LENGTH,
            )
        return configured.encode("utf-8")

    if _runtime_secret is None:
        _runtime_secret = secrets.token_bytes(32)
        logger.warning(
            "SOTABAND_WORKLOAD_SECRET 未配置，已为本进程生成临时密钥 —— "
            "进程重启后所有工作负载凭证失效。生产环境请显式配置。"
        )
    return _runtime_secret


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class WorkloadClaims:
    """校验通过的令牌声明。**只包含令牌能证明的东西**。

    注意这里**没有** ``tenant_id`` / ``network_id`` / ``role`` —— 那些一律从数据库
    现查（见模块文档：令牌只是身份指针，不承载权限）。
    """

    installation_id: uuid.UUID
    #: 过期时间（unix 秒）
    expires_at: int
    #: 令牌唯一 id，写进审计用于关联
    jti: str

    @property
    def actor_id(self) -> str:
        return str(self.installation_id)


def issue_workload_token(
    installation_id: uuid.UUID, *, ttl_seconds: int | None = None
) -> tuple[str, int]:
    """为某个安装实例签发短期工作负载令牌。

    返回 ``(明文令牌, 过期 unix 秒)``。

    调用方必须**先**确认调用者确实拥有这个 installation（见
    ``core/sharing/service.py::issue_credential``）—— 本函数不做授权。
    """
    raw_ttl = ttl_seconds if ttl_seconds is not None else settings.workload.ttl_seconds
    try:
        ttl = int(raw_ttl)
    except (TypeError, ValueError) as exc:
        raise ValueError("ttl_seconds 必须为整数") from exc
    # 短期凭证的硬边界：调用方/配置不能把有效期放大成永久凭证。
    ttl = min(max(ttl, 60), 3600)

    expires_at = int(utcnow().timestamp()) + ttl
    jti = secrets.token_urlsafe(9)
    payload = f"{installation_id}.{expires_at}.{jti}"
    token = f"{WORKLOAD_TOKEN_PREFIX}.{payload}.{_sign(payload)}"
    return token, expires_at


def verify_workload_token(token: str) -> WorkloadClaims:
    """校验令牌并返回声明。失败抛 :class:`WorkloadTokenError`。

    校验顺序：格式 → 签名（恒定时间比较）→ 过期。
    **先验签名再看过期**：否则攻击者可以用过期时间做探测。
    """
    if not token or not isinstance(token, str):
        raise WorkloadTokenError("缺少工作负载令牌")

    parts = token.split(".")
    # w1 / installation_id / exp / jti / sig
    if len(parts) != 5 or parts[0] != WORKLOAD_TOKEN_PREFIX:
        raise WorkloadTokenError("工作负载令牌格式不正确")

    _, raw_id, raw_exp, jti, sig = parts
    if len(sig) != _SIG_HEX_LEN or not jti:
        raise WorkloadTokenError("工作负载令牌格式不正确")

    payload = f"{raw_id}.{raw_exp}.{jti}"
    if not hmac.compare_digest(sig, _sign(payload)):
        raise WorkloadTokenError("工作负载令牌签名无效")

    try:
        installation_id = uuid.UUID(raw_id)
        expires_at = int(raw_exp)
    except (ValueError, TypeError) as exc:
        raise WorkloadTokenError("工作负载令牌格式不正确") from exc

    if expires_at <= int(utcnow().timestamp()):
        raise WorkloadTokenError("工作负载令牌已过期")

    return WorkloadClaims(
        installation_id=installation_id, expires_at=expires_at, jti=jti
    )


__all__ = [
    "WORKLOAD_TOKEN_PREFIX",
    "WorkloadClaims",
    "WorkloadTokenError",
    "issue_workload_token",
    "verify_workload_token",
]
