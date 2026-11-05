"""进程内滑动窗口限流。

设计见 ``docs/design-08-01-auth.md`` §5.2。对应威胁模型 T2（密码爆破）。

## ⚠️ 单 worker 假设

进程内计数意味着**多 worker 部署时每个进程各自计数**，实际阈值会被乘以
worker 数。MVP 是单 worker（启动脚本未加 ``--workers``），因此有效；一旦有人
加上 ``--workers 4``，防护会静默削弱到 1/4。

这是有意接受的取舍（ADR-08-09 把 Redis 挂到了 Phase 5 决策门），但**必须在
部署文档里写明**，否则将来会变成"以为有防护其实没有"。

## 为什么用单调时钟

窗口计算用 ``time.monotonic()`` —— 系统时间被 NTP 校准或手动调整时不会
产生负窗口或窗口永久不过期。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowResult:
    """限流判定结果。"""

    allowed: bool
    #: 被拒时建议的重试等待秒数（向上取整，至少 1）
    retry_after: int = 0
    #: 当前窗口内剩余可用次数
    remaining: int = 0


class SlidingWindowCounter:
    """朴素滑动窗口计数器：窗口内事件数达到上限即拒绝。

    用于注册这类"没有失败/成功之分，只数次数"的场景。
    """

    def __init__(self, max_events: int, window_seconds: int, *, name: str = "") -> None:
        self.max_events = max_events
        self.window = window_seconds
        self.name = name or self.__class__.__name__
        self._events: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._events.get(key)
        if bucket is None:
            bucket = deque()
            self._events[key] = bucket
        cutoff = now - self.window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        return bucket

    def hit(self, key: str) -> WindowResult:
        """记录一次事件并返回判定。被拒时不记录（避免计数器无限增长）。"""
        now = time.monotonic()
        self._sweep_if_needed(now)
        bucket = self._prune(key, now)

        if len(bucket) >= self.max_events:
            oldest = bucket[0]
            retry = max(1, int(self.window - (now - oldest)) + 1)
            return WindowResult(allowed=False, retry_after=retry, remaining=0)

        bucket.append(now)
        return WindowResult(
            allowed=True, remaining=max(0, self.max_events - len(bucket))
        )

    def peek(self, key: str) -> WindowResult:
        """**只读**判定 —— 不记录事件。

        用于「先看有没有超限，再决定要不要计数」的场景。典型是改密码：
        请求进来先 peek 判断是否已被限流，只有**当前密码校验失败**时才 ``hit()``
        计数。若用 ``hit()`` 来做准入判断，正常用户的每次成功请求也会消耗配额。
        """
        now = time.monotonic()
        self._sweep_if_needed(now)
        bucket = self._prune(key, now)

        if len(bucket) >= self.max_events:
            oldest = bucket[0]
            retry = max(1, int(self.window - (now - oldest)) + 1)
            return WindowResult(allowed=False, retry_after=retry, remaining=0)
        return WindowResult(
            allowed=True, remaining=max(0, self.max_events - len(bucket))
        )

    def reset(self, key: str) -> None:
        self._events.pop(key, None)

    def reset_all(self) -> None:
        self._events.clear()

    def _sweep_if_needed(self, now: float) -> None:
        """key 数量超过阈值时清理全空桶，防止内存无界增长。"""
        if len(self._events) < 4096:
            return
        cutoff = now - self.window
        for key in list(self._events):
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                del self._events[key]


class LoginThrottle:
    """登录限流：窗口内连续失败达到上限 → 临时锁定。

    与 :class:`SlidingWindowCounter` 的区别是**只统计失败**：登录成功会清空
    计数，所以正常用户不会被自己"成功登录"的次数拖累。
    """

    def __init__(
        self,
        max_failures: int,
        window_seconds: int,
        lock_seconds: int,
        *,
        name: str = "",
    ) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self.lock_seconds = lock_seconds
        self.name = name or self.__class__.__name__
        self._failures: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}

    def check(self, key: str) -> WindowResult:
        """检查是否处于锁定中（不记录失败）。"""
        now = time.monotonic()
        until = self._locked_until.get(key)
        if until is not None:
            if until > now:
                return WindowResult(
                    allowed=False, retry_after=max(1, int(until - now) + 1), remaining=0
                )
            # 锁已过期，清掉
            self._locked_until.pop(key, None)
            self._failures.pop(key, None)

        bucket = self._prune(key, now)
        return WindowResult(
            allowed=True, remaining=max(0, self.max_failures - len(bucket))
        )

    def record_failure(self, key: str) -> WindowResult:
        """记录一次失败；达到阈值则进入锁定。"""
        now = time.monotonic()
        bucket = self._prune(key, now)
        bucket.append(now)

        if len(bucket) >= self.max_failures:
            self._locked_until[key] = now + self.lock_seconds
            bucket.clear()
            return WindowResult(
                allowed=False, retry_after=self.lock_seconds, remaining=0
            )
        return WindowResult(
            allowed=True, remaining=max(0, self.max_failures - len(bucket))
        )

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)

    def reset_all(self) -> None:
        self._failures.clear()
        self._locked_until.clear()

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._failures.get(key)
        if bucket is None:
            bucket = deque()
            self._failures[key] = bucket
        cutoff = now - self.window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(self._failures) > 4096:
            self._sweep(now)
        return bucket

    def _sweep(self, now: float) -> None:
        cutoff = now - self.window
        for key in list(self._failures):
            bucket = self._failures[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket and key not in self._locked_until:
                del self._failures[key]


# ── 全局单例（进程内）─────────────────────────────────────────────────


def login_throttle() -> LoginThrottle:
    """账号维度的登录失败节流。"""
    global _LOGIN_THROTTLE
    if _LOGIN_THROTTLE is None:
        from config.settings import settings

        _LOGIN_THROTTLE = LoginThrottle(
            max_failures=settings.auth.login_max_attempts_per_account,
            window_seconds=settings.auth.login_window_seconds,
            lock_seconds=settings.auth.login_lock_seconds,
            name="login-account",
        )
    return _LOGIN_THROTTLE


def register_limiter() -> SlidingWindowCounter:
    """IP 维度的注册限流，并复用作登录的 IP 维度粗限流。"""
    global _REGISTER_LIMITER
    if _REGISTER_LIMITER is None:
        from config.settings import settings

        _REGISTER_LIMITER = SlidingWindowCounter(
            max_events=settings.auth.register_max_per_ip_hour,
            window_seconds=3600,
            name="register-ip",
        )
    return _REGISTER_LIMITER


def login_ip_limiter() -> SlidingWindowCounter:
    """登录的 IP 维度限流 —— 防单机 spraying。

    与账号维度（[:func:`login_throttle`]）互补：

    - 账号维度挡"盯着一个账号猛试"；
    - IP 维度挡"一个来源扫很多账号"。
    """
    global _LOGIN_IP_LIMITER
    if _LOGIN_IP_LIMITER is None:
        from config.settings import settings

        _LOGIN_IP_LIMITER = SlidingWindowCounter(
            max_events=settings.auth.login_ip_max_per_window,
            window_seconds=settings.auth.login_ip_window_seconds,
            name="login-ip",
        )
    return _LOGIN_IP_LIMITER


def password_change_limiter() -> SlidingWindowCounter:
    """改密码失败的账号维度限流。

    ## 为什么改密码也需要限流

    登录有限流是常识，但改密码接口常被忽略 —— 它**同样要校验当前密码**，
    于是持有一个有效会话（例如窃取的 Cookie）的攻击者可以在这里反复猜密码，
    而且完全绕过登录接口的限流。

    账号维度（而不是 IP 维度）是对的：这个场景里攻击者已有会话，IP 是次要特征。

    只有**失败**才计数（见路由层）；成功改密后计数会被清零。
    """
    global _PASSWORD_CHANGE_LIMITER
    if _PASSWORD_CHANGE_LIMITER is None:
        from config.settings import settings

        _PASSWORD_CHANGE_LIMITER = SlidingWindowCounter(
            max_events=settings.auth.password_change_max_failures,
            window_seconds=settings.auth.password_change_window_seconds,
            name="password-change",
        )
    return _PASSWORD_CHANGE_LIMITER


def invoke_limiter() -> SlidingWindowCounter:
    """Agent 调用（FR-N7）的**调用方安装实例**维度限流。

    ## 为什么这个限流是必要的（设计文档 §8.3 的遗留项）

    白名单解决的是"**能不能**调"，速率限制解决的是"**能调多快**"。两者缺一不可：

    - 白名单放行之后，目标方对调用**没有任何拒绝手段**；
    - 一个写错的 Agent（`while true: call(...)`）会把目标 Agent 打到过载 ——
      典型的"吵闹邻居"。

    维度选 installation 而不是 IP 或用户：滥用主体是**某个跑飞的 Agent 进程**，
    而它正好对应一个安装实例。同一个人有多个工作空间时互不影响，也不会因为
    共用出口 IP 误伤同事。
    """
    global _INVOKE_LIMITER
    if _INVOKE_LIMITER is None:
        from config.settings import settings

        _INVOKE_LIMITER = SlidingWindowCounter(
            max_events=settings.workload.invoke_max_per_window,
            window_seconds=settings.workload.invoke_window_seconds,
            name="agent-invoke",
        )
    return _INVOKE_LIMITER


def reset_all_limiters() -> None:
    """清空全部限流状态（供测试使用）。"""
    global _LOGIN_THROTTLE, _REGISTER_LIMITER, _LOGIN_IP_LIMITER, _PASSWORD_CHANGE_LIMITER
    global _INVOKE_LIMITER
    _LOGIN_THROTTLE = None
    _REGISTER_LIMITER = None
    _LOGIN_IP_LIMITER = None
    _PASSWORD_CHANGE_LIMITER = None
    _INVOKE_LIMITER = None


_LOGIN_THROTTLE: LoginThrottle | None = None
_REGISTER_LIMITER: SlidingWindowCounter | None = None
_LOGIN_IP_LIMITER: SlidingWindowCounter | None = None
_PASSWORD_CHANGE_LIMITER: SlidingWindowCounter | None = None
_INVOKE_LIMITER: SlidingWindowCounter | None = None
