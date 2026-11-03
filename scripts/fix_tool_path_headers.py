"""一次性修复：把工具实现里写死目录层级的头部换成 env 解析版

多用户路径规划（2026-11）后续。旧头部靠 ``Path(__file__).parents[N]`` /
``TOOL_DIR`` 往上数固定层数求项目根：

    内置 resources/tools/implementations/<id>/  → 5 层才对，写的是 4 层
    私有 USER_HOME/resources-private/tools/implementations/<id>/ → 层级更少，怎么数都错

结果就是 `_PROJECT_ROOT` 解析成不存在 `core/` 的目录，`_call_api`/`_call_tool`/
注册中心一用就 ImportError；`_DATA_DIR` 也指向系统根 data/（git 区）。

本脚本把该头部整块替换为与 TOOL_TEMPLATE 同构的 env 解析版：env 优先、
兜底按特征文件向上找（不假定层数）、数据目录落 USER_HOME/data。

用法：./venv/bin/python scripts/fix_tool_path_headers.py [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.user_paths import user_home  # noqa: E402

# 旧头部：从「# ── 项目根路径 ──」到 _DOWNLOADS_DIR 赋值（各工具此段逐字一致，
# 且与各自的 import 无关——额外 import 都在该块之前）
OLD_BLOCK_RE = re.compile(
    r"# ── 项目根路径 ──\n"
    r"_tool_dir = os\.environ\.get\(\"TOOL_DIR\", \"\"\)\n"
    r"if _tool_dir:\n"
    r"    _PROJECT_ROOT = Path\(_tool_dir\)\.resolve\(\)\.parent\.parent\.parent\.parent\n"
    r"else:\n"
    r"    _PROJECT_ROOT = Path\(__file__\)\.resolve\(\)\.parent\.parent\.parent\.parent\.parent\n"
    r"if str\(_PROJECT_ROOT\) not in sys\.path:\n"
    r"    sys\.path\.insert\(0, str\(_PROJECT_ROOT\)\)\n"
    r"\n"
    r"# ── 数据目录 ──\n"
    r"_DATA_DIR = _PROJECT_ROOT / \"data\"\n"
    r"_DOWNLOADS_DIR = _DATA_DIR / \"downloads\"\n"
)

NEW_BLOCK = '''# ── 运行目录（由平台注入，禁止写死绝对路径或按 __file__ 层级反推）──
# SOTABAND_ROOT      系统根：代码 + 内置资源，视为只读
# SOTABAND_USER_HOME 当前用户目录：本工具的一切产物写这里
# TOOL_DIR           本工具实现目录
def _env_path(name):
    v = os.environ.get(name, "").strip()
    return Path(v) if v else None

def _find_up(path, marker):
    """兜底：向上找 marker 所在目录。只按特征文件定位，不假定固定层级。"""
    p = path
    for _ in range(12):
        if (p / marker).exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return None

_tool_dir = _env_path("TOOL_DIR") or (Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd())
_PROJECT_ROOT = _env_path("SOTABAND_ROOT") or _find_up(_tool_dir, "core") or Path.cwd()
# 用户目录：env 缺失时按「SOTABAND_HOME 根 + uid」还原，绝不退回系统根
_USER_HOME = _env_path("SOTABAND_USER_HOME") or (
    (_env_path("SOTABAND_HOME") or _PROJECT_ROOT.parent / "SB-USER")
    / (os.environ.get("SOTABAND_USER_ID", "").strip() or "0")
)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 数据目录（产物一律落用户目录，系统根随 git 分发不可写脏）──
_DATA_DIR = _USER_HOME / "data"
_DOWNLOADS_DIR = _DATA_DIR / "downloads"

def _out_dir(*parts) -> Path:
    """本工具产物目录：USER_HOME/data/<工具或子目录>/<...>，自动创建。"""
    d = _DATA_DIR.joinpath(*parts) if parts else _DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d
'''

# 变体：__file__ 未定义时 try/except NameError 回退 cwd（synthesize-image 等）
OLD_BLOCK_VARIANT_B = re.compile(
    r"# ── 项目根路径 ──\n"
    r"_tool_dir = os\.environ\.get\(\"TOOL_DIR\", \"\"\)\n"
    r"if _tool_dir:\n"
    r"    _PROJECT_ROOT = Path\(_tool_dir\)\.resolve\(\)\.parent\.parent\.parent\.parent\n"
    r"else:\n"
    r"    try:\n"
    r"        _PROJECT_ROOT = Path\(__file__\)\.resolve\(\)\.parent\.parent\.parent\.parent\.parent\n"
    r"    except NameError:\n"
    r"        # 当通过 exec / -c 运行时 __file__ 未定义，回退到当前工作目录\n"
    r"        _PROJECT_ROOT = Path\.cwd\(\)\n"
    r"if str\(_PROJECT_ROOT\) not in sys\.path:\n"
    r"    sys\.path\.insert\(0, str\(_PROJECT_ROOT\)\)\n"
    r"\n"
    r"# ── 数据目录 ──\n"
    r"_DATA_DIR = _PROJECT_ROOT / \"data\"\n"
    r"_DOWNLOADS_DIR = _DATA_DIR / \"downloads\"\n"
)

# 变体：无「数据目录」段（部分工具不需要）
OLD_BLOCK_VARIANT_C = re.compile(
    r"# ── 项目根路径 ──\n"
    r"_tool_dir = os\.environ\.get\(\"TOOL_DIR\", \"\"\)\n"
    r"if _tool_dir:\n"
    r"    _PROJECT_ROOT = Path\(_tool_dir\)\.resolve\(\)\.parent\.parent\.parent\.parent\n"
    r"else:\n"
    r"    _PROJECT_ROOT = Path\(__file__\)\.resolve\(\)\.parent\.parent\.parent\.parent\.parent\n"
    r"if str\(_PROJECT_ROOT\) not in sys\.path:\n"
    r"    sys\.path\.insert\(0, str\(_PROJECT_ROOT\)\)\n"
)

# 私有根兜底：旧模板把私有根当成「项目根同级」，现已迁到 USER_HOME 下
OLD_PRIVATE_ROOT = re.compile(
    r"_PROJECT_ROOT\.parent / \"resources-private\""
)


def iter_targets() -> list[Path]:
    out: list[Path] = []
    for space in (ROOT / "resources" / "tools" / "implementations",
                  user_home() / "resources-private" / "tools" / "implementations"):
        if space.is_dir():
            out.extend(sorted(space.glob("*/tool.py")))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    changed: list[str] = []
    skipped: list[str] = []
    unmatched: list[str] = []

    for f in iter_targets():
        text = f.read_text(encoding="utf-8")
        if "_env_path(" in text:
            skipped.append(str(f))
            continue
        m = None
        for pat in (OLD_BLOCK_RE, OLD_BLOCK_VARIANT_B, OLD_BLOCK_VARIANT_C):
            m = pat.search(text)
            if m:
                break
        if not m:
            unmatched.append(str(f))
            continue
        new = text[:m.start()] + NEW_BLOCK + text[m.end():]
        # 私有根兜底改写（若该工具含 _call_tool 的旧式同级私有根假设）
        new, n_priv = OLD_PRIVATE_ROOT.subn(
            '_USER_HOME / "resources-private"', new
        )
        try:
            compile(new, str(f), "exec")
        except SyntaxError as e:
            print(f"  ✗ 跳过（替换后语法错误）: {f}: {e}")
            unmatched.append(str(f))
            continue
        if not args.dry_run:
            f.write_text(new, encoding="utf-8")
        changed.append(f"{f}" + (f"  (+私有根改写 {n_priv} 处)" if n_priv else ""))

    print(f"改写 {len(changed)} 个，已是新版 {len(skipped)} 个，未匹配 {len(unmatched)} 个")
    for c in changed:
        print("  ~", c)
    if unmatched:
        print("\n未匹配（需人工处理）:")
        for u in unmatched:
            print("  ?", u)


if __name__ == "__main__":
    main()
