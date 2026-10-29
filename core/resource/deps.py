"""工具依赖环境管理 — uv 优先的 per-tool venv 方案

每个工具可以在自己的实现目录下拥有独立 ``.venv``（用 uv 创建；uv 的全局
内容寻址缓存让多个 venv 之间以硬链接共享同一份包文件，磁盘几乎零增量），
依赖声明固化在同目录 ``requirements.txt``（``uv pip freeze`` 精确 pin，
迁移机器或误删后可秒级重建）。

uv 不可用时（如未安装的 Windows 机器），调用方回退到既有
pip --target 安装到项目 venv 的行为，功能不劣化。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# 直连 PyPI 不通的部署环境默认走清华镜像；外部已设置则不覆盖
DEFAULT_UV_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"

# import 名 → pip 包名的全局唯一映射
# （此前 tool_routes 与 check_tool_env 各有一份且 cv2 互相打架，统一取 headless）
PIP_NAME_MAP = {
    "cv2": "opencv-python-headless",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "bing_image_downloader": "bing-image-downloader",
    "volcenginesdkarkruntime": "volcengine-python-sdk[ark]",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "fitz": "PyMuPDF",
    "dateutil": "python-dateutil",
    "serial": "pyserial",
    "OpenSSL": "pyopenssl",
    "jwt": "PyJWT",
    "win32com": "pywin32",
    "pkg_resources": "setuptools",
    "bs4": "beautifulsoup4",
}


def pip_name(import_name: str) -> str:
    """import 名转 pip 包名（无映射时原样返回）"""
    return PIP_NAME_MAP.get(import_name, import_name)


def uv_available() -> str | None:
    """返回 uv 可执行文件路径；不可用时返回 None。

    支持 ``SOTABAND_UV`` 环境变量显式指定路径；服务进程 PATH 不含
    ``~/.local/bin`` 时按常规安装位置兜底探测。
    """
    override = os.environ.get("SOTABAND_UV", "").strip()
    if override:
        return override if Path(override).exists() else None
    found = shutil.which("uv")
    if found:
        return found
    local = Path.home() / ".local" / "bin"
    for name in ("uv", "uv.exe"):
        cand = local / name
        if cand.exists():
            return str(cand)
    return None


def uv_env() -> dict:
    """uv 子进程环境：未配置任何索引时默认走清华镜像"""
    env = os.environ.copy()
    if not env.get("UV_DEFAULT_INDEX") and not env.get("UV_INDEX_URL"):
        env["UV_DEFAULT_INDEX"] = DEFAULT_UV_INDEX
    return env


def venv_python_path(tool_dir: Path) -> Path:
    """工具 .venv 解释器路径（存在哪个返回哪个，用于探测）"""
    unix = tool_dir / ".venv" / "bin" / "python"
    if unix.exists():
        return unix
    win = tool_dir / ".venv" / "Scripts" / "python.exe"
    if win.exists():
        return win
    return unix


def venv_exists(tool_dir: Path) -> bool:
    return venv_python_path(tool_dir).exists()


def requirements_file(tool_dir: Path) -> Path:
    return tool_dir / "requirements.txt"


def read_requirements(tool_dir: Path) -> list[str]:
    """读取 requirements.txt 中的包声明（忽略注释/空行）"""
    f = requirements_file(tool_dir)
    if not f.exists():
        return []
    lines = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def resolve_tool_dir(tool_id: str) -> Path:
    """按执行器一致的顺序（内置 → 私有）定位工具实现目录。

    均不存在时返回内置默认路径（与 ToolExecutor._resolve_impl_dir 兜底一致）。
    """
    from core.resource import paths

    for space in paths.space_dirs("tools"):
        d = space / "implementations" / tool_id
        if (d / "tool.py").exists():
            return d
    return paths.BUILTIN_ROOT / "tools" / "implementations" / tool_id


def create_tool_venv(tool_dir: Path, specs: list[str] | None = None,
                     requirements: bool = False, timeout: int = 1800) -> str:
    """用 uv 为工具创建独立 .venv 并安装依赖。

    先建在 ``.venv.tmp`` 再原子 rename 发布，避免执行器在半成品环境上执行。
    requirements=True 时从 requirements.txt 安装（重建场景），否则安装 specs。
    返回 venv python 路径；失败抛 RuntimeError（附 uv 输出尾部）。
    """
    uv = uv_available()
    if not uv:
        raise RuntimeError("uv 不可用")

    tmp = tool_dir / ".venv.tmp"
    final = tool_dir / ".venv"
    shutil.rmtree(tmp, ignore_errors=True)
    tool_dir.mkdir(parents=True, exist_ok=True)

    def _run(argv: list[str]):
        return subprocess.run(
            [uv] + argv, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            env=uv_env(), timeout=timeout,
        )

    r = _run(["venv", str(tmp)])
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"uv venv 失败: {(r.stderr or r.stdout)[-500:]}")

    tmp_py = str(tmp / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    if requirements:
        req = requirements_file(tool_dir)
        if req.exists():
            r = _run(["pip", "install", "-r", str(req), "--python", tmp_py])
            if r.returncode != 0:
                shutil.rmtree(tmp, ignore_errors=True)
                raise RuntimeError(f"uv pip install -r 失败: {(r.stdout or r.stderr)[-500:]}")
    elif specs:
        r = _run(["pip", "install", *specs, "--python", tmp_py])
        if r.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"uv pip install 失败: {(r.stdout or r.stderr)[-500:]}")

    if final.exists():
        shutil.rmtree(final, ignore_errors=True)
    os.replace(tmp, final)
    return str(venv_python_path(tool_dir))


def uv_pip_install_argv(uv: str, venv_python: str, spec: str) -> list[str]:
    """构造流式安装单个依赖的 uv 命令（调用方自行 Popen 采集输出）"""
    return [uv, "pip", "install", spec, "--python", venv_python]


def freeze_requirements(tool_dir: Path) -> None:
    """把工具 .venv 的已安装包精确 pin 写入 requirements.txt"""
    uv = uv_available()
    venv_py = venv_python_path(tool_dir)
    if not uv or not venv_py.exists():
        return
    try:
        r = subprocess.run(
            [uv, "pip", "freeze", "--python", str(venv_py)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=uv_env(), timeout=60,
        )
        if r.returncode == 0 and r.stdout.strip():
            requirements_file(tool_dir).write_text(r.stdout, encoding="utf-8")
    except Exception:
        pass
