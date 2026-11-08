"""必填参数的平台统一兜底校验。

## 为什么放在平台层

历史生成 prompt 既没要求也没禁止模型自己写「缺少必填参数」校验，结果内置
54 个工具里约一半在 ``tool.py`` 内部硬编码了 ``if not kwargs.get("x")`` 拦截
（如 ``batch-ai-bbox-labeling`` / ``edit-image`` / ``helloword``）。这带来三个问题：

1. 必填语义散落在代码里 —— 界面上把参数从「必填」改成「可选」后，代码不认，
   仍然会拦，改了个寂寞；
2. 同一个缺参场景有 N 套文案、N 种行为（有的 return failed，有的 raise）；
3. 工具代码里混着类型校验与必填校验，重构时容易连带改坏。

因此改为：**生成期禁止自写缺参校验**（见 ``ToolBuilder._llm_generate`` 的
CRITICAL RULES），**执行期由本模块按 registry 的 ``param_meta`` 统一拦截**。
存量工具可通过 ``entry["enforce_required"] = false`` 单独关掉（默认开启）。

## 缺失口径

``None`` / 空串 / 纯空白 算缺失；``0``、``False``、空 list/dict **不算**缺失
（它们是有意义的取值）。
"""

from __future__ import annotations

from typing import Any


def is_missing(value: Any) -> bool:
    """该参数值是否视为「未提供」。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def missing_required_params(
    param_meta: list[dict] | None,
    params: dict | None,
    mode: str | None = None,
) -> list[dict]:
    """返回缺失的必填参数定义。

    Args:
        param_meta: registry 里的参数元数据（含 name / required / mode）
        params: 本次实际传入的参数
        mode: 多模式工具的当前模式；``None`` 时不过滤（与交互 Agent 现有逻辑一致）

    注意：``mode`` 本身是分发参数，不参与必填校验。
    """
    provided = params or {}
    missing: list[dict] = []
    for p in param_meta or []:
        name = (p.get("name") or "").strip()
        if not name or name == "mode":
            continue
        p_mode = (p.get("mode") or "").strip()
        if mode and p_mode and p_mode != mode:
            continue
        if not p.get("required"):
            continue
        if name not in provided or is_missing(provided.get(name)):
            missing.append(p)
    return missing


def format_missing_message(missing: list[dict]) -> str:
    """把缺失参数渲染成一句可读的中文提示。"""
    names = [(p.get("name") or "").strip() for p in missing]
    names = [n for n in names if n]
    if not names:
        return "缺少必填参数"
    return "缺少必填参数：" + "、".join(names)


def failure_response(missing: list[dict]) -> dict:
    """统一的缺参失败响应（形状与工具返回值一致，前端/Agent 无需特判）。"""
    return {
        "status": "failed",
        "output_format": "text",
        "message": format_missing_message(missing),
        "missing_params": [(p.get("name") or "").strip() for p in missing],
        "data": {},
    }


def find_entry(tool_id: str) -> dict | None:
    """按 id 查工具条目（内置 + 私有合并，纯读取，不建目录）。"""
    if not tool_id:
        return None
    from core.resource.registry.tool_registry import iter_tool_entries

    for e in iter_tool_entries():
        if str(e.get("id") or "").strip() == str(tool_id).strip():
            return e
    return None


def check_params(tool_id: str, params: dict | None, mode: str | None = None) -> dict | None:
    """执行前的必填校验。

    Returns:
        ``None`` 表示可以执行；否则返回应当直接回给调用方的失败响应。

    跳过校验的三种情况：
    - 工具尚未注册（沙箱测试 / 自动调试传的是草稿 id，签名里没有 param_meta）
    - 该工具显式关闭了校验（``enforce_required = false``）
    - 没有参数元数据（老条目 / 无参数工具）
    """
    entry = find_entry(tool_id)
    if entry is None:
        return None
    if entry.get("enforce_required", True) is False:
        return None
    meta = entry.get("param_meta") or []
    if not meta:
        return None

    current_mode = mode if mode is not None else (params or {}).get("mode")
    missing = missing_required_params(meta, params, current_mode)
    if not missing:
        return None
    return failure_response(missing)
