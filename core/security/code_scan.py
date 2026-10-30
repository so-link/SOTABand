"""工具/Agent 代码静态安全扫描（todo 3.2）。

基于 AST 的确定性检查，不用正则匹配调用（正则可被换行/别名绕过）。
两级结论：

- **errors（阻断）**：几乎只用于破坏性/逃逸目的的调用——shell 执行、
  进程替换、动态执行、直接删除路径、网络服务端等。LLM 生成的正常
  数据工具不会用到它们；命中即视为代码缺陷，返回给自动调试循环重写。
- **warnings（记录不阻断）**：合法工具常见的能力（subprocess 跑本地
  命令、open 读写文件、requests 联网）。这些是平台信任模型允许的
  部分（工具在独立子进程运行），但会在生成/调试事件流中透明展示。

设计约定：宁可漏报（warning 化），不可误杀存量 48 个内置工具——
error 名单以「对内置工具零命中」为回归基线，直接运行本模块即可复核：
``python -m core.security.code_scan``（扫描全部内置工具 tool.py，应输出 0 errors）。
"""

from __future__ import annotations

import ast

# 直接 import 即阻断的模块（顶层名）。
# 注：subprocess/threading 不在列——内置工具大量使用，属于 warning 能力。
BANNED_IMPORTS: frozenset[str] = frozenset({
    "ctypes",        # 绕过一切 Python 层约束直调 C
    "socket",        # 原始套接字（requests/http 不算，那是正常联网）
    "pty",           # 伪终端逃逸
    "resource",      # RLIMIT/进程控制
})

# 形如 obj.attr(...) 的危险调用（对内置工具零命中）。
BANNED_ATTR_CALLS: frozenset[tuple[str, str]] = frozenset({
    ("os", "system"), ("os", "popen"), ("os", "execv"), ("os", "execve"),
    ("os", "execvp"), ("os", "spawnl"), ("os", "spawnv"),
    ("os", "remove"), ("os", "rmdir"), ("os", "unlink"), ("os", "kill"),
    ("os", "killpg"), ("shutil", "rmtree"),
    ("pathlib", "rmtree"),
    ("subprocess", "getoutput"), ("subprocess", "getstatusoutput"),
    ("importlib", "import_module"),  # 动态导入可绕过 BANNED_IMPORTS
})

# 形如 x.y.z 链上的危险调用（取末两段兜底，如 a.subprocess.getoutput）。
_SHELL_SUFFIXES: frozenset[str] = frozenset({"getoutput", "getstatusoutput"})

# 危险内置函数（对内置工具零命中：eval/exec/compile/__import__/breakpoint）。
BANNED_BUILTINS: frozenset[str] = frozenset({
    "eval", "exec", "compile", "__import__", "breakpoint",
})

# 允许联网的常规库（识别 requests 的 session 方法时用到）。
_HTTP_MODULES: frozenset[str] = frozenset({"requests", "httpx", "urllib"})


class ScanResult:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


def _call_root_chain(node: ast.AST) -> tuple[str, ...] | None:
    """把 a.b.c(...) 的 func 解析为 ('a','b','c')；无法静态解析返回 None。"""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return tuple(reversed(parts))
    return None


def _local_names(tree: ast.AST) -> set[str]:
    """收集函数作用域内的局部名，用于区分 open 调用与同名变量。"""
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for a in n.args.args + n.args.kwonlyargs + n.args.posonlyargs:
                names.add(a.arg)
            if n.args.vararg:
                names.add(n.args.vararg.arg)
            if n.args.kwarg:
                names.add(n.args.kwarg.arg)
    return names


def scan_code(code: str) -> ScanResult:
    """静态扫描代码，返回 ScanResult。语法错误本身作为 error 返回。"""
    res = ScanResult()
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        res.errors.append(f"第 {e.lineno} 行语法错误：{e.msg}")
        return res

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top in BANNED_IMPORTS:
                    res.errors.append(
                        f"第 {node.lineno} 行：禁止 import {a.name}（安全约束）")
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in BANNED_IMPORTS:
                res.errors.append(
                    f"第 {node.lineno} 行：禁止 from {node.module} import …（安全约束）")
                continue
            if top in _HTTP_MODULES:
                continue  # 常规联网库，无需警告

        elif isinstance(node, ast.Call):
            chain = _call_root_chain(node.func) if isinstance(node.func, (ast.Attribute, ast.Name)) else None
            if not chain:
                continue
            if len(chain) == 1:
                fn = chain[0]
                if fn in BANNED_BUILTINS:
                    res.errors.append(f"第 {node.lineno} 行：禁止使用 {fn}()（动态执行）")
                elif fn in {"rmdir", "remove", "unlink", "import_module"}:
                    res.errors.append(f"第 {node.lineno} 行：禁止直接使用 {fn}() 删除路径")
            elif len(chain) == 2:
                pair = (chain[0], chain[1])
                if pair in BANNED_ATTR_CALLS:
                    res.errors.append(
                        f"第 {node.lineno} 行：禁止调用 {chain[0]}.{chain[1]}()（安全约束）")
            else:
                if chain[-1] in _SHELL_SUFFIXES:
                    res.errors.append(
                        f"第 {node.lineno} 行：禁止调用 {'.'.join(chain)}()（shell/动态导入）")

    # warnings：对内置工具常见、平台信任模型允许但值得透明的能力
    has = {"subprocess": False, "open": False, "network": False}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = (getattr(node, "module", None) or "")
            if isinstance(node, ast.Import):
                mod = ",".join(a.name for a in node.names)
            tops = {m.split(".")[0] for m in mod.split(",")}
            if "subprocess" in tops:
                has["subprocess"] = True
            if _HTTP_MODULES & tops:
                has["network"] = True
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            has["open"] = True
        elif isinstance(node, ast.Call):
            chain = _call_root_chain(node.func) if isinstance(node.func, ast.Attribute) else None
            if chain and len(chain) >= 2 and chain[-2] in {"requests", "httpx", "session"}:
                has["network"] = True
    if has["subprocess"]:
        res.warnings.append("使用了 subprocess 执行外部命令（工具子进程内允许，但请注意命令来源不可信时的注入风险）")
    if has["open"]:
        res.warnings.append("直接读写本地文件（open），建议使用 TOOL_DIR / 数据集路径范围内的文件操作")
    if has["network"]:
        res.warnings.append("存在对外网络调用")
    return res


if __name__ == "__main__":
    import glob

    _files = sorted(glob.glob("resources/tools/implementations/*/tool.py"))
    _bad = 0
    for _f in _files:
        with open(_f, encoding="utf-8") as _h:
            _r = scan_code(_h.read())
        if not _r.ok:
            _bad += 1
            print(f"ERROR {(_f.split('/')[-2]):30} {_r.errors[:2]}")
    print(f"scanned {len(_files)} builtin tools, errors={_bad}")
    raise SystemExit(1 if _bad else 0)
