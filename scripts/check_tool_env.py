#!/usr/bin/env python3
"""检查所有工具运行环境，用 uv 初始化/重建工具独立 .venv。

每个工具的第三方依赖装在实现目录的 .venv 里（uv 全局缓存硬链接去重），
依赖清单固化在 requirements.txt；无第三方依赖或共享环境已满足的工具不动。

用法:
    python scripts/check_tool_env.py            # 检查并交互式处理
    python scripts/check_tool_env.py --auto     # 自动初始化/重建全部缺失环境
    python scripts/check_tool_env.py --dry-run  # 仅检查，不改动
"""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

# ── 标准库列表（Python 3.12）──
STD_LIBS = {
    "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio",
    "asyncore", "atexit", "audioop", "base64", "bdb", "binascii", "bisect",
    "builtins", "bz2", "calendar", "cgi", "cgitb", "chunk", "cmath", "cmd",
    "code", "codecs", "codeop", "collections", "colorsys", "compileall",
    "concurrent", "configparser", "contextlib", "contextvars", "copy",
    "copyreg", "cProfile", "crypt", "csv", "ctypes", "curses", "dataclasses",
    "datetime", "dbm", "decimal", "difflib", "dis", "distutils", "doctest",
    "email", "encodings", "enum", "errno", "faulthandler", "fcntl",
    "filecmp", "fileinput", "fnmatch", "formatter", "fractions", "ftplib",
    "functools", "gc", "getopt", "getpass", "gettext", "glob", "graphlib",
    "grp", "gzip", "hashlib", "heapq", "hmac", "html", "http", "idlelib",
    "imaplib", "imghdr", "imp", "importlib", "inspect", "io", "ipaddress",
    "itertools", "json", "keyword", "lib2to3", "linecache", "locale",
    "logging", "lzma", "mailbox", "mailcap", "marshal", "math", "mimetypes",
    "mmap", "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
    "numbers", "operator", "optparse", "os", "ossaudiodev", "parser",
    "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil",
    "platform", "plistlib", "poplib", "posix", "posixpath", "pprint",
    "profile", "pstats", "pty", "pwd", "py_compile", "pyclbr",
    "pydoc", "queue", "quopri", "random", "re", "readline", "reprlib",
    "resource", "rlcompleter", "runpy", "sched", "secrets", "select",
    "selectors", "shelve", "shlex", "shutil", "signal", "site", "smtpd",
    "smtplib", "sndhdr", "socket", "socketserver", "sqlite3", "ssl",
    "stat", "statistics", "string", "stringprep", "struct", "subprocess",
    "sunau", "symtable", "sys", "sysconfig", "syslog", "tabnanny",
    "tarfile", "telnetlib", "tempfile", "termios", "test", "textwrap",
    "threading", "time", "timeit", "tkinter", "token", "tokenize",
    "tomllib", "trace", "traceback", "tracemalloc", "tty", "turtle",
    "turtledemo", "types", "typing", "unicodedata", "unittest", "urllib",
    "uu", "uuid", "venv", "warnings", "wave", "weakref", "webbrowser",
    "winreg", "winsound", "wsgiref", "xdrlib", "xml", "xmlrpc",
    "zipapp", "zipfile", "zipimport", "zlib",
    # 内置模块
    "_thread", "_io", "_collections_abc", "__future__",
}


def get_project_root() -> Path:
    """获取项目根目录"""
    return Path(__file__).resolve().parent.parent


def get_tool_dir() -> Path:
    """获取工具实现目录"""
    return get_project_root() / "resources" / "tools" / "implementations"


def tool_impl_dirs() -> List[Path]:
    """内置 + 私有两个资源根下的工具实现目录"""
    dirs = []
    try:
        sys.path.insert(0, str(get_project_root()))
        from core.resource import paths as _paths
        for space in _paths.space_dirs("tools"):
            d = space / "implementations"
            if d.exists():
                dirs.append(d)
    except Exception:
        d = get_tool_dir()
        if d.exists():
            dirs.append(d)
    return dirs


def get_python_exe() -> str:
    """获取 Python 解释器路径"""
    # 优先用全局 venv（兼容 Windows 的 Scripts\python.exe 与 Unix 的 bin/python）
    root = get_project_root()
    for rel in ("Scripts/python.exe", "bin/python"):
        venv_py = root / "venv" / rel
        if venv_py.exists():
            return str(venv_py)
    return sys.executable


def extract_imports(file_path: Path) -> List[str]:
    """从 Python 文件中提取所有顶层 import 的模块名"""
    try:
        tree = ast.parse(file_path.read_text(encoding='utf-8'))
    except SyntaxError:
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # 取顶层模块名
                top = alias.name.split(".")[0]
                imports.append(top)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                imports.append(top)
    return imports


def is_std_lib(module_name: str) -> bool:
    """判断是否为标准库"""
    return module_name in STD_LIBS


def is_project_module(module_name: str) -> bool:
    """判断是否为项目内部模块"""
    project_modules = {"app", "core", "resources", "storage", "config"}
    return module_name in project_modules


