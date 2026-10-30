"""工具/Agent 代码静态安全扫描（todo 3.2）。

基于 AST 的确定性检查，不用正则匹配调用（正则可被换行/别名绕过）。
两级结论：

- **errors（阻断）**：几乎只用于破坏性/逃逸目的的调用——shell 执行
  （os.system/popen/getoutput，以及子进程调用显式 shell=True）、
  进程替换、动态执行、直接删除路径、网络服务端等。LLM 生成的正常
  数据工具不会用到它们；命中即视为代码缺陷，返回给自动调试循环重写。
- **warnings（记录不阻断）**：合法工具常见的能力（subprocess 列表参数
  跑本地命令、open 读写文件、requests 联网）。这些是平台信任模型允许的
  部分（工具在独立子进程运行），但会在生成/调试事件流中透明展示。

设计约定：宁可漏报（warning 化），不可误杀存量 48 个内置工具——
error 名单以「对内置工具零命中」为回归基线，直接运行本模块即可复核：
``python -m core.security.code_scan``（扫描全部内置工具 tool.py，应输出 0 errors）。
"""

from __future__ import annotations

import ast
import ipaddress
import re
from urllib.parse import urlparse

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

# 视为内网/本机的主机名后缀（ urlparse 不识别 .local 之类为私有地址）。
_LOCAL_TLD_RE = re.compile(r"\.(?:local|internal|lan)$", re.IGNORECASE)


def _host_is_internal(host: str) -> bool:
    """判断 URL 主机名是否指向本机/内网（warning 级，不阻断）。"""
    host = host.strip("[]").rstrip(".").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    if _LOCAL_TLD_RE.search(host):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_loopback or ip.is_private or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def _string_constants(tree: ast.AST):
    """产出 (lineno, 字符串字面量)，含 f-string 中字面部分。"""
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.lineno, n.value


# ── 导入白名单（todo 3.1 检查项 5，warning 级） ──
#
# 语义：非标准库、非平台命名空间的 import，必须能在工具自己的
# requirements.txt 里找到对应依赖——否则该工具换机运行/重建 venv 时
# 必然 ModuleNotFoundError（依赖漂移），提示补齐声明而不是阻断，
# 因为「requirements 有但拼写不同」的误报代价高于漏报。
# 匹配用宽松归一化（去 -_. 后前缀互含），复用 deps.PIP_NAME_MAP 做
# import 名 → pip 包名转换（cv2→opencv-python-headless 等）。

_PLATFORM_MODULES: frozenset[str] = frozenset({"core"})


def check_undeclared_imports(code: str, requirements_text: str | None) -> list[str]:
    """返回「import 了但 requirements.txt 未声明」的模块名列表（无则空）。

    requirements_text 为 None/空时，所有非 stdlib/非平台导入都算未声明。
    """
    import sys as _sys
    from core.resource.deps import pip_name

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    def _norm(s: str) -> str:
        return re.sub(r"[-_.]", "", s.lower())

    declared = set()
    for line in (requirements_text or "").splitlines():
        m = re.match(r"([A-Za-z0-9_.\-]+)", line.strip())
        if m and not line.strip().startswith("#"):
            declared.add(_norm(m.group(1)))

    mods: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods.add(n.module.split(".")[0])

    out: list[str] = []
    for mod in sorted(mods):
        if mod in _sys.stdlib_module_names or mod in _PLATFORM_MODULES:
            continue
        cand = _norm(pip_name(mod))
        if any(cand == d or cand.startswith(d) or d.startswith(cand) for d in declared):
            continue
        out.append(mod)
    return out


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


