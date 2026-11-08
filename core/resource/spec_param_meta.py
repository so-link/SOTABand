"""参数「必填」列的确定性读写 + ``param_meta`` 同步。

## 为什么需要

`param_meta` 只在注册时生成（还是 LLM 抽取），而 MD 表格是使用者直接编辑的。
两者一旦脱节，就会出现「界面上把参数改成可选，对话 Agent 却还在追问」——
因为运行时读的是 `param_meta`，不是 MD。

本模块把两者绑定起来，且**全程确定性、不调 LLM**：

- :func:`set_param_required` 改 MD 表格里该参数行的「必填」单元格（只改这一格）
- :func:`sync_param_meta` 以 MD 为准重算 `param_meta`，同时保留 LLM 抽取的
  ``hints`` 等 MD 里没有的信息
- :func:`preserve_manual_required` 供注册时使用：参数名集合没变时，沿用使用者
  在界面上手动设置的 required，避免下一次注册被 LLM 改回去
"""

from __future__ import annotations

from core.resource.spec_outline import (
    SpecNode,
    parse_markdown_outline,
    parse_table,
)

REQUIRED_YES = "是"
REQUIRED_NO = "否"


def _required_column(header: list[str]) -> str:
    """找出表头里的「必填」列名（容忍「是否必填」「必填?」等写法）。"""
    for h in header or []:
        name = (h or "").strip()
        if "必填" in name:
            return name
    return ""


def _column_index(header: list[str], col_name: str) -> int:
    for i, h in enumerate(header or []):
        if (h or "").strip() == col_name:
            return i
    return -1


def _replace_nth_cell(line: str, index: int, value: str) -> str:
    """把表格行第 index 个单元格改成 value，**保留原有对齐空格**。

    不用 ``update_table_cell`` 的整表重渲染：那会把整张表的列宽压缩成
    ``| a | b |``，导致一次改一个格子产生整篇 diff。这里只动目标单元格。
    """
    parts = line.split("|")
    pos = index + 1
    if pos <= 0 or pos >= len(parts):
        return line
    cell = parts[pos]
    stripped = cell.strip()
    if stripped:
        lead = cell[: len(cell) - len(cell.lstrip())]
        trail = cell[len(cell.rstrip()):]
    else:
        lead, trail = " ", " "
    parts[pos] = f"{lead}{value}{trail}"
    return "|".join(parts)


def _name_column_index(header: list[str]) -> int:
    """找出参数名所在列下标（默认第 0 列）。"""
    for i, h in enumerate(header or []):
        name = (h or "").strip()
        if "参数名" in name:
            return i
    for i, h in enumerate(header or []):
        if (h or "").strip() in ("参数", "名称", "字段"):
            return i
    return 0


def find_param_rows(spec_md: str, name: str, tool_id: str = ""):
    """定位某参数在文档各表格中的行。

    Returns:
        (outline, hits)；hits 为 ``[(node, [row_index...], required_col_name)]``。
        多模式工具里同一参数可能在多个模式下各有一行，因此返回值是列表。
    """
    target = (name or "").strip()
    outline = parse_markdown_outline(spec_md, tool_id=tool_id)
    hits: list[tuple[SpecNode, list[int], int]] = []
    if not target:
        return outline, hits

    for node in outline.nodes:
        block = parse_table(node)
        if block is None or not block.header:
            continue
        col = _required_column([str(h).strip() for h in block.header])
        if not col:
            continue
        ci = _column_index([str(h).strip() for h in block.header], col)
        if ci < 0:
            continue
        ni = _name_column_index(block.header)
        rows = [
            i for i, r in enumerate(block.rows)
            if len(r) > ni and str(r[ni] or "").strip() == target
        ]
        if rows:
            hits.append((node, rows, ci))
    return outline, hits


def set_param_required(spec_md: str, name: str, required: bool, tool_id: str = "") -> tuple[str, int]:
    """把某参数的「必填」单元格改成「是」/「否」。

    只改目标单元格，不动同表其他行与文档其余段落（按行范围替换，不走字符串查找）。

    Returns:
        (新的完整 MD, 实际改动的单元格数)。参数不存在时原样返回 0。
    """
    outline, hits = find_param_rows(spec_md, name, tool_id=tool_id)
    if not hits:
        return spec_md, 0

    value = REQUIRED_YES if required else REQUIRED_NO
    # 直接按绝对行号原位改这一行：不重建节点、不重排表格，
    # 因此不会产生「整篇 diff」（replace_node_content 会 rstrip 掉节点末尾空行）。
    lines = spec_md.split("\n")
    changed = 0

    for node, rows, col_index in hits:
        block = parse_table(node)
        if block is None:
            continue
        data_start = node.line_start + block.line_start + 2   # 跳过表头与分隔行
        for row in rows:
            li = data_start + row
            if 0 <= li < len(lines):
                lines[li] = _replace_nth_cell(lines[li], col_index, value)
                changed += 1

    if changed == 0:
        return spec_md, 0
    return "\n".join(lines), changed


def sync_param_meta(spec_md: str, existing: list[dict] | None = None) -> list[dict]:
    """以 MD 表格为准重算 ``param_meta``，保留 MD 里没有的字段。

    保留项：``hints``（LLM 抽取的示例输入）、旧条目独有的 ``mode``。
    覆盖项：``required`` / ``type`` / ``default`` / ``desc``（使用者刚改的就是这些）。
    """
    from core.resource.builder.tool_builder import ToolCodeBuilder

    parsed = ToolCodeBuilder._parse_spec_inputs(spec_md) or []
    old_by_name = {
        str(p.get("name") or "").strip(): p for p in (existing or []) if p.get("name")
    }

    merged_list: list[dict] = []
    for p in parsed:
        name = str(p.get("name") or "").strip()
        old = old_by_name.get(name) or {}
        merged = {**old, **p}
        if old.get("hints"):
            merged["hints"] = old["hints"]
        if old.get("mode") and not merged.get("mode"):
            merged["mode"] = old["mode"]
        merged_list.append(merged)
    return merged_list


def preserve_manual_required(new_meta: list[dict], old_meta: list[dict] | None) -> list[dict]:
    """参数名集合未变时，沿用旧条目里的 ``required``（人工设置优先）。

    参数集合一旦变化（新增/删除参数），说明文档结构变了，此时以新结果为准 ——
    否则新参数会继承到错误的必填状态。
    """
    old_by_name = {
        str(p.get("name") or "").strip(): p for p in (old_meta or []) if p.get("name")
    }
    if not old_by_name or not new_meta:
        return new_meta

    new_names = {str(p.get("name") or "").strip() for p in new_meta if p.get("name")}
    if new_names != set(old_by_name):
        return new_meta

    for p in new_meta:
        name = str(p.get("name") or "").strip()
        old = old_by_name.get(name) or {}
        if "required" in old:
            p["required"] = bool(old["required"])
        if old.get("mode") and not p.get("mode"):
            p["mode"] = old["mode"]
    return new_meta
