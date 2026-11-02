#!/usr/bin/env python3
"""自动检查所有工具运行环境，安装缺失的 Python 包。

扫描范围：内置工具 `resources/tools/implementations/`
         + 私有工具 `resources-private/tools/implementations/`（导入 / 新建的工具都在这里）

用法:
    python scripts/check_tool_env.py                 # 检查并交互式安装
    python scripts/check_tool_env.py --auto          # 自动安装所有缺失包
    python scripts/check_tool_env.py --dry-run       # 仅检查，不安装
    python scripts/check_tool_env.py --tool <id>     # 只处理某个工具
    SOTABAND_PIP_TIMEOUT=1800 python scripts/check_tool_env.py --auto   # 大包放宽超时

退出码：0 = 依赖齐备或安装全部成功；1 = 有安装失败。
"""

import argparse
import ast
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Windows 控制台默认 GBK，emoji / 中文会直接抛 UnicodeEncodeError
if sys.platform == "win32":
    for _stream in ("stdout", "stderr"):
        try:
            getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# 内置模块 + 标准库（Python 3.12）
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
    "_thread", "_io", "_collections_abc", "__future__",
}

# 项目内部模块（不属于第三方依赖）
PROJECT_MODULES = {"app", "core", "resources", "storage", "config", "scripts"}

# pip 包名映射（import 名 → pip 包名），与 core.deps 冲突时以 core.deps 为准
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

# 单个包安装超时（秒）。torch 这类大 wheel 300s 常见不够。
INSTALL_TIMEOUT = int(os.environ.get("SOTABAND_PIP_TIMEOUT", "900"))


# ── 路径 / 解释器 ────────────────────────────────────────────

def get_project_root() -> Path:
    return PROJECT_ROOT


def private_root() -> Path:
    """私有资源根：与 project paths.py 的规则保持一致。"""
    env = os.environ.get("SOTABAND_PRIVATE_RESOURCES", "").strip()
    return Path(env) if env else PROJECT_ROOT.parent / "resources-private"


def get_tool_dirs() -> List[Tuple[Path, str]]:
    """返回 [(实现目录, 来源标签)]：内置 + 私有。

    只扫内置目录是历史遗留问题——新导入 / 新建的工具都落在私有根，
    所以这里必须两个根都扫，且同名时私有覆盖内置（与 registry 语义一致）。
    """
    return [
        (PROJECT_ROOT / "resources" / "tools" / "implementations", "内置"),
        (private_root() / "tools" / "implementations", "私有"),
    ]


def get_python_exe() -> str:
    """工具实际使用的解释器：项目 venv（跨平台），否则当前解释器。"""
    for rel in ("venv/bin/python", "venv/Scripts/python.exe"):
        p = PROJECT_ROOT / rel
        if p.exists():
            return str(p)
    return sys.executable


# ── 依赖提取 ────────────────────────────────────────────────

def extract_imports(file_path: Path) -> List[str]:
    """从 Python 文件中提取顶层 import 的模块名"""
    try:
        # utf-8-sig：Windows 编辑器保存的 .py 常带 BOM，普通 utf-8 读出来的
        # 首字符是 \ufeff，ast.parse 会直接 SyntaxError → 依赖被漏检。
        tree = ast.parse(file_path.read_text(encoding="utf-8-sig"))
    except SyntaxError:
        return []

    imports: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split(".")[0])
    return imports


def is_std_lib(module_name: str) -> bool:
    return module_name in STD_LIBS


def is_project_module(module_name: str) -> bool:
    return module_name in PROJECT_MODULES


