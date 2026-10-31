"""依赖体检：工具代码的第三方 import → pip 包名 → 当前执行环境是否就绪。

单独成模块的原因：同一张「import 名 ↔ pip 包名」映射表被四处需要——
  * 依赖检测（/api/tool/{id}/check-deps、导入后体检）
  * 依赖安装（install_deps_stream 判断"是否已装"必须用 import 名）
放在 core 层避免各写一份，出现 `cv2` 与 `opencv-python` 互相误判。

映射方向说明：
  * 检测：`import PIL` → 缺 → 安装时得用 `pillow`
  * 安装前判断：依赖写的是 `opencv-python`，不能 `import opencv-python`
    （语法错误），必须反查成 `cv2`
"""

from __future__ import annotations

import re
import subprocess

# import 名 → pip 包名（小写匹配）
PIP_NAME_BY_IMPORT: dict[str, str] = {
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "serial": "pyserial",
    "OpenSSL": "pyopenssl",
    "jwt": "pyjwt",
    "win32com": "pywin32",
    "pkg_resources": "setuptools",
    "fit_zoo": "scipy",
}

# pip 包名（小写）→ import 名
IMPORT_NAME_BY_PIP: dict[str, str] = {
    pip.lower(): imp for imp, pip in PIP_NAME_BY_IMPORT.items()
}

# 依赖字符串里的版本/额外标记分隔符：numpy>=1.26 / pkg[extra] / pkg==1.0
_SPEC_SPLIT = re.compile(r"[<>=!~;\[\s,]")


def pip_name_for(module: str) -> str:
    """import 名 → pip 包名（无映射时原样返回）"""
    return PIP_NAME_BY_IMPORT.get(module, module)


def import_name_for(dep_spec: str) -> str:
    """pip 依赖串 → import 名（`opencv-python>=4.8` → `cv2`）"""
    base = _SPEC_SPLIT.split((dep_spec or "").strip())[0].strip()
    if not base:
        return ""
    return IMPORT_NAME_BY_PIP.get(base.lower(), base)


def extract_imports(code: str) -> list[str]:
    """从工具代码里静态提取第三方 import（复用构建器的 AST 实现）"""
    if not (code or "").strip():
        return []
    # 去掉 BOM：Windows 编辑器保存的文件常带 \ufeff，ast.parse 会直接报错，
    # 依赖被静默漏检（表现为「导入成功但依赖永远显示齐备」）。
    code = code.lstrip("\ufeff")
    try:
        from core.resource.builder.tool_builder import ToolCodeBuilder
        return ToolCodeBuilder._extract_imports(code)
    except Exception:
        return []


def probe_import(python_exe: str, module: str, timeout: int = 15) -> bool:
    """用指定解释器尝试 import，判断是否就绪。"""
    if not module:
        return True
    try:
        proc = subprocess.run(
            [python_exe, "-c", f"import {module}"],
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode == 0
    except Exception:
        # 探测本身失败（超时/解释器不可用）不计为缺失，避免误报
        return True


def check_dependencies(
    code: str,
    python_exe: str,
    env_label: str = "",
    tool_id: str = "",
) -> dict:
    """体检一段工具代码的依赖。返回结构前后端共用：

    {"tool_id", "deps", "missing", "install", "pairs", "python", "env"}
    * deps     : 提取到的 import 名
    * missing  : 缺失的 import 名
    * install  : 与 missing 一一对应的 pip 包名
    * pairs    : [{"import": "cv2", "pip": "opencv-python"}]
    """
    deps = extract_imports(code)
    missing: list[str] = []
    pairs: list[dict] = []
    for mod in deps:
        if probe_import(python_exe, mod):
            continue
        missing.append(mod)
        pairs.append({"import": mod, "pip": pip_name_for(mod)})
    return {
        "tool_id": tool_id,
        "deps": deps,
        "missing": missing,
        "install": [p["pip"] for p in pairs],
        "pairs": pairs,
        "python": python_exe,
        "env": env_label,
    }


def check_tool_dependencies(tool_id: str, code: str | None = None) -> dict:
    """体检一个已注册工具（自动定位它的实现代码与执行解释器）。"""
    from core.executor.tool_executor import ToolExecutor
    from core.resource.registry.tool_registry import ToolRegistry

    registry = ToolRegistry()
    if code is None:
        code = registry.read_code_text(tool_id)

    python_exe = ToolExecutor._get_python_exe(tool_id)
    lowered = python_exe.replace("\\", "/").lower()
    if "/implementations/" in lowered and "/.venv/" in lowered:
        env_label = "tool_venv"
    elif "/venv/" in lowered:
        env_label = "project_venv"
    else:
        env_label = "system"

    return check_dependencies(code, python_exe, env_label, tool_id)


__all__ = [
    "PIP_NAME_BY_IMPORT",
    "IMPORT_NAME_BY_PIP",
    "pip_name_for",
    "import_name_for",
    "extract_imports",
    "probe_import",
    "check_dependencies",
    "check_tool_dependencies",
]