def _has_shell_true(node: ast.Call) -> bool:
    """子进程调用是否显式 shell=True（字面量）。shell=True 等价于
    os.system：命令字符串整个交给 /bin/sh，注入面与 shell 执行同级，
    故为 error。shell=变量 无法静态判定值，接受漏报（warning 兜底）。"""
    for kw in node.keywords:
        if kw.arg == "shell":
            v = kw.value
            if isinstance(v, ast.Constant):
                return v.value is True
            return False
    return False


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
                elif chain[0] == "subprocess" and _has_shell_true(node):
                    res.errors.append(
                        f"第 {node.lineno} 行：subprocess 调用带 shell=True 等价于 shell 执行，禁止")
            else:
                if chain[-1] in _SHELL_SUFFIXES:
                    res.errors.append(
                        f"第 {node.lineno} 行：禁止调用 {'.'.join(chain)}()（shell/动态导入）")
                elif _has_shell_true(node):
                    res.errors.append(
                        f"第 {node.lineno} 行：子进程调用带 shell=True 等价于 shell 执行，禁止")

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

    # 路径遍历风险：字符串字面量中出现 ../（todo 3.2 检查项 2，warning 级）。
    # error 级需数据流分析（参数是否来自用户输入），静态层不做，避免误杀。
    trav_lines = sorted({
        line for line, v in _string_constants(tree) if "../" in v or "..\\" in v
    })
    if trav_lines:
        shown = "、".join(f"第 {l} 行" for l in trav_lines[:3])
        res.warnings.append(
            f"字符串中出现 ../ 相对上跳路径（{shown}），若拼接用户输入可能越权访问 TOOL_DIR 之外的位置")

    # 网络目标校验：字面量 URL 指向本机/内网（todo 3.2 检查项 3，warning 级）。
    # 主机名为变量时静态无法判定，接受漏报。
    internal_urls: list[str] = []
    for _line, v in _string_constants(tree):
        for m in re.finditer(r"https?://[^\s'\"]+", v):
            host = urlparse(m.group(0)).hostname or ""
            if _host_is_internal(host):
                internal_urls.append(f"第 {_line} 行 {m.group(0)[:60]}")
    if internal_urls:
        res.warnings.append(
            f"存在指向本机/内网的 URL（{ '；'.join(internal_urls[:3]) }），请确认目标服务允许被工具访问")

    # 复杂度异常（todo 3.2 检查项 4，warning 级——常驻服务循环是合法写法，
    # 不能阻断；但对 LLM 生成的「忘写退出条件的死循环」是有效提示）：
    # (a) while True 体内无任何 break/return/raise；(b) 巨量字面量分配（≥10MB 级）。
    def _exits_loop(node: ast.AST) -> bool:
        """本层是否存在能退出外层 while 的 break/return/raise。

        规则：嵌套函数/lambda 内的 return 不算；嵌套 for/while 内的 break
        属于内层循环，也不算；return/raise 在任何深度（除嵌套定义外）都退出。
        """
        stack: list[tuple[ast.AST, bool]] = [(node, False)]
        while stack:
            x, in_loop = stack.pop()
            if x is not node and isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(x, (ast.Return, ast.Raise)):
                return True
            if isinstance(x, ast.Break) and not in_loop:
                return True
            deeper = in_loop or isinstance(x, (ast.For, ast.AsyncFor, ast.While))
            stack.extend((c, deeper) for c in ast.iter_child_nodes(x))
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value is True:
            has_exit = any(
                _exits_loop(stmt)
                for stmt in node.body
                if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            if not has_exit:
                res.warnings.append(
                    f"第 {node.lineno} 行 while True 循环体内没有 break/return/raise，若非有意常驻循环请检查退出条件")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in {"bytearray", "bytes", "list"}:
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, int) and a.value >= 10 ** 7:
                    res.warnings.append(
                        f"第 {node.lineno} 行字面量分配 {a.value} 个元素（{node.func.id}），请确认内存占用")
                    break
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            for c in (node.left, node.right):
                if isinstance(c, ast.Constant) and isinstance(c.value, int) and c.value >= 10 ** 7:
                    res.warnings.append(
                        f"第 {node.lineno} 行出现 {c.value} 量级的乘法尺寸表达式，请确认内存占用")
                    break
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