def check_module_installed(module_name: str, python_exe: str) -> bool:
    try:
        result = subprocess.run(
            [python_exe, "-c", f"import {module_name}"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_pip_name(import_name: str) -> str:
    """import 名 → pip 包名。优先用 core.deps 的统一映射表。"""
    try:
        from core.deps import pip_name_for
        mapped = pip_name_for(import_name)
        if mapped and mapped != import_name:
            return mapped
    except Exception:
        pass
    return PIP_NAME_MAP.get(import_name, import_name)


def install_package(pip_name: str, python_exe: str) -> Tuple[bool, str]:
    print(f"  📦 安装 {pip_name} ...", end=" ", flush=True)
    try:
        result = subprocess.run(
            [python_exe, "-m", "pip", "install", pip_name],
            capture_output=True, text=True, timeout=INSTALL_TIMEOUT,
        )
        if result.returncode == 0:
            print("✅")
            return True, ""
        lines = [ln for ln in (result.stderr or "").strip().split("\n") if ln.strip()]
        err = lines[-1] if lines else (result.stderr or "")[:200]
        print(f"❌ {err}")
        return False, err
    except subprocess.TimeoutExpired:
        print(f"❌ 超时（>{INSTALL_TIMEOUT}s，可用 SOTABAND_PIP_TIMEOUT 放宽）")
        return False, f"安装超时（{INSTALL_TIMEOUT}s）"
    except Exception as e:
        print(f"❌ {e}")
        return False, str(e)


# ── 主流程 ──────────────────────────────────────────────────

def collect_tools(only_tool: str = "") -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """返回 ({工具 id: [第三方 import 名]}, {工具 id: 来源})"""
    tool_deps: Dict[str, List[str]] = {}
    origins: Dict[str, str] = {}

    for impl_dir, label in get_tool_dirs():
        if not impl_dir.is_dir():
            continue
        for tool_path in sorted(impl_dir.iterdir()):
            if not tool_path.is_dir():
                continue
            tool_file = tool_path / "tool.py"
            if not tool_file.exists():
                continue
            tool_id = tool_path.name
            if only_tool and tool_id != only_tool:
                continue

            external: List[str] = []
            for imp in extract_imports(tool_file):
                if not is_std_lib(imp) and not is_project_module(imp):
                    if imp not in external:
                        external.append(imp)

            # 私有后扫，同名覆盖内置
            tool_deps[tool_id] = external
            origins[tool_id] = label

    return tool_deps, origins


def check_and_install(
    auto: bool = False,
    dry_run: bool = False,
    only_tool: str = "",
) -> Dict[str, List[str]]:
    python_exe = get_python_exe()
    tool_deps, origins = collect_tools(only_tool)

    print("🔍 检查工具运行环境")
    print(f"   项目根目录: {PROJECT_ROOT}")
    print(f"   私有资源根: {private_root()}")
    print(f"   Python:     {python_exe}")
    for d, label in get_tool_dirs():
        exists = "✓" if d.is_dir() else "✗"
        print(f"   {label}工具目录 [{exists}]: {d}")
    print()

    if not tool_deps:
        print("❌ 没有找到任何工具（检查上面两个目录是否存在 tool.py）")
        return {}

    print("=" * 74)
    print(f"{'工具名称':<38} {'来源':<6} {'外部依赖'}")
    print("=" * 74)
    for name in sorted(tool_deps):
        deps_str = ", ".join(tool_deps[name]) if tool_deps[name] else "(无)"
        print(f"{name:<38} {origins.get(name, ''):<6} {deps_str}")
    print()

    all_external: List[str] = sorted({d for deps in tool_deps.values() for d in deps})
    print("=" * 60)
    print(f"{'模块名':<30} {'状态':<10} {'pip包名'}")
    print("=" * 60)

    missing: List[str] = []
    for dep in all_external:
        pip_name = get_pip_name(dep)
        if check_module_installed(dep, python_exe):
            print(f"{dep:<30} {'✅ 已安装':<10} {pip_name}")
        else:
            print(f"{dep:<30} {'❌ 缺失':<10} {pip_name}")
            missing.append(dep)
    print()

    if not missing:
        print("🎉 所有工具依赖已就绪！")
        return {}

    print(f"📋 共 {len(missing)} 个包需要安装（单包超时 {INSTALL_TIMEOUT}s）")
    print()

    if dry_run:
        print("🔍 仅检查模式，跳过安装。")
        return {}

    if not auto:
        try:
            answer = input("是否安装以上缺失的包？[Y/n] ").strip().lower()
        except EOFError:
            answer = "n"
        if answer and answer != "y":
            print("已取消。")
            return {}

    print()
    print("─" * 60)
    results: Dict[str, List[str]] = {"success": [], "failed": []}

    for dep in missing:
        pip_name = get_pip_name(dep)
        ok, _err = install_package(pip_name, python_exe)
        results["success" if ok else "failed"].append(pip_name)

    print()
    print("=" * 60)
    print("📊 安装结果")
    print(f"   成功: {len(results['success'])} 个")
    if results["failed"]:
        print(f"   失败: {len(results['failed'])} 个")
        for pkg in results["failed"]:
            print(f"     ❌ {pkg}")
        print()
        print("   失败多为网络问题（大 wheel 断流/超时），可尝试：")
        print("     · 配镜像：mkdir -p ~/.pip && printf '[global]\\nindex-url = https://mirrors.aliyun.com/pypi/simple/\\ntimeout = 120\\nretries = 5\\n' > ~/.pip/pip.conf")
        print("     · 大包单独装，例如 CPU 版 torch：")
        print("       venv/bin/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu")
        print("     · 放宽超时：SOTABAND_PIP_TIMEOUT=1800 python scripts/check_tool_env.py --auto")
    else:
        print("🎉 所有缺失包安装完成！")

    return results


def main():
    parser = argparse.ArgumentParser(description="检查并修复工具运行环境（内置 + 私有工具）")
    parser.add_argument("--auto", action="store_true", help="自动安装所有缺失包，无需确认")
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不安装")
    parser.add_argument("--tool", default="", help="只处理指定工具 id")
    args = parser.parse_args()

    results = check_and_install(auto=args.auto, dry_run=args.dry_run, only_tool=args.tool)
    if results.get("failed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
