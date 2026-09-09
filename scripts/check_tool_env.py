#!/usr/bin/env python3
"""自动检查所有工具运行环境，安装缺失的 Python 包。

用法:
    python scripts/check_tool_env.py            # 检查并交互式安装
    python scripts/check_tool_env.py --auto     # 自动安装所有缺失包
    python scripts/check_tool_env.py --dry-run  # 仅检查，不安装
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

# ── pip 包名映射（import 名 → pip 包名）──
PIP_NAME_MAP = {
    "cv2": "opencv-python-headless",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "bing_image_downloader": "bing-image-downloader",
    "volcenginesdkarkruntime": "volcengine-python-sdk[ark]",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "fitz": "PyMuPDF",
}


def get_project_root() -> Path:
    """获取项目根目录"""
    return Path(__file__).resolve().parent.parent


def get_tool_dir() -> Path:
    """获取工具实现目录"""
    return get_project_root() / "resources" / "tools" / "implementations"


def get_python_exe() -> str:
    """获取 Python 解释器路径"""
    # 优先用全局 venv
    venv_py = get_project_root() / "venv" / "bin" / "python"
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
    """检查模块是否已安装"""
    try:
        result = subprocess.run(
            [python_exe, "-c", f"import {module_name}"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_pip_name(import_name: str) -> str:
    """将 import 名转换为 pip 包名"""
    return PIP_NAME_MAP.get(import_name, import_name)


def install_package(pip_name: str, python_exe: str) -> Tuple[bool, str]:
    """安装 Python 包"""
    print(f"  📦 安装 {pip_name} ...", end=" ", flush=True)
    try:
        result = subprocess.run(
            [python_exe, "-m", "pip", "install", pip_name],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            print("✅")
            return True, ""
        else:
            # 提取最后一行错误
            lines = result.stderr.strip().split("\n")
            err = lines[-1] if lines else result.stderr[:200]
            print(f"❌ {err}")
            return False, err
    except subprocess.TimeoutExpired:
        print("❌ 超时")
        return False, "安装超时"
    except Exception as e:
        print(f"❌ {e}")
        return False, str(e)


def check_and_install(
    auto: bool = False,
    dry_run: bool = False,
) -> Dict[str, List[str]]:
    """检查所有工具并安装缺失的包"""
    tool_dir = get_tool_dir()
    python_exe = get_python_exe()

    if not tool_dir.exists():
        print(f"❌ 工具目录不存在: {tool_dir}")
        sys.exit(1)

    print(f"🔍 检查工具运行环境")
    print(f"   项目根目录: {get_project_root()}")
    print(f"   Python:     {python_exe}")
    print(f"   工具目录:   {tool_dir}")
    print()

    # 收集所有工具及其依赖
    tool_deps: Dict[str, List[str]] = {}
    all_external_deps: Set[str] = set()

    for tool_path in sorted(tool_dir.iterdir()):
        if not tool_path.is_dir():
            continue
        tool_file = tool_path / "tool.py"
        if not tool_file.exists():
            continue

        imports = extract_imports(tool_file)
        external = []
        for imp in imports:
            if not is_std_lib(imp) and not is_project_module(imp):
                external.append(imp)
                all_external_deps.add(imp)

        tool_deps[tool_path.name] = external

    # 显示每个工具的依赖
    print("=" * 60)
    print(f"{'工具名称':<40} {'外部依赖'}")
    print("=" * 60)

    for tool_name in sorted(tool_deps):
        deps = tool_deps[tool_name]
        deps_str = ", ".join(deps) if deps else "(无)"
        print(f"{tool_name:<40} {deps_str}")

    print()

    # 检查哪些已安装、哪些缺失
    print("=" * 60)
    print(f"{'模块名':<30} {'状态':<10} {'pip包名'}")
    print("=" * 60)

    missing: Set[str] = set()
    installed: Set[str] = set()

    for dep in sorted(all_external_deps):
        ok = check_module_installed(dep, python_exe)
        pip_name = get_pip_name(dep)
        if ok:
            print(f"{dep:<30} {'✅ 已安装':<10} {pip_name}")
            installed.add(dep)
        else:
            print(f"{dep:<30} {'❌ 缺失':<10} {pip_name}")
            missing.add(dep)

    print()

    if not missing:
        print("🎉 所有工具依赖已就绪！")
        return {}

    # 安装缺失的包
    print(f"📋 共 {len(missing)} 个包需要安装")
    print()

    if dry_run:
        print("🔍 仅检查模式，跳过安装。")
        return {}

    if not auto:
        answer = input("是否安装以上缺失的包？[Y/n] ").strip().lower()
        if answer and answer != "y":
            print("已取消。")
            return {}

    print()
    print("─" * 60)
    results = {"success": [], "failed": []}

    for dep in sorted(missing):
        pip_name = get_pip_name(dep)
        ok, err = install_package(pip_name, python_exe)
        if ok:
            results["success"].append(pip_name)
        else:
            results["failed"].append(pip_name)

    print()
    print("=" * 60)
    print("📊 安装结果")
    print(f"   成功: {len(results['success'])} 个")
    if results["failed"]:
        print(f"   失败: {len(results['failed'])} 个")
        for pkg in results["failed"]:
            print(f"     ❌ {pkg}")

    if not results["failed"]:
        print("🎉 所有缺失包安装完成！")

    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="检查并修复工具运行环境")
    parser.add_argument(
        "--auto", action="store_true",
        help="自动安装所有缺失包，无需确认",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅检查，不安装",
    )
    args = parser.parse_args()

    check_and_install(auto=args.auto, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
