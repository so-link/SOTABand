"""工具实现和 Python 环境的统一定位，检查、调试和执行共用。"""

import os
import sys
from pathlib import Path

from core.resource import paths


def implementation_dirs() -> list[Path]:
    return [root / "tools" / "implementations" for root in paths.resource_roots()]


def discover_tools() -> dict[str, Path]:
    """收集含 tool.py 的实现；私有同 ID 实现覆盖内置实现。只读，不创建目录。"""
    tools: dict[str, Path] = {}
    for base in implementation_dirs():
        if not base.is_dir():
            continue
        for directory in sorted(base.iterdir()):
            if directory.is_dir() and (directory / "tool.py").is_file():
                tools[directory.name] = directory
    return tools


def resolve_tool_dir(tool_id: str) -> Path:
    for base in reversed(implementation_dirs()):
        directory = base / tool_id
        if (directory / "tool.py").is_file():
            return directory
    return paths.BUILTIN_ROOT / "tools" / "implementations" / tool_id


def venv_python(venv_dir: Path, *, platform: str | None = None) -> Path | None:
    candidate = (venv_dir / "Scripts" / "python.exe" if (platform or os.name) == "nt"
                 else venv_dir / "bin" / "python")
    return candidate if candidate.is_file() else None


def resolve_python(tool_dir: Path | None = None) -> str:
    """工具 .venv → 项目 venv/.venv → 当前解释器，按操作系统识别路径。"""
    candidates = [tool_dir / ".venv"] if tool_dir is not None else []
    candidates.extend([paths.PROJECT_ROOT / "venv", paths.PROJECT_ROOT / ".venv"])
    for directory in candidates:
        python = venv_python(directory)
        if python is not None:
            return str(python)
    return sys.executable


def resolve_tool_python(tool_id: str) -> str:
    return resolve_python(resolve_tool_dir(tool_id))
