"""安全审查三层测试 — 静态审查 + 运行时审查 + 回归

只测试，不修改产品代码。运行：
    .venv/bin/python tests/test_security_review.py

pytest 兼容：所有用例包在 __main__ 守卫内，pytest 收集阶段不会执行，
不影响 pytest tests/ 整体运行。
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

pytestmark = pytest.mark.unit

from core.security.review import review_code, block_violations, StaticReviewer, TEMPLATE_MARKER
from core.executor.tool_executor import ToolExecutor
from config.settings import settings

PASS = "✅ PASS"
FAIL = "❌ FAIL"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    print(f"{PASS if ok else FAIL}  {name}" + (f"  — {detail}" if detail else ""))


def run_tool(code: str, params: dict | None = None, timeout: float = 30) -> dict:
    """通过统一执行器跑一段工具代码（与线上环境完全一致）"""
    return ToolExecutor.execute_sync(
        tool_id="security-test", params=params or {}, code=code, timeout=timeout
    )


if __name__ == "__main__":
    # ============================================================
    # 一、静态审查（core/security/review.py）
    # ============================================================
    print("\n=== 一、静态审查 ===")

    # 1.1 危险系统调用
    v = review_code("import os\nos.system('ls -la')\n")
    check("os.system 被判 block", any(x.rule == "BANNED_CALL" and x.severity == "block" for x in v),
          f"violations={[(x.rule, x.severity) for x in v]}")

    # 1.2 subprocess 家族
    v = review_code("import subprocess\nsubprocess.run(['echo', 'hi'])\n")
    check("subprocess.run 被判 block", any(x.rule == "BANNED_CALL" and x.severity == "block" for x in v))

    # 1.3 eval / exec
    v = review_code("eval('1+1')\n")
    check("eval 被判 block", any(x.severity == "block" for x in v))

    # 1.4 文件删除必须走 core API
    v = review_code("import shutil\nshutil.rmtree('/tmp/x')\n")
    check("shutil.rmtree 被判 block", any(x.severity == "block" for x in v))

    # 1.5 getattr 混淆绕过
    v = review_code("import os\ngetattr(os, 'system')('ls')\n")
    check("getattr(os,'system') 被判 block (OBFUSCATED_CALL)",
          any(x.rule == "OBFUSCATED_CALL" and x.severity == "block" for x in v))

    # 1.6 getattr 良性调用不误报
    v = review_code("getattr(str, 'upper')('a')\n")
    check("getattr(str,'upper') 不误报", len([x for x in v if x.severity == "block"]) == 0,
          f"violations={[(x.rule) for x in v]}")

    # 1.7 裸 open 写文件 → warn 级
    v = review_code('with open("/tmp/out.txt", "w") as f:\n    f.write("x")\n')
    check("open(...,'w') 被判 warn (RAW_OPEN_WRITE)",
          any(x.rule == "RAW_OPEN_WRITE" and x.severity == "warn" for x in v))

    # 1.8 open 只读不误报
    v = review_code('f = open("/tmp/in.txt", "r")\n')
    check("open(...,'r') 不告警", len(v) == 0, f"violations={[(x.rule) for x in v]}")

    # 1.9 importlib 变量名动态导入 → warn
    v = review_code("import importlib\nimportlib.import_module(name)\n")
    check("importlib.import_module(变量) 被判 warn", any(x.rule == "DYNAMIC_IMPORT" for x in v))

    # 1.10 语法错误 → block
    v = review_code("def broken(:\n")
    check("语法错误被判 block (SYNTAX_ERROR)", any(x.rule == "SYNTAX_ERROR" for x in v))

    # 1.11 模板剥离：模板内的 subprocess（_call_tool）不算违规
    template_with_body = (
        "import subprocess as _sp\n"
        "def _call_tool(n, **p):\n"
        "    _sp.run(['echo', 'x'])\n"
        + TEMPLATE_MARKER + "\n"
        "def execute(**kwargs):\n"
        "    return {'status': 'success'}\n"
    )
    v = review_code(template_with_body)
    check("模板头部 subprocess 剥离后不告警", len(v) == 0, f"violations={[(x.rule) for x in v]}")

    # 1.12 模板剥离后，生成体内的 subprocess 仍被抓
    v = review_code(template_with_body + "\nimport subprocess\nsubprocess.run(['ls'])\n")
    check("生成体内 subprocess 仍被判 block", any(x.severity == "block" for x in v))

    # 1.13 对仓库中所有现有工具代码全量扫描（摸底）
    tool_impls = sorted((ROOT / "resources/tools/implementations").glob("*/tool.py"))
    scan_summary = []
    for impl in tool_impls:
        code = impl.read_text(encoding="utf-8", errors="replace")
        vs = review_code(code)
        blks = block_violations(vs)
        if blks:
            scan_summary.append(f"{impl.parent.name}: {len(blks)} block")
    check("现有工具代码扫描（摸底，不判 Pass/Fail）", True,
          f"共 {len(tool_impls)} 个工具，block 违规: {scan_summary if scan_summary else '无'}")

    # ============================================================
    # 二、运行时审查（ToolExecutor 注入的 audit hook + rlimit）
    # ============================================================
    print("\n=== 二、运行时审查 ===")

    # 运行时拦截类用例需要 enforce 模式（产品默认值已改为 audit 灰度）
    settings.security.review_mode = "enforce"

    # 2.1 白名单内写入（data/ 目录）→ 成功
    code = f"""
