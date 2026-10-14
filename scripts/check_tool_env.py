#!/usr/bin/env python3
"""检查已有工具的 Python 依赖；空资源库正常跳过，不负责安装平台本身。"""

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
# 与后端一致，先读取 .env，再解析 SOTABAND_PRIVATE_RESOURCES。
import config.settings  # noqa: E402,F401
from core.executor.tool_environment import discover_tools, implementation_dirs, resolve_python  # noqa: E402

PIP_NAME_MAP = {
    "cv2": "opencv-python-headless", "PIL": "Pillow", "sklearn": "scikit-learn",
    "bing_image_downloader": "bing-image-downloader",
    "volcenginesdkarkruntime": "volcengine-python-sdk[ark]", "yaml": "PyYAML",
    "dotenv": "python-dotenv", "fitz": "PyMuPDF",
}
PROJECT_MODULES = {"app", "core", "resources", "storage", "config"}


def _run(python_exe: str, args: list[str], *, timeout: int = 30):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [python_exe, *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        cwd=PROJECT_ROOT, env=env,
    )


def get_std_libs(python_exe: str) -> set[str]:
    """按工具实际 Python 版本识别标准库，避免把旧版清单写死。"""
    result = _run(python_exe, ["-c", (
        "import sys,json; print(json.dumps(sorted(set(sys.stdlib_module_names)"
        " | set(sys.builtin_module_names))))"
    )])
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Python 环境无法启动")
    return set(json.loads(result.stdout.strip()))


def extract_imports(file_path: Path) -> list[str]:
    """提取绝对 import；语法错误向上传递，不能当作无依赖。"""
    tree = ast.parse(file_path.read_text(encoding="utf-8-sig"), filename=str(file_path))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imports.add(node.module.split(".")[0])
    return sorted(imports)


def collect_dependencies(tool_dir: Path, std_libs: set[str]) -> set[str]:
    """本地辅助模块无需 pip，但其自身的第三方 import 仍需检查。"""
    dependencies = set()
    files = [tool_dir / "tool.py"]
    visited = set()
    while files:
        file_path = files.pop()
        if file_path in visited:
            continue
        visited.add(file_path)
        for module in extract_imports(file_path):
            local_file = tool_dir / f"{module}.py"
            local_package = tool_dir / module
            if local_file.is_file():
                files.append(local_file)
            elif local_package.is_dir():
                files.extend(local_package.rglob("*.py"))
            elif module not in std_libs and module not in PROJECT_MODULES:
                dependencies.add(module)
    return dependencies


def check_module(module_name: str, python_exe: str) -> tuple[str, str]:
    """区分未安装与已安装但无法导入；后者不能误报为安装成功。"""
    probe = """import importlib,json,sys
try:
    importlib.import_module(sys.argv[1])
    result = {"status": "ready", "error": ""}
except ModuleNotFoundError as exc:
    status = "missing" if exc.name == sys.argv[1] else "broken"
    result = {"status": status, "error": str(exc)}
except Exception as exc:
    result = {"status": "broken", "error": str(exc)}
print(json.dumps(result, ensure_ascii=False))
"""
    try:
        result = _run(python_exe, ["-c", probe, module_name])
        if result.returncode:
            return "broken", result.stderr.strip() or "导入进程异常退出"
        data = json.loads(result.stdout.strip().splitlines()[-1])
        return data["status"], data["error"]
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as exc:
        return "broken", str(exc)


def get_pip_name(import_name: str) -> str:
    return PIP_NAME_MAP.get(import_name, import_name)


def install_package(pip_name: str, python_exe: str) -> tuple[bool, str]:
    print(f"  安装 {pip_name} ...", flush=True)
    try:
        result = _run(python_exe, ["-m", "pip", "install", pip_name], timeout=300)
        if result.returncode == 0:
            return True, ""
        return False, (result.stderr.strip() or result.stdout.strip())[-1000:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def check_and_install(auto: bool = False, dry_run: bool = False) -> dict[str, list[str]]:
    results = {"success": [], "failed": [], "missing": []}
    print("检查已有工具的 Python 运行环境")
    for directory in implementation_dirs():
        print(f"  扫描目录: {directory}")
    tools = discover_tools()
    if not tools:
        print("尚无工具，跳过工具依赖检查。平台依赖请使用 python -m pip install -e . 安装。")
        return results

    environments: dict[str, set[str]] = {}
    stdlib_cache: dict[str, set[str]] = {}
    for tool_id, directory in sorted(tools.items()):
        python = resolve_python(directory)
        print(f"\n工具: {tool_id}\n  实现: {directory}\n  Python: {python}")
        try:
            if python not in stdlib_cache:
                stdlib_cache[python] = get_std_libs(python)
            deps = collect_dependencies(directory, stdlib_cache[python])
            environments.setdefault(python, set()).update(deps)
            print(f"  第三方依赖: {', '.join(sorted(deps)) or '(无)'}")
        except (OSError, ValueError, SyntaxError, RuntimeError, subprocess.TimeoutExpired) as exc:
            message = f"{tool_id}: {exc}"
            results["failed"].append(message)
            print(f"  检查失败: {message}")

    for python, deps in environments.items():
        print(f"\n检查环境: {python}")
        missing = []
        for dep in sorted(deps):
            status, error = check_module(dep, python)
            print(f"  {dep}: {status}" + (f" — {error}" if error else ""))
            if status == "missing":
                missing.append(dep)
            elif status != "ready":
                results["failed"].append(f"{python}: {dep}: {error}")
        if not missing:
            continue
        install = auto and not dry_run
        if not auto and not dry_run:
            try:
                install = input("是否向上述环境安装缺失包？[Y/n] ").strip().lower() in ("", "y")
            except EOFError:
                install = False
        for dep in missing:
            label = f"{python}: {dep}"
            if not install:
                results["missing"].append(label)
                continue
            ok, error = install_package(get_pip_name(dep), python)
            if ok:
                status, error = check_module(dep, python)
                ok = status == "ready"
            if ok:
                results["success"].append(label)
                print(f"  {dep}: 安装后导入验证通过")
            else:
                results["failed"].append(f"{label}: {error}")
                print(f"  {dep}: 修复失败 — {error}")

    if results["failed"] or results["missing"]:
        print(f"\n环境未就绪：失败 {len(results['failed'])} 项，未安装 {len(results['missing'])} 项。")
    else:
        print("\n工具的 Python 依赖已就绪。")
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auto", action="store_true", help="自动安装缺失的包")
    parser.add_argument("--dry-run", action="store_true", help="只检查；缺失或损坏时返回非零退出码")
    args = parser.parse_args(argv)
    results = check_and_install(auto=args.auto, dry_run=args.dry_run)
    return 1 if results["failed"] or results["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
