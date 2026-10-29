#!/usr/bin/env python3
"""
SOTABand 一键安装 / 启动脚本（Windows / macOS / Linux）

用法:
    python scripts/serve.py setup             # 仅安装环境（venv + Python 依赖 + 工具依赖 + 前端依赖）
    python scripts/serve.py dev               # 开发模式：安装(缺什么装什么) + 热更新前后端
    python scripts/serve.py start             # 部署模式：安装 + 构建前端 + 预览模式前后端（无热更新，暴露面小）
    python scripts/serve.py stop              # 停止由本脚本启动且仍在运行的服务

可选参数:
    --skip-tools        跳过工具依赖检查（scripts/check_tool_env.py，首次较慢，含 torch 等大包）
    --rebuild-frontend  强制重新构建前端（仅 start）
    --mirror <url>      为 venv 写入 pip 镜像源（如 https://pypi.tuna.tsinghua.edu.cn/simple）

端口与监听地址在仓库根目录 .env 中配置:
    SOTABAND_API_HOST / SOTABAND_API_PORT   后端（默认 0.0.0.0:8001）
    SOTABAND_WEB_PORT                       前端（开发/预览共用，默认 5173）
"""

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / "venv"
RUN_DIR = ROOT / ".run"
IS_WIN = os.name == "nt"


def info(msg: str):
    print(f"\033[36m[serve]\033[0m {msg}", flush=True)