def execute(**kwargs):
    p = {str(ROOT) + "/data/sec_test_allowed.txt"!r}
    with open(p, "w") as f:
        f.write("ok")
    return {{"status": "success", "message": "written", "data": {{}}}}
"""
    r = run_tool(code)
    check("写入 data/ 白名单内 → 成功", r.get("status") == "success", f"result={r}")
    (ROOT / "data/sec_test_allowed.txt").unlink(missing_ok=True)

    # 2.2 白名单外写入（家目录）→ 被拦截
    code = """
def execute(**kwargs):
    with open("/root/sec_test_evil.txt", "w") as f:
        f.write("evil")
    return {"status": "success"}
"""
    r = run_tool(code)
    check("写入 /root → 被拦截", r.get("status") == "failed" and "FILE_WRITE_DENY" in str(r.get("_security_violations", [])),
          f"error={r.get('error')}, violations={r.get('_security_violations')}")
    Path("/root/sec_test_evil.txt").unlink(missing_ok=True)

    # 2.3 os.system → 被拦截
    # 本分支与上游差异：执行前还有 code_scan 底线闸门（ToolExecutor._security_gate），
    # os.system 在进沙箱前即被静态拒绝，运行时钩子是第二道防线（上游无此前置
    # 闸门，该用例原本验证的正是运行时事件）。本地契约：命令不得被执行、结果失败。
    code = """
import os
def execute(**kwargs):
    os.system("echo pwned")
    return {"status": "success"}
"""
    r = run_tool(code)
    check("os.system → 被拦截（前置静态闸门先于运行时钩子）",
          r.get("status") == "failed" and "pwned" not in str(r),
          f"error={r.get('error')}, msg={str(r.get('message'))[:80]}")

    # 2.4 subprocess.Popen 外部命令 → 被拦截
    code = """
import subprocess
def execute(**kwargs):
    subprocess.Popen(["echo", "hi"]).wait()
    return {"status": "success"}
"""
    r = run_tool(code)
    check("subprocess.Popen → 被拦截", r.get("status") == "failed",
          f"error={r.get('error')}, violations={r.get('_security_violations')}")

    # 2.5 系统模板 _call_tool 特征调用 → 放行（不误伤工具间调用）
    # 真实模板的内部脚本特征：同时包含 "import json, sys" 与 "execute("
    inner = (
        "import json, sys; sys.path.insert(0, '.'); "
        "exec(open('/dev/null').read()); "
        "print(json.dumps(execute()))"
    )
    assert "import json, sys" in inner and "execute(" in inner
    code = f"""
