"""条件轮询小工具（避免固定 sleep 作为断言）。"""

from __future__ import annotations

import time


def wait_until(predicate, timeout: float = 10.0, interval: float = 0.2, description: str = ""):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"等待超时（{timeout}s）：{description}；最后值={last!r}")
