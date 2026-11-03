#!/usr/bin/env python3
"""把存量工具内联 _call_tool 的「只查内置根」改为「私有根优先 + 内置兜底」

背景：2026-11-04 起资源读取统一为覆盖层语义（私有优先）。老模板生成的
_call_tool 只查 resources/（内置根）——用户把工具「复制为我的副本」并
修改后，其他工具互调仍会命中内置旧版本。新模板（tool_builder 的
_resource_roots 版）已是私有优先，这里补齐存量两种内联变体。

变体 A（直接以 tool_name 为目录）：
    tool_dir = _PROJECT_ROOT / "resources" / ... / tool_name
变体 B（先查内置 registry.json 再定位）：
    reg_path = _PROJECT_ROOT / ...registry.json → tool_id → tool_dir

替换后的块只依赖头部必有的 _USER_HOME / _PROJECT_ROOT，不新增 helper。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 私有优先的实现目录探测（两种变体共用同一表达）
SEARCH = (
    "    # 资源根读取顺序：私有优先（用户「复制为副本」后的修改生效），内置兜底\n"
    "    _roots = [_USER_HOME / \"resources-private\", _PROJECT_ROOT / \"resources\"]\n"
)

A_OLD = """    tool_dir = _PROJECT_ROOT / "resources" / "tools" / "implementations" / tool_name
    tool_file = tool_dir / "tool.py"
"""
A_NEW = (
    SEARCH
    + "    tool_dir = next((r / \"tools\" / \"implementations\" / tool_name for r in _roots\n"
    + "                         if (r / \"tools\" / \"implementations\" / tool_name / \"tool.py\").exists()),\n"
    + "                    _roots[0] / \"tools\" / \"implementations\" / tool_name)\n"
    + "    tool_file = tool_dir / \"tool.py\"\n"
)

B_OLD = """    # 从 registry.json 中查找工具 ID（目录名）
    reg_path = _PROJECT_ROOT / "resources" / "tools" / "registry.json"
    tool_id = tool_name  # 默认用名称作为 ID
    if reg_path.exists():
        try:
            tools = json.loads(reg_path.read_text(encoding="utf-8"))
            # 先精确匹配 id，再模糊匹配 name
            for t in tools:
                if t.get("id") == tool_name or t.get("name") == tool_name:
                    tool_id = t["id"]
                    break
        except Exception:
            pass
    tool_dir = _PROJECT_ROOT / "resources" / "tools" / "implementations" / tool_id
"""
B_NEW = (
    SEARCH
    + "    # 从「私有 + 内置」registry.json 查找工具 ID（目录名），私有优先\n"
    + "    tool_id = tool_name  # 默认用名称作为 ID\n"
    + "    for _r in _roots:\n"
    + "        reg_path = _r / \"tools\" / \"registry.json\"\n"
    + "        if not reg_path.exists():\n"
    + "            continue\n"
    + "        try:\n"
    + "            for t in json.loads(reg_path.read_text(encoding=\"utf-8\")):\n"
    + "                if t.get(\"id\") == tool_name or t.get(\"name\") == tool_name:\n"
    + "                    tool_id = t[\"id\"]\n"
    + "                    break\n"
    + "        except Exception:\n"
    + "            continue\n"
    + "        if tool_id != tool_name:\n"
    + "            break\n"
    + "    tool_dir = next((r / \"tools\" / \"implementations\" / tool_id for r in _roots\n"
    + "                         if (r / \"tools\" / \"implementations\" / tool_id / \"tool.py\").exists()),\n"
    + "                    _roots[0] / \"tools\" / \"implementations\" / tool_id)\n"
)

changed_a, changed_b, missed = [], [], []
for tool_py in sorted(ROOT.glob("resources/tools/implementations/*/tool.py")):
    text = tool_py.read_text(encoding="utf-8")
    if "_call_tool" not in text:
        continue
    if B_OLD in text:
        tool_py.write_text(text.replace(B_OLD, B_NEW), encoding="utf-8")
        changed_b.append(tool_py.parent.name)
    elif A_OLD in text:
        tool_py.write_text(text.replace(A_OLD, A_NEW), encoding="utf-8")
        changed_a.append(tool_py.parent.name)
    elif "_roots = [" not in text:
        missed.append(tool_py.parent.name)

print(f"变体B改动 {len(changed_b)}: {changed_b}")
print(f"变体A改动 {len(changed_a)}: {changed_a}")
if missed:
    print(f"有 _call_tool 但两种模式都没匹配（需人工看）{len(missed)}: {missed}")
sys.exit(1 if missed else 0)