import subprocess, sys
def execute(**kwargs):
    proc = subprocess.Popen(
        [sys.executable, "-c", {inner!r}],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    out, _ = proc.communicate(timeout=15)
    return {{"status": "success", "data": {{"violations": _sec_violations}}}}
"""
    r = run_tool(code)
    check("模板 _call_tool 特征子进程 → 放行",
          r.get("status") == "success" and not any("BANNED_SYSCALL" in v for v in (r.get("data") or {}).get("violations", [])),
          f"violations={(r.get('data') or {}).get('violations')}")

    # 2.6 网络连接被记录（audit 事件）
    # 本分支与上游差异：code_scan 静态闸门禁止工具代码直接 import socket，
    # 因此经由 urllib 间接触发 connect——audit 事件在 socket 层触发，与导入
    # 来源无关，仍能验证运行时网络记录路径。
    code = """
import urllib.request
def execute(**kwargs):
    try:
        urllib.request.urlopen("http://127.0.0.1:9/", timeout=3)
    except Exception:
        pass
    return {"status": "success", "data": {"violations": _sec_violations}}
"""
    r = run_tool(code)
    net_events = [v for v in (r.get("data") or {}).get("violations", []) if v.startswith("NETWORK")]
    # 断言主机名正确提取（audit 事件 args[1] 才是地址元组；上游曾误取 args[0]）
    check("网络连接被记录（NETWORK 事件，含主机名）",
          r.get("status") == "success" and any("127.0.0.1" in v for v in net_events),
          f"events={net_events}")

    # 2.7 CPU 资源限制（临时调低 max_cpu_seconds 验证 rlimit 生效）
    saved_cpu = settings.security.max_cpu_seconds
    settings.security.max_cpu_seconds = 2
    try:
        code = """
def execute(**kwargs):
    x = 0
    while True:
        x += 1
    return {"status": "success"}
"""
        t0 = time.time()
        r = run_tool(code, timeout=15)
        elapsed = time.time() - t0
        check("CPU rlimit 生效（死循环 ~2s 被杀）",
              r.get("status") == "failed" and elapsed < 14,
              f"elapsed={elapsed:.1f}s, result={str(r)[:100]}")
    finally:
        settings.security.max_cpu_seconds = saved_cpu

    # 2.8 内存资源限制（临时调低 max_memory_mb）
    saved_mem = settings.security.max_memory_mb
    settings.security.max_memory_mb = 256
    try:
        code = """
def execute(**kwargs):
    blob = bytearray(512 * 1024 * 1024)  # 512MB 超过 256MB 限制
    return {"status": "success", "data": {"len": len(blob)}}
"""
        r = run_tool(code, timeout=30)
        check("内存 rlimit 生效（超分配被杀）", r.get("status") == "failed",
              f"error={r.get('error')}, msg={str(r.get('message'))[:80]}")
    finally:
        settings.security.max_memory_mb = saved_mem

    # ============================================================
    # 三、回归：现有工具不受影响
    # ============================================================
    print("\n=== 三、回归测试 ===")

    registry_path = ROOT / "resources/tools/registry.json"
    if registry_path.exists():
        tools = json.loads(registry_path.read_text(encoding="utf-8"))
        active_ids = [t["id"] for t in tools if t.get("status") == "active"]
        # 挑两个无重依赖的工具回归
        candidates = [tid for tid in ("calc-square", "calculate-circle-area", "test-greeting") if tid in active_ids]
        for tid in candidates[:2]:
            r = ToolExecutor.execute_sync(tool_id=tid, params={"radius": 3, "x": 4, "name": "s"}, timeout=60)
            ok = r.get("status") == "success"
            check(f"回归: 工具 {tid} 正常执行", ok, f"msg={str(r.get('message'))[:60]}")
    else:
        check("回归: registry.json 存在", False, "找不到注册表")

    # ============================================================
    # 汇总
    # ============================================================
    print("\n=== 汇总 ===")
    failed = [r for r in results if not r[1]]
    print(f"总计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    if failed:
        print("失败项：")
        for name, _, detail in failed:
            print(f"  - {name}: {detail}")
    sys.exit(1 if failed else 0)
