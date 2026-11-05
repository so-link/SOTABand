"""工具迁移器（SOTABand 内置初始化工具）

把一个工具（定义 MD + 实现目录 + 注册条目）打包成 .sotatool.zip，
或把这样一个包导入当前环境，导入后立即可用。

不依赖任何第三方库：只用标准库 + 平台 core 层。
"""

import base64
import json
import sys
from pathlib import Path
from typing import Any, Dict


def _detect_project_root() -> Path:
    """定位项目根（位置无关）：优先从 sys.path 反查，再从本文件向上找。"""
    for entry in list(sys.path):
        try:
            cand = Path(entry)
            if (cand / "core").is_dir() and (cand / "app").is_dir():
                return cand
        except Exception:
            continue
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "core").is_dir() and (parent / "app").is_dir():
            return parent
    return here.parent.parent.parent.parent.parent


_PROJECT_ROOT = _detect_project_root()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

ACTIONS = ("export", "import", "list", "info")


def _fail(message: str, **extra) -> Dict[str, Any]:
    data = {"action": extra.pop("action", "")}
    data.update(extra)
    return {
        "status": "failed",
        "output_format": "text",
        "message": message,
        "data": data,
    }


def _ok(message: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "success",
        "output_format": "text",
        "message": message,
        "data": data,
    }


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def execute(**kwargs) -> Dict[str, Any]:
    """导出 / 导入工具迁移包。

    Args:
        action (str): export | import | list | info
        tool_id (str): 导出时的工具 id
        bundle_path (str): 导入时的包路径（绝对路径，或本人导出目录下的文件名）
        bundle_base64 (str): 导入时的包内容（base64，便于跨机器粘贴）
        overwrite (bool): 导入时同 id 已存在是否覆盖
        include_tests (bool): 导出时是否带 tests 目录（默认 True）
        include_assets (bool): 导出时是否带二进制资产（默认 True）
    """
    action = str(kwargs.get("action") or "info").strip().lower()
    if action not in ACTIONS:
        return _fail(f"未知 action: {action}（可选 {', '.join(ACTIONS)}）", action=action)

    try:
        from core.migration import tool_bundle
    except Exception as e:  # 理论上不会发生：core 在项目根内
        return _fail(f"无法加载迁移模块 core.migration: {e}", action=action)

    # ── info ──────────────────────────────────────────────
    if action == "info":
        return _ok("工具迁移器就绪", {
            "action": action,
            "bundle_format": tool_bundle.BUNDLE_FORMAT,
            "format_version": tool_bundle.BUNDLE_VERSION,
            "suffix": tool_bundle.BUNDLE_SUFFIX,
            "export_dir": str(tool_bundle.export_dir()),
            "project_root": str(_PROJECT_ROOT),
        })

    # ── list ──────────────────────────────────────────────
    if action == "list":
        items = tool_bundle.list_bundles()
        lines = [
            f"{b.get('file_name')}  [{b.get('tool_id') or '?'} {b.get('version') or ''}]"
            for b in items
        ]
        msg = "导出目录为空" if not items else f"共 {len(items)} 个迁移包：\n" + "\n".join(lines)
        return _ok(msg, {"action": action, "export_dir": str(tool_bundle.export_dir()),
                         "bundles": items, "total": len(items)})

    # ── export ────────────────────────────────────────────
    if action == "export":
        tool_id = str(kwargs.get("tool_id") or "").strip()
        if not tool_id:
            return _fail("action=export 需要 tool_id", action=action)
        try:
            r = tool_bundle.export_tool(
                tool_id,
                include_tests=_as_bool(kwargs.get("include_tests"), True),
                include_assets=_as_bool(kwargs.get("include_assets"), True),
            )
        except ValueError as e:
            return _fail(f"导出失败: {e}", action=action, tool_id=tool_id)
        except Exception as e:
            return _fail(f"导出异常: {e}", action=action, tool_id=tool_id)

        size_kb = r["size"] / 1024
        skip_note = ""
        if r.get("skipped"):
            skip_note = f"；跳过 {len(r['skipped'])} 个大文件"
        dep_note = ""
        if r.get("dependencies"):
            dep_note = f"；依赖需在目标环境安装: {', '.join(r['dependencies'])}"
        return _ok(
            f"已导出工具 {r['tool_id']} → {r['bundle_path']}"
            f"（{size_kb:.1f} KB，{r['file_count']} 个文件{skip_note}{dep_note}）",
            {"action": action, **r},
        )

    # ── import ────────────────────────────────────────────
    overwrite = _as_bool(kwargs.get("overwrite"), False)
    raw = kwargs.get("bundle_base64")
    path = str(kwargs.get("bundle_path") or "").strip()

    try:
        if raw:
            try:
                content = base64.b64decode(str(raw), validate=True)
            except Exception as e:
                return _fail(f"bundle_base64 解码失败: {e}", action=action)
            r = tool_bundle.import_tool(None, content, overwrite)
        elif path:
            p = Path(path)
            if not p.is_absolute():
                # 相对路径先在本人导出目录（<USER_HOME>/<uid>/exports）下找，再按当前工作目录找
                cand = tool_bundle.export_dir() / path
                p = cand if cand.is_file() else p
            r = tool_bundle.import_tool(p, None, overwrite)
        else:
            return _fail("action=import 需要 bundle_path 或 bundle_base64", action=action)
    except ValueError as e:
        return _fail(f"导入失败: {e}", action=action)
    except Exception as e:
        return _fail(f"导入异常: {e}", action=action)

    return _ok(
        f"已导入工具 {r['tool_id']}（{r['name']} v{r['version']}，"
        f"{r['file_count']} 个文件）→ {r['impl_dir']}；{r['note']}",
        {"action": action, **r},
    )