def ok(msg: str):
    print(f"\033[32m[ ok ]\033[0m {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"\033[31m[fail]\033[0m {msg}", flush=True)
    sys.exit(1)


def run(cmd, cwd=None, check=True, quiet=False, env=None) -> int:
    if not quiet:
        info("$ " + " ".join(str(c) for c in cmd))
    proc = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env)
    if check and proc.returncode != 0:
        fail(f"命令失败 (exit {proc.returncode}): {' '.join(str(c) for c in cmd)}")
    return proc.returncode


# ── .env 与环境 ──────────────────────────────────────────────

def load_dotenv() -> dict:
    out = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, _, v = t.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


def npm_exe() -> str:
    exe = shutil.which("npm.cmd" if IS_WIN else "npm") or shutil.which("npm")
    if not exe:
        fail("未找到 npm。请先安装 Node.js >= 18：https://nodejs.org/")
    return exe


def port_free(host: str, port: int) -> bool:
    probe = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((probe, port)) != 0


def wait_http(url: str, timeout: float = 30.0) -> bool:
    import urllib.request

    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


# ── 安装 ─────────────────────────────────────────────────────

def step_venv() -> Path:
    py = venv_python()
    if py.exists():
        ok(f"虚拟环境已存在: {VENV}")
        return py
    if sys.version_info < (3, 11):
        fail(f"需要 Python >= 3.11，当前 {sys.version.split()[0]}。macOS: brew install python@3.12")
    info("创建虚拟环境 venv/ ...")
    run([sys.executable, "-m", "venv", VENV])
    ok("虚拟环境已创建")
    return venv_python()


def step_pip_config(py: Path, mirror: str | None):
    # venv 内 pip 配置路径: Windows 为 venv\pip.ini，Unix 为 venv/pip.conf
    target = VENV / ("pip.ini" if IS_WIN else "pip.conf")
    if target.exists():
        ok(f"pip 配置已存在: {target}")
        return
    if mirror:
        target.write_text(f"[global]\nindex-url = {mirror}\n", encoding="utf-8")
        ok(f"已写入 pip 镜像源: {mirror}（{target}）")
    else:
        # 探测 PyPI 直连性，不通则给出可操作的提示
        try:
            import urllib.request

            urllib.request.urlopen("https://pypi.org/simple/", timeout=5)
            ok("PyPI 直连正常")
        except Exception:
            info("⚠ 无法访问 pypi.org，若安装失败请重跑并加: --mirror https://pypi.tuna.tsinghua.edu.cn/simple")


def step_python_deps(py: Path, skip_tools: bool):
    site = py.parent / ("Lib/site-packages" if IS_WIN else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    fastapi_ok = any(site.glob("fastapi*")) if site.exists() else False
    if fastapi_ok:
        ok("Python 依赖已安装（跳过 pip install -e）")
    else:
        info("安装 Python 依赖（pip install -e '.[dev]'，首次约 1-3 分钟）...")
        run([py, "-m", "pip", "install", "--upgrade", "pip"], quiet=True)
        run([py, "-m", "pip", "install", "-e", ".[dev]"], cwd=ROOT)
        ok("Python 依赖安装完成")
    if skip_tools:
        info("已跳过工具依赖检查（--skip-tools）")
        return
    if not shutil.which("uv") and not (Path.home() / ".local/bin/uv").exists():
        info("提示: 未检测到 uv，工具依赖将安装到共享项目 venv；安装 uv 后工具将自动获得独立环境（pip install uv 或 curl -LsSf https://astral.sh/uv/install.sh | sh）")
    info("检查工具运行环境（torch/opencv 等大包，首次较久）...")
    code = run([py, "scripts/check_tool_env.py", "--auto"], cwd=ROOT, check=False)
    if code != 0:
        info("⚠ 部分工具依赖安装失败，可稍后手动重试: python scripts/check_tool_env.py --auto")
    else:
        ok("工具运行环境就绪")


def step_frontend_build(rebuild: bool):
    frontend = ROOT / "frontend"
    nm = frontend / "node_modules"
    dist = frontend / "dist"
    if nm.exists():
        ok("前端依赖已安装（跳过 npm install）")
    else:
        info("安装前端依赖（npm install）...")
        run([npm_exe(), "install"], cwd=frontend)
        ok("前端依赖安装完成")
    if rebuild or not dist.exists():
        info("构建前端（npm run build）...")
        run([npm_exe(), "run", "build"], cwd=frontend)
        ok("前端构建完成")
    else:
        ok("前端已构建（如需重建加 --rebuild-frontend）")


# ── 启动 ─────────────────────────────────────────────────────

def spawn(cmd, cwd, log_path: Path, env) -> subprocess.Popen:
    log = open(log_path, "w", encoding="utf-8")
    kwargs = {}
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen([str(c) for c in cmd], cwd=cwd, stdout=log, stderr=subprocess.STDOUT, env=env, **kwargs)


def kill_tree(pid: int):
    """终止整个进程树（spawn 时子进程是独立组长，按进程组杀，避免 npm→vite 孙进程残留）"""
    if IS_WIN:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        return
    # 注意：不能用 getpgid(pid) 取组——组长（如 npm 包装进程）先退出时 getpgid 会失败，
    # 但只要进程组还有成员，killpg(pid) 依然有效（spawn 时 start_new_session 保证 pgid == pid）
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(50):
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def stop_managed():
    pidfile = RUN_DIR / "pids.txt"
    if not pidfile.exists():
        info("没有由 serve.py 记录的运行中服务")
        return
    for line in pidfile.read_text().splitlines():
        line = line.strip()
        if not line.isdigit():
            continue
        kill_tree(int(line))
        ok(f"已停止进程组 pid {line}")
    pidfile.unlink(missing_ok=True)


def serve(mode: str, skip_tools: bool, rebuild_frontend: bool, mirror: str | None):
    env_cfg = load_dotenv()
    if not (ROOT / ".env").exists():
        if (ROOT / ".env.example").exists():
            info("⚠ 未找到 .env，已从 .env.example 复制，请编辑填入 LLM Provider 的 API Key 后重跑")
            shutil.copy(ROOT / ".env.example", ROOT / ".env")
            sys.exit(1)
        fail("未找到 .env 和 .env.example，无法确定 LLM 配置")

    host = env_cfg.get("SOTABAND_API_HOST", "0.0.0.0")
    api_port = int(env_cfg.get("SOTABAND_API_PORT", "8001"))
    web_port = int(env_cfg.get("SOTABAND_WEB_PORT", "5173"))

    py = step_venv()
    step_pip_config(py, mirror)
    step_python_deps(py, skip_tools)
    if mode == "start":
        step_frontend_build(rebuild_frontend)

    if not port_free(host, api_port):
        fail(f"后端端口 {host}:{api_port} 已被占用。请修改 .env 的 SOTABAND_API_PORT，或先用 `python scripts/serve.py stop` / 手动结束占用进程")
    if not port_free("127.0.0.1", web_port):
        fail(f"前端端口 {web_port} 已被占用。请修改 .env 的 SOTABAND_WEB_PORT，或结束占用进程")

    RUN_DIR.mkdir(exist_ok=True)
    stop_managed()

    child_env = {
        **os.environ,
        "SOTABAND_API_HOST": host,
        "SOTABAND_API_PORT": str(api_port),
        "SOTABAND_WEB_PORT": str(web_port),
    }

    backend = spawn([py, "-m", "uvicorn", "app.main:app", "--host", host, "--port", api_port],
                    ROOT, RUN_DIR / "backend.log", child_env)
    if not wait_http(f"http://127.0.0.1:{api_port}/api/health", timeout=30):
        backend.terminate()
        log = (RUN_DIR / "backend.log").read_text(encoding="utf-8", errors="replace")
        fail(f"后端启动失败，日志尾部:\n{log[-1500:]}")
    ok(f"后端已启动: http://{host}:{api_port}  (日志: .run/backend.log)")

    web_cmd = ([npm_exe(), "run", "dev"] if mode == "dev" else [npm_exe(), "run", "preview"])
    frontend_proc = spawn(web_cmd, ROOT / "frontend", RUN_DIR / "frontend.log", child_env)
    if not wait_http(f"http://127.0.0.1:{web_port}/", timeout=30):
        backend.terminate()
        frontend_proc.terminate()
        log = (RUN_DIR / "frontend.log").read_text(encoding="utf-8", errors="replace")
        fail(f"前端启动失败，日志尾部:\n{log[-1500:]}")
    ok(f"前端已启动: http://127.0.0.1:{web_port}  (日志: .run/frontend.log, 模式: {mode})")

    (RUN_DIR / "pids.txt").write_text(f"{backend.pid}\n{frontend_proc.pid}\n")
    print()
    ok("SOTABand 运行中。打开浏览器访问上面的前端地址；Ctrl+C 停止全部服务。")

    def shutdown(*_):
        info("正在停止服务 ...")
        for p in (backend, frontend_proc):
            kill_tree(p.pid)
        (RUN_DIR / "pids.txt").unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if not IS_WIN:
        signal.signal(signal.SIGTERM, shutdown)

    procs = {"后端": backend, "前端": frontend_proc}
    while True:
        for name, p in procs.items():
            if p.poll() is not None:
                logname = RUN_DIR / ("backend.log" if name == "后端" else "frontend.log")
                tail = Path(logname).read_text(encoding="utf-8", errors="replace")[-1500:]
                info(f"{name}进程退出 (exit {p.returncode})，日志尾部:\n{tail}")
                shutdown()
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser(description="SOTABand 一键安装 / 启动")
    ap.add_argument("command", choices=["setup", "dev", "start", "stop"])
    ap.add_argument("--skip-tools", action="store_true", help="跳过工具依赖检查")
    ap.add_argument("--rebuild-frontend", action="store_true", help="强制重新构建前端（start）")
    ap.add_argument("--mirror", help="为 venv 写入 pip 镜像源 URL")
    a = ap.parse_args()

    if a.command == "stop":
        stop_managed()
    elif a.command == "setup":
        py = step_venv()
        step_pip_config(py, a.mirror)
        step_python_deps(py, a.skip_tools)
        step_frontend_build(False)
        ok("环境安装完成。下一步: python scripts/serve.py dev（开发）或 start（部署）")
    else:
        serve(a.command, a.skip_tools, a.rebuild_frontend, a.mirror)


if __name__ == "__main__":
    main()
