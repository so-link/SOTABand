"""代码安全静态审查

## 与 core/security/code_scan.py 的关系（两条闸门，各司其职）

- ``code_scan.py``：破坏性/逃逸底线（shell 执行、动态执行、socket/ctypes），
  名单以「内置工具零命中」为回归基线，是所有执行路径的最后闸门。
- 本模块：core API 约束线，策略更严——本地文件操作/子进程/目录增删必须走
  core API 或 _call_tool。默认 review_mode=audit 只记录；enforce 切换前需
  先清零存量内置工具命中（名单与 code_scan 基线存在有意差异）。

## 职责
对工具 / Agent 代码做 AST 级静态审查，在代码执行或注册前发现：

- 危险系统调用（os.system / subprocess / eval / exec 等）
- 绕过 core API 的直接文件删除/创建操作
- 动态导入与常见混淆手法（getattr 拼名、importlib 变量名）

## 审查范围
工具代码由「系统模板头部 + LLM 生成体」组成（见 tool_builder.TOOL_TEMPLATE），
模板内的 ``subprocess`` 等用法是系统可信代码，不算违规。因此默认只审查
模板分界标记之后的生成体部分；无标记的老代码全量审查。

## 级别
- ``block``: 必须修复（自动调试：跳过执行并要求 LLM 改；注册：拒绝）
- ``warn`` : 提醒（记录并展示，不阻塞）

本模块只依赖标准库 ``ast``，可被后端、构建器、测试任意引用。
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 模板与生成体的分界标记（与 tool_builder.TOOL_TEMPLATE 保持一致）
TEMPLATE_MARKER = "# === 头部结束，以下由 LLM 生成 ==="


@dataclass
class Violation:
    """一条审查违规记录"""

    rule: str       # 规则 ID，如 BANNED_CALL / RAW_OPEN_WRITE
    severity: str   # block | warn
    message: str
    line: int = 0
    snippet: str = ""

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "line": self.line,
            "snippet": self.snippet,
        }


class StaticReviewer:
    """基于 AST 的静态代码审查器（无第三方依赖）"""

    # ── 高危调用（block 级）：本地文件操作/系统调用必须走 core API ──
    BANNED_CALLS: dict[str, str] = {
        # 系统命令执行
        "os.system": "禁止直接执行系统命令，请改用 core API 或 _call_tool",
        "os.popen": "禁止直接执行系统命令",
        "os.spawnl": "禁止创建系统进程",
        "os.spawnv": "禁止创建系统进程",
        "os.execv": "禁止替换当前进程",
        "os.execl": "禁止替换当前进程",
        "os.kill": "禁止进程信号操作",
        "pty.spawn": "禁止创建伪终端",
        "subprocess.run": "禁止创建子进程，工具间调用请用模板提供的 _call_tool",
        "subprocess.Popen": "禁止创建子进程",
        "subprocess.call": "禁止创建子进程",
        "subprocess.check_output": "禁止创建子进程",
        "subprocess.check_call": "禁止创建子进程",
        # 文件/目录增删改 —— 必须走 core API
        "os.remove": "文件删除必须走 core API",
        "os.unlink": "文件删除必须走 core API",
        "os.rmdir": "目录删除必须走 core API",
        "os.removedirs": "目录删除必须走 core API",
        "os.mkdir": "目录创建必须走 core API",
        "os.makedirs": "目录创建必须走 core API",
        "shutil.rmtree": "目录删除必须走 core API",
        "shutil.move": "文件移动必须走 core API",
        "shutil.copytree": "禁止递归复制目录",
        "os.chmod": "禁止修改文件权限",
        "os.chown": "禁止修改文件属主",
        "os.symlink": "禁止创建符号链接",
        "os.link": "禁止创建硬链接",
        "os.truncate": "禁止截断文件",
        "os.replace": "文件替换必须走 core API",
        "os.rename": "文件重命名必须走 core API",
        "shutil.copy": "文件复制请走 core API",
        # 环境篡改
        "os.putenv": "禁止修改环境变量",
        # 代码执行原语
        "eval": "禁止使用 eval",
        "exec": "禁止使用 exec",
        "compile": "禁止动态编译代码",
        "__import__": "禁止 __import__，请使用 import 语句",
        "os._exit": "禁止直接终止进程",
    }

    # getattr 动态拼名时的黑名单短名（防 getattr(os, "system") 绕过）
    BANNED_GETATTR_NAMES = {
        "system", "popen", "spawnl", "spawnv", "execv", "execl", "kill",
        "remove", "unlink", "rmdir", "removedirs", "mkdir", "makedirs",
        "chmod", "chown", "symlink", "link", "truncate", "replace", "rename",
        "rmtree", "copytree", "putenv", "_exit",
    }

    # 允许 getattr 拼名的接收对象（这些模块才有危险方法）
    GETATTR_TARGET_MODULES = {"os", "sys", "shutil", "subprocess", "pty", "pathlib"}

    # ── 路径越界规则（V2）──
    # 写入目标里出现固定用户目录：SB-USER/<uid> 或 SB-USER/ 根。
    # 平台的多用户布局是 <USER_HOME_ROOT>/<uid>/，任何写死某一层的路径
    # 都会造成跨用户产物（调用者看不到、属主被写脏），必须拦。
    CROSS_USER_RE = re.compile(r"SB-USER[/\\](?:\d+|default|\{|$)", re.IGNORECASE)
    # 工具自身目录是只读资产层（models/、requirements.txt 随工具分发），
    # 产物写这里会写脏公共资源层；先 warn 观察，V3 由运行时白名单强制。
    TOOL_DIR_MARKERS = {"TOOL_DIR", "__file__"}
    # 写入型调用：目标参数位置固定，便于从 AST 直接取路径表达式。
    # mkdir/touch 也算：目录创建会先在工具目录下落地，之后再往里写文件。
    WRITE_METHODS = {"write_text", "write_bytes", "mkdir", "touch"}

    def _dotted_name(self, node: ast.AST) -> str:
        """把 Attribute/Name 链还原成 'os.path.join' 形式"""
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    def _snippet(self, body: str, lineno: int) -> str:
        lines = body.splitlines()
        if 1 <= lineno <= len(lines):
            return lines[lineno - 1].strip()[:120]
        return ""

    # ── 路径规则辅助（只做保守的常量折叠，不追求完备）──

    def _collect_aliases(self, tree: ast.AST) -> dict[str, ast.AST]:
        """收集 ``name = 表达式`` 的常量别名，供路径解析用。

        只收单目标赋值；同名多次出现视为不可靠并剔除（不同分支/函数里的
        同名变量不一定是同一个值，宁可漏报）。
        """
        alias: dict[str, ast.AST] = {}
        conflicts: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                tgt = node.targets[0]
                if isinstance(tgt, ast.Name):
                    if tgt.id in alias:
                        conflicts.add(tgt.id)
                    else:
                        alias[tgt.id] = node.value
        for name in conflicts:
            alias.pop(name, None)
        return alias

    def _collect_functions(self, tree: ast.AST) -> dict[str, ast.FunctionDef]:
        """收集函数名 → 定义，供「写入目标经过辅助函数」的追踪。

        生成代码常见形态是 ``root = _tool_directory()`` /
        ``out = _resolve("runs", root)``，TOOL_DIR/__file__ 只出现在函数体里。
        只收单一定义；同名多次出现视为不可靠并剔除。
        """
        funcs: dict[str, ast.FunctionDef] = {}
        conflicts: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name in funcs:
                    conflicts.add(node.name)
                else:
                    funcs[node.name] = node
        for name in conflicts:
            funcs.pop(name, None)
        return funcs

    def _const_prefix(self, node: ast.AST | None,
                      aliases: dict[str, ast.AST] | None = None,
                      depth: int = 0) -> str:
        """尽力折叠出路径表达式的字符串前缀。

        只处理「一眼能看出是常量」的形态：字符串常量、f-string 的常量片段、
        ``os.path.join`` / ``Path`` / ``str`` 的首参、``a / b`` 的左侧，
        以及单层常量别名（``p = os.path.join("/root/SB-USER/0", ...)``）。
        任何运行时值参与（如 ``user_home()``、``kwargs["x"]``）都返回空串——
        宁可漏报，不能把合法动态路径误判成越界。
        """
        if node is None or depth > 3:
            return ""
        if isinstance(node, ast.Name) and aliases:
            value = aliases.get(node.id)
            if value is not None:
                return self._const_prefix(value, aliases, depth + 1)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self._const_prefix(node.left, aliases, depth + 1)
        if isinstance(node, ast.Call):
            name = self._dotted_name(node.func)
            if name in ("os.path.join", "Path", "pathlib.Path", "str") and node.args:
                return self._const_prefix(node.args[0], aliases, depth + 1)
        return ""

    def _mentions_name(self, node: ast.AST | None, names: set[str],
                       aliases: dict[str, ast.AST] | None = None,
                       functions: dict[str, ast.FunctionDef] | None = None,
                       depth: int = 0) -> bool:
        """表达式中是否引用了给定名字/属性（如 TOOL_DIR、``__file__``）。

        同时穿透两类间接：变量别名（``p = join(TOOL_DIR, ...)``）与函数
        返回值（``root = _tool_directory()``）。生成代码更常见的是后者——
        只看别名会在 ``_resolve("runs", root)`` 这种写法上漏报。
        """
        if node is None or depth > 8:
            return False
        # 条件表达式（a if cond else b）：只有两条分支都指向受限目录才算
        # 确定越界。典型误报是「内置资产存在则用只读路径，否则写用户目录」
        # ——运行时走哪个分支静态不可知，不能按 block 处理。
        if isinstance(node, ast.IfExp):
            return (
                self._mentions_name(node.body, names, aliases, functions, depth + 1)
                and self._mentions_name(node.orelse, names, aliases, functions, depth + 1)
            )
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                if sub.id in names:
                    return True
                # 项目根不是工具目录：它的定位函数里也有 Path(__file__)，
                # 继续追会把 _USER_HOME / _PROJECT_ROOT 的兜底路径误判成
                # 工具目录（paper-review 的 _USER_HOME 就是这样）。
                if sub.id in ("_PROJECT_ROOT", "PROJECT_ROOT"):
                    continue
                if aliases and sub.id in aliases:
                    if self._mentions_name(aliases[sub.id], names, aliases, functions, depth + 1):
                        return True
                if functions and sub.id in functions:
                    if self._mentions_name(functions[sub.id], names, aliases, functions, depth + 1):
                        return True
            if isinstance(sub, ast.Attribute) and sub.attr in names:
                return True
            # os.environ.get("TOOL_DIR") 里的 TOOL_DIR 是字符串常量，不是 Name
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value in names:
                return True
        return False

    def _write_target(self, node: ast.Call) -> ast.AST | None:
        """返回写入调用的目标路径表达式；非写入调用返回 None。

        覆盖三类常见落盘方式：
        - ``open(path, "w"/"a"/"x"/"+")``；
        - ``Path(...).write_text()/write_bytes()``；
        - ``cv2.imwrite(path, img)`` 等 ``*imwrite``。
        """
        dotted = self._dotted_name(node.func)

        if dotted == "open" and node.args:
            mode = "r"
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if not any(c in mode for c in "wax+"):
                return None
            return node.args[0]

        if isinstance(node.func, ast.Attribute) and node.func.attr in self.WRITE_METHODS:
            return node.func.value

        if dotted.endswith("imwrite") and node.args:
            return node.args[0]

        return None

    def review(self, code: str, strip_template: bool = True) -> list[Violation]:
        """审查代码，返回违规列表（空列表 = 通过）。"""
        body = code
        line_offset = 0
        if strip_template and TEMPLATE_MARKER in code:
            idx = code.index(TEMPLATE_MARKER) + len(TEMPLATE_MARKER)
            body = code[idx:]
            line_offset = code[:idx].count("\n")

        try:
            tree = ast.parse(body)
        except SyntaxError as e:
            return [Violation(
                rule="SYNTAX_ERROR", severity="block",
                message=f"代码语法错误: {e.msg}", line=e.lineno or 0,
            )]

        violations: list[Violation] = []
        aliases = self._collect_aliases(tree)
        functions = self._collect_functions(tree)

        for node in ast.walk(tree):
            # ── 规则1: 危险调用 ──
            if isinstance(node, ast.Call):
                dotted = self._dotted_name(node.func)
                if dotted in self.BANNED_CALLS:
                    violations.append(Violation(
                        rule="BANNED_CALL", severity="block",
                        message=f"{dotted}() — {self.BANNED_CALLS[dotted]}",
                        line=(node.lineno or 0) + line_offset,
                        snippet=self._snippet(body, node.lineno or 0),
                    ))
                    continue

                # ── 规则2: getattr(os, "system") 拼名绕过 ──
                if dotted == "getattr" and len(node.args) >= 2:
                    tgt = node.args[0]
                    name_arg = node.args[1]
                    tgt_name = tgt.id if isinstance(tgt, ast.Name) else ""
                    if (tgt_name in self.GETATTR_TARGET_MODULES
                            and isinstance(name_arg, ast.Constant)
                            and isinstance(name_arg.value, str)
                            and name_arg.value in self.BANNED_GETATTR_NAMES):
                        violations.append(Violation(
                            rule="OBFUSCATED_CALL", severity="block",
                            message=f'getattr({tgt_name}, "{name_arg.value}") — 混淆调用危险函数',
                            line=(node.lineno or 0) + line_offset,
                            snippet=self._snippet(body, node.lineno or 0),
                        ))
                        continue

                # ── 规则3: importlib 动态导入变量名 ──
                if dotted in ("importlib.import_module", "__import__"):
                    if node.args and not isinstance(node.args[0], ast.Constant):
                        violations.append(Violation(
                            rule="DYNAMIC_IMPORT", severity="warn",
                            message="动态 import 变量名，无法静态确认模块安全性",
                            line=(node.lineno or 0) + line_offset,
                            snippet=self._snippet(body, node.lineno or 0),
                        ))
                        continue

                # ── 规则4: 裸 open() 写文件（warn）──
                if dotted == "open" and len(node.args) >= 1:
                    mode = "r"
                    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                        mode = str(node.args[1].value)
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            mode = str(kw.value.value)
                    if any(c in mode for c in "wa+"):
                        violations.append(Violation(
                            rule="RAW_OPEN_WRITE", severity="warn",
                            message=f'open(..., mode="{mode}") — 直接写文件，建议通过 core API 完成',
                            line=(node.lineno or 0) + line_offset,
                            snippet=self._snippet(body, node.lineno or 0),
                        ))

                # ── 规则5/6: 写入路径越界（V2）──
                # 只对「能折叠出常量前缀」或「引用 TOOL_DIR/__file__」的目标告警，
                # 任何变量参与（user_home()/kwargs）都放过，避免误伤合法动态路径。
                target = self._write_target(node)
                if target is not None:
                    prefix = self._const_prefix(target, aliases)
                    if self.CROSS_USER_RE.search(prefix):
                        violations.append(Violation(
                            rule="CROSS_USER_WRITE", severity="block",
                            message="写入路径指向 SB-USER 下的固定用户目录（跨用户/系统资源层），"
                                    "请改用 _user_home() / _out_dir(...) 在调用者目录下生成路径",
                            line=(node.lineno or 0) + line_offset,
                            snippet=self._snippet(body, node.lineno or 0),
                        ))
                    elif self._mentions_name(target, self.TOOL_DIR_MARKERS, aliases, functions):
                        violations.append(Violation(
                            rule="TOOL_DIR_WRITE", severity="block",
                            message="写入目标落在工具自身目录（TOOL_DIR/__file__）——"
                                    "该目录是随工具分发的只读资产层，产物请写 _out_dir(...)",
                            line=(node.lineno or 0) + line_offset,
                            snippet=self._snippet(body, node.lineno or 0),
                        ))

        # 按行号排序，输出稳定
        violations.sort(key=lambda v: (v.line, v.rule))
        return violations


def review_code(code: str, strip_template: bool = True) -> list[Violation]:
    """便捷入口：审查代码返回违规列表。"""
    return StaticReviewer().review(code, strip_template=strip_template)


def block_violations(violations: list[Violation]) -> list[Violation]:
    """过滤出 block 级违规。"""
    return [v for v in violations if v.severity == "block"]


def gate(code: str, tool_id: str | None = None) -> tuple[list[Violation], list[Violation]]:
    """按 review_mode 执行审查，返回 ``(需阻断的违规, 全部违规)``。

    - ``off``     → 空 / 空，完全不审；
    - ``audit``   → 阻断为空，全部违规保留（供调用方记录或展示，灰度期不拦）；
    - ``enforce`` → 阻断 = block 级违规。

    所有落盘/执行入口统一走这里，避免每个路由自己判断模式——历史上
    auto-debug 就是因为没有判断模式，在 audit 灰度下仍按 enforce 驳回。
    """
    try:
        from config.settings import settings

        mode = settings.security.review_mode_for(tool_id)
    except Exception:  # 配置不可用时按灰度处理，绝不因审查而阻断
        mode = "audit"

    if mode == "off":
        return [], []

    violations = review_code(code)
    blocks = block_violations(violations)
    if violations:
        # 用 warning 而不是 info：项目没有配置 logging，root 默认 WARNING，
        # info 会被 lastResort 丢弃——audit 灰度期就完全看不到命中。
        # 每次命中只打一行，量很小，不构成日志噪音。
        logger.warning(
            "static review: target=%s mode=%s violations=%d blocks=%d",
            tool_id or "-", mode, len(violations), len(blocks),
        )
    return (blocks if mode == "enforce" else []), violations
