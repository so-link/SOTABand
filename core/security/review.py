"""代码安全静态审查

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
from dataclasses import dataclass

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

        # 按行号排序，输出稳定
        violations.sort(key=lambda v: (v.line, v.rule))
        return violations


def review_code(code: str, strip_template: bool = True) -> list[Violation]:
    """便捷入口：审查代码返回违规列表。"""
    return StaticReviewer().review(code, strip_template=strip_template)


def block_violations(violations: list[Violation]) -> list[Violation]:
    """过滤出 block 级违规。"""
    return [v for v in violations if v.severity == "block"]
