"""页面静默失败守卫：pageerror / console.error / API 4xx-5xx / 图片失败。"""

from __future__ import annotations

from dataclasses import dataclass, field


_DEFAULT_CONSOLE_ALLOW = (
    "Download the React DevTools",
    "React DevTools",
    "favicon",
    "Failed to load resource: the server responded with a status of 401",  # 登录失败用例显式 allow
)


@dataclass
class Guards:
    errors: list[str] = field(default_factory=list)
    allowed_api_prefixes: list[str] = field(default_factory=list)
    allowed_console: list[str] = field(default_factory=lambda: list(_DEFAULT_CONSOLE_ALLOW))
    ignore_images: bool = False
    allow_unauthorized: bool = False

    def allow_api(self, prefix: str) -> None:
        self.allowed_api_prefixes.append(prefix)

    def allow_console(self, text: str) -> None:
        self.allowed_console.append(text)

    def allow_images(self) -> None:
        self.ignore_images = True

    def _api_allowed(self, url: str) -> bool:
        return any(prefix in url for prefix in self.allowed_api_prefixes)

    def _console_allowed(self, text: str) -> bool:
        return any(marker in text for marker in self.allowed_console)

    def attach(self, page) -> None:
        def on_pageerror(exc) -> None:
            self.errors.append(f"pageerror: {exc}")

        def on_console(message) -> None:
            if message.type != "error":
                return
            text = message.text or ""
            if not self._console_allowed(text):
                self.errors.append(f"console.error: {text}")

        def on_response(response) -> None:
            if "/api/" not in response.url:
                return
            if response.status == 401 and self.allow_unauthorized:
                return
            if response.status < 400 or self._api_allowed(response.url):
                return
            self.errors.append(
                f"API {response.request.method} {response.url} → {response.status}"
            )

        def on_failed(request) -> None:
            if self.ignore_images or request.resource_type != "image":
                return
            failure = request.failure or "unknown"
            # 页面刷新/导航会取消尚未完成的图片请求，这不是图片加载失败。
            if "ERR_ABORTED" in failure:
                return
            self.errors.append(f"image failed: {request.url} ({failure})")

        page.on("pageerror", on_pageerror)
        page.on("console", on_console)
        page.on("response", on_response)
        page.on("requestfailed", on_failed)

    def assert_clean(self) -> None:
        if not self.errors:
            return
        detail = "\n".join(f"  - {item}" for item in self.errors[:12])
        raise AssertionError(f"页面出现未允许的静默失败（{len(self.errors)} 条）：\n{detail}")
