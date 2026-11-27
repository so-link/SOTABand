"""V2 静态审查：写入路径越界规则回归。

锁定的不变量：
- 写死 ``SB-USER/<uid>`` 的路径 → ``CROSS_USER_WRITE``（block，跨用户/系统层）；
- 写 ``TOOL_DIR`` / ``__file__`` 目录 → ``TOOL_DIR_WRITE``（warn，公共资产层）；
- 动态路径（``user_home()`` / ``_out_dir()`` / 变量拼接）不误报；
- ``gate()`` 跟随 ``review_mode``：audit 不阻断、enforce 阻断。
"""

from __future__ import annotations

import logging

import pytest

from core.security.review import gate, review_code


def _rules(code: str) -> set[str]:
    return {v.rule for v in review_code(code)}


def _block_rules(code: str) -> set[str]:
    return {v.rule for v in review_code(code) if v.severity == "block"}


OPEN_FIXED_USER = '''
def execute(**kwargs):
    with open("/root/SB-USER/100/runs/a.txt", "w") as f:
        f.write("x")
    return {"status": "success"}
'''

OPEN_JOIN_FIXED_USER = '''
import os

def execute(**kwargs):
    p = os.path.join("/root/SB-USER/0", "runs", "a.txt")
    with open(p, "w") as f:
        f.write("x")
    return {"status": "success"}
'''

PATHLIB_FIXED_USER = '''
from pathlib import Path

def execute(**kwargs):
    Path("/root/SB-USER/1000/data/a.txt").write_text("x", encoding="utf-8")
    return {"status": "success"}
'''

OPEN_TOOL_DIR = '''
import os

def execute(**kwargs):
    with open(os.path.join(TOOL_DIR, "runs", "a.txt"), "w") as f:
        f.write("x")
    return {"status": "success"}
'''

PATHLIB_FILE_PARENT = '''
from pathlib import Path

def execute(**kwargs):
    (Path(__file__).parent / "runs" / "a.txt").write_text("x", encoding="utf-8")
    return {"status": "success"}
'''

OPEN_DYNAMIC_HOME = '''
from pathlib import Path

def execute(**kwargs):
    with open(user_home() / "data" / "a.txt", "w") as f:
        f.write("x")
    return {"status": "success"}
'''

OPEN_OUT_DIR = '''
def execute(**kwargs):
    with open(_out_dir("runs") / "a.txt", "w") as f:
        f.write("x")
    return {"status": "success"}
'''


def test_fixed_user_directory_write_is_blocked():
    assert "CROSS_USER_WRITE" in _block_rules(OPEN_FIXED_USER)


def test_joined_fixed_user_directory_write_is_blocked():
    assert "CROSS_USER_WRITE" in _block_rules(OPEN_JOIN_FIXED_USER)


def test_pathlib_write_to_fixed_user_is_blocked():
    assert "CROSS_USER_WRITE" in _block_rules(PATHLIB_FIXED_USER)


def test_write_under_tool_dir_is_blocked():
    violations = review_code(OPEN_TOOL_DIR)
    hits = [v for v in violations if v.rule == "TOOL_DIR_WRITE"]
    assert hits and hits[0].severity == "block"


def test_write_under_file_parent_is_blocked():
    violations = review_code(PATHLIB_FILE_PARENT)
    hits = [v for v in violations if v.rule == "TOOL_DIR_WRITE"]
    assert hits and hits[0].severity == "block"


def test_dynamic_user_home_is_not_flagged():
    assert "CROSS_USER_WRITE" not in _rules(OPEN_DYNAMIC_HOME)
    assert "TOOL_DIR_WRITE" not in _rules(OPEN_DYNAMIC_HOME)


def test_out_dir_is_not_flagged():
    assert "CROSS_USER_WRITE" not in _rules(OPEN_OUT_DIR)
    assert "TOOL_DIR_WRITE" not in _rules(OPEN_OUT_DIR)


def test_gate_follows_review_mode(monkeypatch):
    monkeypatch.setenv("REVIEW_ENFORCE_TOOLS", "")

    monkeypatch.setenv("REVIEW_MODE", "audit")
    blocks, all_violations = gate(OPEN_FIXED_USER, "demo-tool")
    assert blocks == []
    assert any(v.rule == "CROSS_USER_WRITE" for v in all_violations)

    monkeypatch.setenv("REVIEW_MODE", "enforce")
    blocks, _ = gate(OPEN_FIXED_USER, "demo-tool")
    assert any(v.rule == "CROSS_USER_WRITE" for v in blocks)

    monkeypatch.setenv("REVIEW_MODE", "off")
    blocks, all_violations = gate(OPEN_FIXED_USER, "demo-tool")
    assert blocks == [] and all_violations == []


def test_gate_logs_at_warning_level(monkeypatch, caplog):
    """audit 命中也必须能在默认 logging 配置下看到（root 默认 WARNING）。"""
    monkeypatch.setenv("REVIEW_MODE", "audit")
    monkeypatch.setenv("REVIEW_ENFORCE_TOOLS", "")

    with caplog.at_level(logging.WARNING, logger="core.security.review"):
        gate(OPEN_FIXED_USER, "demo-tool")

    records = [r for r in caplog.records if "static review" in r.getMessage()]
    assert records
    assert records[0].levelno == logging.WARNING
    assert "mode=audit" in records[0].getMessage()


INDIRECT_TOOL_DIR = '''
import os
from pathlib import Path
import cv2

def _tool_directory():
    configured = os.environ.get("TOOL_DIR", "")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parent

def _resolve(value, root):
    path = Path(str(value)).expanduser()
    return (path if path.is_absolute() else root / path).resolve()

def execute(**kwargs):
    root = _tool_directory()
    output_root = _resolve(kwargs.get("output_dir") or "runs", root)
    output_dir = output_root / "run-1"
    output_dir.mkdir(parents=True, exist_ok=False)
    image_path = output_dir / "a.jpg"
    cv2.imwrite(str(image_path), None)
    return {"status": "success"}
'''

INDIRECT_USER_HOME = '''
from pathlib import Path

def _out_base():
    from core.user_paths import user_home
    return user_home()

def execute(**kwargs):
    out = _out_base() / "data" / "runs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "a.txt").write_text("x", encoding="utf-8")
    return {"status": "success"}
'''


def test_indirect_tool_dir_via_helper_function_is_blocked():
    """TOOL_DIR/__file__ 藏在辅助函数里，也必须追踪到（真实工具的常见写法）。"""
    hits = [v for v in review_code(INDIRECT_TOOL_DIR) if v.rule == "TOOL_DIR_WRITE"]
    assert len(hits) >= 2  # mkdir + imwrite 两处写入
    assert all(v.severity == "block" for v in hits)


def test_indirect_user_home_is_not_flagged():
    rules = _rules(INDIRECT_USER_HOME)
    assert "TOOL_DIR_WRITE" not in rules
    assert "CROSS_USER_WRITE" not in rules
