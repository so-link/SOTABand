#!/usr/bin/env python3
"""wxbit 全量 E2E 一键编排。

流程：临时 SOTABAND_HOME → 播种系统资源 → 假 LLM → 后端 → 前端 →
root 建普通测试用户 → pytest Playwright → 路由覆盖门禁 → 工具静态冒烟 →
摘要/报告 → 清理。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
REPORT_ROOT = PROJECT_ROOT / "dist" / "e2e-report"
RUN_ROOT = PROJECT_ROOT / ".runtime" / "e2e"
IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    for _stream in ("stdout", "stderr"):
        try:
            getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def log(message: str) -> None:
    print(message, flush=True)


def _port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _wait_http(url: str, timeout: float, process: subprocess.Popen | None = None) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"进程提前退出（code={process.returncode}）：{url}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = str(exc)
        time.sleep(0.4)
    raise TimeoutError(f"等待 {url} 超时（{timeout}s）：{last_error}")


def _redact_cmd(cmd: list[str]) -> list[str]:
    """日志中隐藏密码/Token 参数值。"""
    out = list(cmd)
    for flag in ("--root-password", "--e2e-password", "--token"):
        for index, item in enumerate(out):
            if item == flag and index + 1 < len(out):
                out[index + 1] = "<redacted>"
    return out


def _run_command(
    cmd: list[str],
    env: dict,
    cwd: Path,
    log_path: Path | None = None,
    timeout: float | None = None,
    capture: bool = False,
) -> tuple[int, str]:
    log(f"[run] {' '.join(_redact_cmd(cmd))}")
    if capture:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = proc.stdout or ""
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(output, encoding="utf-8")
        return proc.returncode, output
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8", errors="replace") as handle:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        return proc.returncode, ""
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, timeout=timeout)
    return proc.returncode, ""


class ManagedProcess:
    def __init__(self, name: str, cmd: list[str], env: dict, log_path: Path, cwd: Path = PROJECT_ROOT):
        self.name = name
        self.cmd = cmd
        self.env = env
        self.log_path = log_path
        self.cwd = cwd
        self.handle = None
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.log_path.open("wb")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
        self.proc = subprocess.Popen(
            self.cmd,
            cwd=str(self.cwd),
            env=self.env,
            stdout=self.handle,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        log(f"[start] {self.name} pid={self.proc.pid} log={self.log_path}")

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.handle is not None:
            self.handle.close()
            self.handle = None
        log(f"[stop] {self.name}")

    def tail(self, limit: int = 40) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-limit:])


def _npm_command(args: list[str]) -> list[str]:
    if IS_WINDOWS:
        return ["cmd.exe", "/c", "npm", *args]
    return ["npm", *args]


def _login_token(base_url: str, username: str, password: str) -> str:
    payload = json.dumps({"username": username, "password": password}).encode()
    request = urllib.request.Request(
        f"{base_url}/api/auth/login",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode())
    token = str(body.get("token") or "")
    if not token:
        raise RuntimeError(f"登录响应缺少 token：{body}")
    return token


def _http_json(
    base_url: str, path: str, token: str = "", method: str = "GET", payload=None
) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def _junit_stats(path: Path) -> dict:
    if not path.exists():
        return {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "passed": 0}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "passed": 0}
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    tests = failures = errors = skipped = 0
    for suite in suites:
        tests += int(suite.attrib.get("tests", 0))
        failures += int(suite.attrib.get("failures", 0))
        errors += int(suite.attrib.get("errors", 0))
        skipped += int(suite.attrib.get("skipped", 0))
    return {
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "passed": max(0, tests - failures - errors - skipped),
    }


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _report_summary(
    *,
    run_id: str,
    stats: dict,
    coverage: dict,
    tools: dict,
    production_count: int,
    pytest_exit: int,
    coverage_exit: int,
    tool_exit: int,
) -> str:
    static = tools.get("static") or {}
    execution = tools.get("execution") or {}
    lines = [
        "# wxbit E2E 全量验证摘要",
        "",
        f"- 运行 ID：{run_id}",
        f"- 浏览器用例：{stats['passed']} 通过 / {stats['failures'] + stats['errors']} 失败 / {stats['skipped']} 跳过（共 {stats['tests']}）",
    ]
    if coverage:
        lines.append(
            f"- 路由覆盖：{coverage.get('covered_operations', 0)}/{coverage.get('required_operations', 0)} "
            f"（{coverage.get('coverage_percent', 0)}%），未覆盖 {len(coverage.get('uncovered') or [])}，"
            f"排除 {len(coverage.get('excluded') or [])}，未匹配请求 {len(coverage.get('unknown_requests') or [])}"
        )
    else:
        lines.append("- 路由覆盖：本次未运行浏览器用例（跳过门禁）")
    lines += [
        f"- 工具静态冒烟：{static.get('passed', 0)}/{static.get('total', 0)} 通过，{static.get('failed', 0)} 失败",
    ]
    if execution:
        lines.append(
            f"- 工具执行回归：{execution.get('passed', 0)} 通过 / {execution.get('failed', 0)} 失败 / {execution.get('skipped', 0)} 跳过（不阻断）"
        )
    lines += [
        f"- 生产锚点改动：{production_count} 处（tests/e2e/production-changes.json）",
        f"- 退出判定：pytest={pytest_exit} coverage={coverage_exit} tools={tool_exit}",
        "",
        "## 失败项",
        "",
    ]
    if stats["failures"] or stats["errors"] or coverage.get("uncovered") or static.get("failed"):
        if stats["failures"] or stats["errors"]:
            lines.append("- 浏览器/API 用例存在失败，见 pytest 日志与失败截图。")
        for item in (coverage.get("uncovered") or [])[:20]:
            lines.append(f"- 未覆盖路由：`{item['key']}`（{item.get('mode')}）")
        for item in (static.get("failures") or [])[:20]:
            lines.append(f"- 工具静态冒烟失败：`{item['tool_id']}`：{'；'.join(item['errors'])}")
    else:
        lines.append("- 无。")
    lines += [
        "",
        "## 已知边界",
        "",
        "- 默认使用假 LLM；真实 LLM/天气 Agent 的 live 用例默认跳过。",
        "- 工具执行回归默认不跑；仅 `--tools` / `--tool-profile` 触发。",
        "- 未修复 `tsc -b` 的既有类型错误；E2E 使用 Vite dev。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="wxbit E2E 全量编排")
    parser.add_argument("--backend-port", type=int, default=8211)
    parser.add_argument("--frontend-port", type=int, default=5183)
    parser.add_argument("--llm-port", type=int, default=8111)
    parser.add_argument("--only", choices=["tools"], default=None)
    parser.add_argument("--tools", action="append", default=[])
    parser.add_argument("--tool-profile", default=None)
    parser.add_argument("--tool-tag", default=None)
    parser.add_argument("--grep", default="")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    run_dir = RUN_ROOT / run_id
    home_root = run_dir / "SB-USER"
    logs_dir = run_dir / "logs"
    fixtures_path = run_dir / "fixtures.json"
    junit_path = REPORT_ROOT / "junit.xml"
    coverage_hits = REPORT_ROOT / "coverage-hits.jsonl"
    run_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    coverage_hits.write_text("", encoding="utf-8")

    for port, name in (
        (args.backend_port, "后端"),
        (args.frontend_port, "前端"),
        (args.llm_port, "假 LLM"),
    ):
        if _port_in_use(port):
            log(f"[fail] {name}端口 {port} 已被占用")
            return 1

    if not (PROJECT_ROOT / "venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")).exists():
        log("[fail] 缺少 venv；先运行 python scripts/serve.py setup 或创建 venv 并安装 .[dev,e2e]")
        return 1
    if not (FRONTEND_ROOT / "node_modules").exists():
        log("[fail] 缺少 frontend/node_modules；先运行 cd frontend && npm install")
        return 1

    root_password = f"E2eRoot!{run_id}"
    e2e_user = f"e2e_{run_id.replace('-', '_')}"
    e2e_password = f"E2eUser!{run_id}"
    backend_url = f"http://127.0.0.1:{args.backend_port}"
    frontend_url = f"http://127.0.0.1:{args.frontend_port}"
    llm_url = f"http://127.0.0.1:{args.llm_port}"

    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "SOTABAND_HOME": str(home_root),
            "SOTABAND_ROOT_USERNAME": "root",
            "SOTABAND_ROOT_PASSWORD": root_password,
            "SOTABAND_AUTH_SECRET": f"e2e-auth-secret-{run_id}",
            "SOTABAND_API_HOST": "127.0.0.1",
            "SOTABAND_API_PORT": str(args.backend_port),
            "SOTABAND_WEB_PORT": str(args.frontend_port),
            "LLM_PROVIDER": "openai",
            "LLM_API_KEY": "e2e",
            "LLM_BASE_URL": f"{llm_url}/v1",
            "LLM_MODEL": "e2e-model",
            "LLM_TIMEOUT": "120",
            "E2E_RUN_ID": run_id,
            "E2E_FIXTURES": str(fixtures_path),
            "E2E_COVERAGE_FILE": str(coverage_hits),
            "SOTABAND_E2E_BACKEND": backend_url,
            "SOTABAND_E2E_FRONTEND": frontend_url,
            "SOTABAND_E2E_USER": e2e_user,
            "SOTABAND_E2E_PASSWORD": e2e_password,
        }
    )

    processes: list[ManagedProcess] = []
    exit_code = 0
    pytest_exit = coverage_exit = tool_exit = 0
    stats = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "passed": 0}
    coverage = {}
    tools = {}
    production_count = 0
    user_id = ""

    try:
        log(f"===== wxbit E2E 运行 {run_id} =====")
        log(f"[paths] 运行目录：{run_dir}")
        log(f"[paths] 报告目录：{REPORT_ROOT}")

        log("[step] 播种系统资源")
        code, _ = _run_command(
            [sys.executable, "scripts/seed_system_resources.py"],
            env,
            PROJECT_ROOT,
            logs_dir / "seed.log",
            timeout=300,
        )
        if code != 0:
            raise RuntimeError(f"系统资源播种失败（exit={code}），日志见 {logs_dir / 'seed.log'}")

        log("[step] 启动假 LLM")
        fake = ManagedProcess(
            "fake-llm",
            [
                sys.executable,
                "scripts/e2e/fake_llm_server.py",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.llm_port),
                "--script",
                str(PROJECT_ROOT / "tests/e2e/fixtures/llm-script.json"),
                "--log",
                str(logs_dir / "fake-llm.jsonl"),
                "--nonce",
                run_id,
            ],
            env,
            logs_dir / "fake-llm.log",
        )
        fake.start()
        processes.append(fake)
        _wait_http(f"{llm_url}/health", 20, fake.proc)

        log("[step] 启动后端")
        backend = ManagedProcess(
            "backend",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.backend_port),
            ],
            env,
            logs_dir / "backend.log",
        )
        backend.start()
        processes.append(backend)
        _wait_http(f"{backend_url}/api/health", 90, backend.proc)

        if args.only != "tools":
            log("[step] 启动前端（Vite dev）")
            frontend = ManagedProcess(
                "frontend",
                _npm_command(
                    [
                        "run",
                        "dev",
                        "--",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(args.frontend_port),
                        "--strictPort",
                    ]
                ),
                env,
                logs_dir / "frontend.log",
                cwd=FRONTEND_ROOT,
            )
            frontend.start()
            processes.append(frontend)
            _wait_http(frontend_url, 90, frontend.proc)

        log("[step] 创建普通 E2E 用户与资源夹具")
        code, output = _run_command(
            [
                sys.executable,
                "scripts/e2e/prepare_fixtures.py",
                "--base-url",
                backend_url,
                "--run-id",
                run_id,
                "--fixtures",
                str(fixtures_path),
                "--coverage-file",
                str(coverage_hits),
                "--root-user",
                "root",
                "--root-password",
                root_password,
                "--e2e-user",
                e2e_user,
                "--e2e-password",
                e2e_password,
            ],
            env,
            PROJECT_ROOT,
            logs_dir / "prepare.log",
            timeout=120,
            capture=True,
        )
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
        if code != 0:
            raise RuntimeError(f"准备夹具失败（exit={code}），日志见 {logs_dir / 'prepare.log'}")

        fixtures = _read_json(fixtures_path, {})
        user_id = str((fixtures.get("user") or {}).get("id") or "")
        user_token = _login_token(backend_url, e2e_user, e2e_password)

        if args.only != "tools":
            log("[step] Playwright 浏览器用例")
            cmd = [
                sys.executable,
                "-m",
                "pytest",
                "tests/e2e",
                "-q",
                "-m",
                "e2e and not e2e_live",
                "--run-e2e",
                f"--junitxml={junit_path}",
            ]
            if args.grep:
                cmd += ["-k", args.grep]
            pytest_exit, output = _run_command(
                cmd,
                env,
                PROJECT_ROOT,
                logs_dir / "pytest.log",
                timeout=args.timeout,
                capture=True,
            )
            if output:
                tail = "\n".join(output.splitlines()[-120:])
                print(tail)
            stats = _junit_stats(junit_path)

        log("[step] 工具静态冒烟")
        tool_cmd = [
            sys.executable,
            "scripts/e2e/tool_regression.py",
            "--base-url",
            backend_url,
            "--token",
            user_token,
            "--static-smoke",
        ]
        tool_exit, _ = _run_command(
            tool_cmd, env, PROJECT_ROOT, logs_dir / "tools.log", timeout=300, capture=True
        )
        if tool_exit != 0:
            exit_code = 1

        if args.tools or args.tool_profile or args.tool_tag:
            log("[step] 工具执行回归（不阻断）")
            exec_cmd = [
                sys.executable,
                "scripts/e2e/tool_regression.py",
                "--base-url",
                backend_url,
                "--token",
                user_token,
                "--execute",
            ]
            for item in args.tools:
                exec_cmd += ["--tools", item]
            if args.tool_profile:
                exec_cmd += ["--tool-profile", args.tool_profile]
            if args.tool_tag:
                exec_cmd += ["--tool-tag", args.tool_tag]
            _run_command(
                exec_cmd, env, PROJECT_ROOT, logs_dir / "tools-execute.log", timeout=600
            )

        if args.only != "tools":
            log("[step] 更新覆盖台账并检查门禁")
            _run_command(
                [
                    sys.executable,
                    "scripts/e2e/classify_manifest.py",
                    "--hits",
                    str(coverage_hits),
                ],
                env,
                PROJECT_ROOT,
                logs_dir / "classify.log",
            )
            coverage_exit, _ = _run_command(
                [
                    sys.executable,
                    "scripts/e2e/coverage_report.py",
                    "--hits",
                    str(coverage_hits),
                ],
                env,
                PROJECT_ROOT,
                logs_dir / "coverage.log",
            )
            if coverage_exit != 0:
                exit_code = 1

        coverage = _read_json(REPORT_ROOT / "coverage.json", {}) if args.only != "tools" else {}
        tools = _read_json(REPORT_ROOT / "tools.json", {})
        production = _read_json(PROJECT_ROOT / "tests/e2e/production-changes.json", {})
        production_count = len(production.get("changes") or []) if isinstance(production, dict) else 0

        if pytest_exit != 0:
            exit_code = 1
        summary = _report_summary(
            run_id=run_id,
            stats=stats,
            coverage=coverage,
            tools=tools,
            production_count=production_count,
            pytest_exit=pytest_exit,
            coverage_exit=coverage_exit,
            tool_exit=tool_exit,
        )
        (REPORT_ROOT / "summary.md").write_text(summary, encoding="utf-8")
        print()
        print(summary)

    except Exception as exc:  # noqa: BLE001
        exit_code = 1
        log(f"[fail] {exc}")
        for process in processes:
            log(f"--- {process.name} tail ---")
            log(process.tail())
    finally:
        # 删除临时用户（后端仍在线时）
        try:
            if user_id and backend_url:
                admin_token = _login_token(
                    backend_url,
                    os.environ.get("SOTABAND_ROOT_USERNAME", "root"),
                    root_password,
                )
                _http_json(
                    backend_url,
                    f"/api/auth/users/{user_id}",
                    admin_token,
                    method="DELETE",
                )
        except Exception:
            pass

        for process in reversed(processes):
            process.stop()

        if exit_code == 0 and not args.keep:
            shutil.rmtree(run_dir, ignore_errors=True)
            log("[clean] 已清理运行目录")
        else:
            log(f"[keep] 运行现场保留：{run_dir}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
