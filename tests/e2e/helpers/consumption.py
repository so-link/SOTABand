"""接口响应 → 页面实际内容对账。

核心不是「接口 200」，而是：接口返回的字段值必须出现在指定页面区域。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Pattern


@dataclass(frozen=True)
class ConsumptionRule:
    api: str | Pattern[str]
    fields: list[str]
    require: int = 1
    scope: str | None = None
    image_selector: str | None = None


def _flatten(value: Any) -> list[Any]:
    if isinstance(value, list):
        out: list[Any] = []
        for item in value:
            out.extend(_flatten(item))
        return out
    if isinstance(value, dict):
        return []
    return [value]


def extract_values(payload: Any, path: str) -> list[Any]:
    """按 ``files[].name`` / ``data.items[].id`` 提取标量值。"""
    current: list[Any] = [payload]
    for raw in path.split("."):
        part = raw.strip()
        if not part:
            continue
        is_array = part.endswith("[]")
        key = part[:-2] if is_array else part
        nxt: list[Any] = []
        for item in current:
            if not isinstance(item, dict) or key not in item:
                continue
            value = item[key]
            if is_array:
                if isinstance(value, list):
                    nxt.extend(value)
            else:
                nxt.append(value)
        current = nxt
    return [item for value in current for item in _flatten(value) if item is not None]


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def _page_blob(scope) -> str:
    chunks: list[str] = []
    try:
        chunks.append(scope.inner_text(timeout=5000))
    except Exception:
        pass
    try:
        attrs = scope.locator("[alt], [title], [src]").evaluate_all(
            """(els) => els.map((el) => [el.getAttribute('alt'), el.getAttribute('title'), el.getAttribute('src')].filter(Boolean).join(' '))"""
        )
        chunks.extend(str(x) for x in (attrs or []))
    except Exception:
        pass
    return _norm("\n".join(chunks))


def _matches_api(url: str, rule: ConsumptionRule, method: str | None = None) -> bool:
    if method and method.upper() != "GET":
        return False
    if isinstance(rule.api, re.Pattern):
        return bool(rule.api.search(url))
    return str(rule.api) in url


def expect_api_consumed(page, rule: ConsumptionRule, action, timeout: int = 20_000) -> dict:
    """执行 action，读取匹配响应，并断言字段值出现在页面 scope 内。"""
    with page.expect_response(
        lambda response: _matches_api(response.url, rule, response.request.method),
        timeout=timeout,
    ) as response_info:
        action()
    response = response_info.value
    if not response.ok:
        raise AssertionError(f"对账接口失败：{response.request.method} {response.url} → HTTP {response.status}")
    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"对账接口返回非 JSON：{response.url}: {exc}") from exc

    scope = page.locator(rule.scope) if rule.scope else page.locator("body")
    if rule.scope:
        scope.first.wait_for(state="visible", timeout=timeout)
    blob = _page_blob(scope)

    checked: list[tuple[str, Any, bool]] = []
    for field in rule.fields:
        for value in extract_values(payload, field):
            if isinstance(value, (dict, list)):
                continue
            rendered = _norm(value) in blob
            checked.append((field, value, rendered))

    if not checked:
        raise AssertionError(
            f"接口没有可对账字段：{response.request.method} {response.url} "
            f"fields={rule.fields} payload_keys={list(payload)[:10] if isinstance(payload, dict) else type(payload).__name__}"
        )

    missing = [(field, value) for field, value, rendered in checked if not rendered]
    required = min(max(1, rule.require), len(checked))
    if len(checked) - len(missing) < required:
        details = "、".join(f"{field}={value!r}" for field, value in missing[:12])
        raise AssertionError(
            f"接口有数据但页面没有消费：{response.request.method} {response.url}\n"
            f"字段：{', '.join(rule.fields)}\n"
            f"未出现在页面上的值（{len(missing)}/{len(checked)}）：{details}\n"
            f"页面可见文本摘要：{blob[:1200]}"
        )

    if rule.image_selector:
        expect_image_loaded(page, rule.image_selector, timeout=timeout)

    return payload


def expect_image_loaded(page, selector: str, timeout: int = 20_000) -> None:
    page.wait_for_function(
        """(sel) => {
            const img = document.querySelector(sel);
            return !!img && img.complete && img.naturalWidth > 0;
        }""",
        arg=selector,
        timeout=timeout,
    )


def dump_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