def check_module_installed(module_name: str, python_exe: str) -> bool:
    """检查模块是否已安装（仅探测存在性，不执行模块代码）。

    用 importlib.util.find_spec 代替真实 import：torch 等大包冷启动
    import 可能超过超时时间，导致已安装的包被误判为缺失而重复安装。
    """
    code = (
        "import importlib.util, sys; "
        "sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)"
    )
    try:
        result = subprocess.run(
            [python_exe, "-c", code, module_name],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_and_install(dry_run: bool = False) -> Dict[str, List[str]]:
    """检查所有工具的运行环境，必要时用 uv 初始化/重建工具独立 .venv。

    单一方案：环境操作全部经 uv。已有 requirements.txt 的工具据此重建；
    无 requirements.txt 但缺依赖的历史工具，引导创建独立 .venv 并装入
    全部第三方依赖，随后 freeze 固化。依赖已齐备的工具（独立 .venv 或
    共享项目 venv 均可运行）不做任何改动。
    """
    root = get_project_root()
    sys.path.insert(0, str(root))
    from core.resource import deps as _deps

    python_exe = get_python_exe()
    impl_dirs = tool_impl_dirs()
    if not impl_dirs:
        print(f"❌ 工具实现目录不存在")
        sys.exit(1)

    print("🔍 检查工具运行环境（uv 管理的工具独立 .venv）")
    print(f"   项目根目录: {root}")
    print(f"   共享回退解释器: {python_exe}")
    print()

    rows: List[Tuple[str, str, str]] = []   # (工具, 状态, 环境)
    failed: List[str] = []
    pending: List[Tuple[Path, str, List[str]]] = []  # (目录, 动作, specs)

    for impl_root in impl_dirs:
        for tool_path in sorted(impl_root.iterdir()):
            if not tool_path.is_dir() or not (tool_path / "tool.py").exists():
                continue
            req = _deps.read_requirements(tool_path)
            has_venv = _deps.venv_exists(tool_path)

            if req:
                if not has_venv:
                    pending.append((tool_path, "按 requirements.txt 重建", []))
                    continue
                if dry_run:
                    rows.append((tool_path.name, "✅ 依赖就绪", "独立 .venv"))
                    continue
                uv = _deps.uv_bin()
                venv_py = str(_deps.venv_python_path(tool_path))
                r = subprocess.run(
                    [uv, "pip", "install", "-r", str(_deps.requirements_file(tool_path)),
                     "--python", venv_py],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    env=_deps.uv_env(), timeout=1800,
                )
                if r.returncode == 0:
                    rows.append((tool_path.name, "✅ 依赖就绪", "独立 .venv"))
                else:
                    rows.append((tool_path.name, "❌ 依赖不满足", "独立 .venv"))
                    failed.append(tool_path.name)
                continue

            imports = extract_imports(tool_path / "tool.py")
            ext = sorted({i for i in imports if not is_std_lib(i) and not is_project_module(i)})
            if not ext:
                rows.append((tool_path.name, "✅ 无第三方依赖", "-"))
                continue
            probe_py = str(_deps.venv_python_path(tool_path)) if has_venv else python_exe
            missing = [m for m in ext if not check_module_installed(m, probe_py)]
            if not missing:
                rows.append((tool_path.name, "✅ 依赖就绪", "独立 .venv" if has_venv else "共享环境"))
            else:
                pending.append((tool_path, "初始化独立 .venv",
                                sorted({_deps.pip_name(m) for m in ext})))

    print("=" * 72)
    print(f"{'工具名称':<40} {'状态':<14} 环境")
    print("=" * 72)
    for name, status, env in rows:
        print(f"{name:<40} {status:<14} {env}")
    print()

    if pending:
        if dry_run:
            print(f"🔍 以下 {len(pending)} 个工具需要 uv 初始化/重建（--auto 执行）:")
            for tool_path, action, _ in pending:
                print(f"   • {tool_path.name}: {action}")
            print()
        else:
            print(f"📋 初始化/重建 {len(pending)} 个工具的独立环境")
            print("─" * 72)
            for tool_path, action, specs in pending:
                print(f"  🔧 {tool_path.name}（{action}）...", end=" ", flush=True)
                try:
                    _deps.create_tool_venv(tool_path, specs=specs or None,
                                           requirements=not specs)
                    _deps.freeze_requirements(tool_path)
                    print("✅")
                    rows.append((tool_path.name, "✅ 依赖就绪", "独立 .venv"))
                except Exception as e:
                    print(f"❌ {e}")
                    failed.append(tool_path.name)
            print()

    if failed:
        print(f"⚠ {len(failed)} 个工具环境处理失败: {', '.join(failed)}")
        return {"success": [], "failed": failed}

    print("🎉 所有工具运行环境就绪！")
    return {}


def main():
    import argparse

    parser = argparse.ArgumentParser(description="检查并用 uv 修复工具运行环境")
    parser.add_argument(
        "--auto", action="store_true",
        help="初始化/重建缺失的工具环境（默认行为，保留兼容）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅检查，不改动环境",
    )
    args = parser.parse_args()

    check_and_install(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
