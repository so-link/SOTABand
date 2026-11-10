"""review_mode 分工具逐步 enforce 回归（批次2-11，评审 C-1 过渡形态）。

锁定的不变量：

- 内置工具在 review.py 严格名单下 block 命中为 0（enforce 可安全开启的前提，
  命中一旦回潮即失败——warn 级不计，enforce 只拦 block）
- SecurityConfig.review_mode_for 语义矩阵：全局 off/enforce 直接生效；
  全局 audit 时 REVIEW_ENFORCE_TOOLS（逗号分隔 / `*`）命中的工具升级 enforce
- 每次解析现读环境变量：运行中改 env 即生效，无需重启
- _security_preamble 按 tool_id 解析 _sec_mode：列表命中 → enforce，
  未命中 → audit；off 时输出恒等实现（不装钩子）
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture()
def clean_env(monkeypatch):
    """隔离 REVIEW_* 环境变量，避免宿主 .env / 系统环境串扰"""

    for k in ("REVIEW_MODE", "REVIEW_ENFORCE_TOOLS"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_builtin_tools_have_zero_block_hits():
    """内置工具 block 命中清零——分工具 enforce 的准入前提"""
    from pathlib import Path

    import config.settings as st
    from core.security.review import block_violations, review_code
    root = Path(getattr(st, "PROJECT_ROOT", Path.cwd())) / "resources" / "tools" / "implementations"
    if not root.is_dir():
        root = Path("resources/tools/implementations")
    offenders = []
    for tool in sorted(root.glob("*/tool.py")):
        viols = block_violations(review_code(tool.read_text(encoding="utf-8", errors="ignore")))
        if viols:
            offenders.append(f"{tool.parent.name}: " + "; ".join(
                f"L{v.line} {v.message[:60]}" for v in viols))
    assert not offenders, "内置工具出现 block 命中，enforce 会误伤存量:\n" + "\n".join(offenders)


def test_mode_for_global_modes(clean_env):
    from config.settings import SecurityConfig

    s = SecurityConfig()
    assert s.review_mode_for("any-tool") == "audit"  # 默认灰度
    clean_env.setenv("REVIEW_MODE", "off")
    # off 保持一键全关：per-tool 列表不得翻案
    clean_env.setenv("REVIEW_ENFORCE_TOOLS", "*")
    assert s.review_mode_for("any-tool") == "off"
    clean_env.setenv("REVIEW_MODE", "enforce")
    assert s.review_mode_for("any-tool") == "enforce"
    assert s.review_mode_for(None) == "enforce"


def test_mode_for_per_tool_upgrade(clean_env):
    from config.settings import SecurityConfig

    s = SecurityConfig()
    clean_env.setenv("REVIEW_ENFORCE_TOOLS", "tool-a, tool-b")
    assert s.review_mode_for("tool-a") == "enforce"
    assert s.review_mode_for("tool-b") == "enforce"
    assert s.review_mode_for("tool-c") == "audit"
    assert s.review_mode_for(None) == "audit"  # 无 tool_id（沙箱未命名）不升级

    clean_env.setenv("REVIEW_ENFORCE_TOOLS", "*")
    assert s.review_mode_for("whatever") == "enforce"

    clean_env.setenv("REVIEW_ENFORCE_TOOLS", "")
    assert s.review_mode_for("tool-a") == "audit"  # 运行中清空列表即回退


def test_mode_for_invalid_global_falls_back_to_audit(clean_env):
    from config.settings import SecurityConfig

    s = SecurityConfig()
    clean_env.setenv("REVIEW_MODE", "garbage")
    assert s.review_mode_for("tool-a") == "audit"


def test_preamble_mode_follows_tool_id(clean_env, tmp_path):
    """前导脚本的 _sec_mode 按工具解析——执行器与常驻 Worker 共用的注入点"""
    import json
    import re

    from core.executor.tool_executor import ToolExecutor

    clean_env.setenv("REVIEW_ENFORCE_TOOLS", "enforced-tool")

    def mode_of(tool_id: str | None) -> str:
        text = ToolExecutor._security_preamble(
            str(tmp_path), str(tmp_path), tool_id)
        m = re.search(r"_sec_mode = (\"[^\"]*\")", text)
        assert m, f"前导缺少 _sec_mode: {text[:200]}"
        return json.loads(m.group(1))

    assert mode_of("enforced-tool") == "enforce"
    assert mode_of("other-tool") == "audit"
    assert mode_of(None) == "audit"

    # off：不装钩子，仅恒等实现
    clean_env.setenv("REVIEW_MODE", "off")
    off_text = ToolExecutor._security_preamble(str(tmp_path), str(tmp_path), "enforced-tool")
    assert "addaudithook" not in off_text
    assert "_sec_merge" in off_text
