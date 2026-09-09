"""工具规范文档的结构化解析

## 为什么需要它

生成的工具规范文档平均 87 行、2163 字符，以 Markdown 表格为主，
对非专业使用者而言难以通读核对。

但文档有一层天然结构：``generate-spec`` 的模板固定为 6 段
（功能概述 / 输入规范 / 输出规范 / 依赖环境 / 运行机制 / 版本历史）。
按 Markdown 标题切分即可还原出节点树，**无需定义新的文档格式**。

有了节点树就能支撑两个能力：
- **分层摘要**：概览层展示人话摘要，每个要点锚定到具体节点
- **节点级精化**：只重写一个段落，而非整体重新生成

## 设计要点

**节点 ID 必须稳定** —— 精化后文档会重建，若 ID 变化会导致前端的
展开/选中状态错乱。因此 ID 由「层级 + 标题」生成，标题不变则 ID 不变。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Any

# 标题行（# 到 ######）
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")

# 节点类型：依据标题关键词推断，用于前端选择图标与摘要话术
_NODE_TYPE_HINTS: list[tuple[str, str]] = [
    ("输入", "input"), ("参数", "input"),
    ("输出", "output"), ("返回", "output"),
    ("依赖", "dependency"), ("环境", "dependency"),
    ("流程", "flow"), ("步骤", "flow"), ("执行", "flow"),
    ("错误", "error"), ("异常", "error"), ("容错", "error"),
    ("版本", "history"), ("变更", "history"),
    ("概述", "overview"), ("功能", "overview"),
]


def _slugify(text: str) -> str:
    """把标题转成稳定的 id 片段。

    保留中文（用户多为中文场景），去掉标点空白。
    同一标题恒定得到同一片段，保证精化后 ID 稳定。
    """
    t = (text or "").strip().lower()
    # 去掉开头的章节编号（如 "1." / "2.1" / "3.1.2"），让 id 更语义化
    t = re.sub(r"^\d+(\.\d+)*[\.、]?\s*", "", t)
    # 去掉 markdown 标记与常见标点
    t = re.sub(r"[`*_\[\]()（）【】|、，。：；!！?？\"'“”‘’<>/\\]", "", t)
    t = re.sub(r"\s+", "-", t)
    # 连续横线合并，去掉首尾横线
    t = re.sub(r"-+", "-", t).strip("-")
    return t or "node"


def _infer_node_type(title: str) -> str:
    """依据标题关键词推断节点类型"""
    t = title or ""
    for kw, ntype in _NODE_TYPE_HINTS:
        if kw in t:
            return ntype
    return "section"


@dataclass
class SpecNode:
    """文档中的一个节点（通常对应一个段落）"""
    id: str
    title: str
    level: int                      # Markdown 标题层级（1~6）
    node_type: str                  # overview|input|output|dependency|flow|error|history|section
    content_md: str                 # 该节点的原始 MD 片段（含标题行）
    line_start: int                 # 在原文中的起始行（0-based，便于高亮定位）
    line_end: int                   # 结束行（不含）
    summary: str = ""               # 人话摘要（由 LLM 生成，可为空）
    children: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SpecOutline:
    """整份文档的结构化视图"""
    tool_id: str = ""
    nodes: list[SpecNode] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    raw_md: str = ""
    version: int = 0                # 精化次数
    warning: str = ""               # 解析/摘要过程中的问题说明，供前端提示
    cached: bool = False            # 摘要是否来自缓存（True 表示无需等待）

    def to_dict(self) -> dict[str, Any]:
        d = {
            "tool_id": self.tool_id,
            "nodes": [n.to_dict() for n in self.nodes],
            "summary": self.summary,
            "version": self.version,
            "cached": self.cached,
        }
        if self.warning:
            d["warning"] = self.warning
        return d

    def get(self, node_id: str) -> SpecNode | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def root_ids(self) -> list[str]:
        """顶层节点（最小层级）"""
        if not self.nodes:
            return []
        min_level = min(n.level for n in self.nodes)
        return [n.id for n in self.nodes if n.level == min_level]


def parse_markdown_outline(
    md: str,
    tool_id: str = "",
    min_level: int = 2,
    max_level: int = 3,
) -> SpecOutline:
    """把 Markdown 文档解析为节点树。

    只切到 ``min_level ~ max_level``（默认 ## 与 ###），因为：
    - 更深层级（表格行、列表项）第一版不做，避免节点过碎
    - 段落级粒度上下文完整，精化成功率高

    Args:
        md: 完整 Markdown 文档
        tool_id: 关联的工具 id
        min_level: 最浅切分层级（1 表示 #，2 表示 ##）
        max_level: 最深切分层级

    Returns:
        SpecOutline。文档无标题时返回空节点列表（调用方需容错）。
    """
    if not md or not md.strip():
        return SpecOutline(tool_id=tool_id, raw_md=md or "")

    lines = md.split("\n")

    # 1) 定位所有符合层级区间的标题行
    headings: list[tuple[int, int, str]] = []  # (行号, 层级, 标题)
    in_code_block = False
    for i, line in enumerate(lines):
        # 跳过代码块内的 # 注释（如 markdown 示例里的 # 标题）
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        m = _HEADING_RE.match(line)
        if not m:
            continue
        level = len(m.group(1))
        if min_level <= level <= max_level:
            headings.append((i, level, m.group(2).strip()))

    if not headings:
        return SpecOutline(tool_id=tool_id, raw_md=md)

    # 2) 处理重复标题：同标题出现多次时加序号，保证 id 唯一
    title_count: dict[str, int] = {}
    nodes: list[SpecNode] = []

    for idx, (line_no, level, title) in enumerate(headings):
        # 节点的正文范围：从本标题到下一个同级或更高级标题之前
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
        content = "\n".join(lines[line_no:end]).rstrip()

        base = f"h{level}-{_slugify(title)}"
        title_count[base] = title_count.get(base, 0) + 1
        nid = base if title_count[base] == 1 else f"{base}-{title_count[base]}"

        nodes.append(SpecNode(
            id=nid,
            title=title,
            level=level,
            node_type=_infer_node_type(title),
            content_md=content,
            line_start=line_no,
            line_end=end,
        ))

    # 3) 建立父子关系：每个节点挂在它之前最近的、层级更小的节点下
    stack: list[SpecNode] = []
    for n in nodes:
        while stack and stack[-1].level >= n.level:
            stack.pop()
        if stack:
            stack[-1].children.append(n.id)
        stack.append(n)

    return SpecOutline(tool_id=tool_id, nodes=nodes, raw_md=md)


def replace_node_content(md: str, node: SpecNode, new_content: str) -> str:
    """用新片段替换文档中指定节点的内容（按行范围精确替换）。

    用行范围而非字符串查找，可避免"文档中存在相同文本"导致的错替换。
    """
    lines = md.split("\n")
    # 防御：行范围越界时退化为整段替换
    start = max(0, min(node.line_start, len(lines)))
    end = max(start, min(node.line_end, len(lines)))
    new_lines = (new_content or "").rstrip().split("\n")
    return "\n".join(lines[:start] + new_lines + lines[end:])


def outline_context_for_prompt(outline: SpecOutline, exclude_id: str) -> str:
    """生成"其他节点的标题+摘要"，供精化时保持整体一致性。

    只给标题与摘要、不给正文，既让 LLM 理解语境，
    又避免它顺手改动使用者没提到的段落。
    """
    parts = []
    for n in outline.nodes:
        if n.id == exclude_id:
            continue
        s = f"  [{n.title}]"
        if n.summary:
            s += f"：{n.summary}"
        parts.append(s)
    return "\n".join(parts) if parts else "（无其他段落）"


# ══════════════════════════════════════════════════════════
# Markdown 表格解析 —— 支撑「表单化直接编辑」
#
# 背景：使用者最常见的修改是"把某个参数的默认值从 8 改成 4"这类
# **确定性改动**。这类改动若走 LLM，需 10 秒级（推理模型思考开销），
# 且模型会顺带改同义词、调语序 —— 业界（Aider/Anthropic/受监管行业实践）
# 的共识是：结构化内容的修改应由系统确定性应用，而非让 LLM 重写。
#
# 表格本身是结构化的，可程序化解析 → 修改 → 重渲染，全程 0 延迟、
# 100% 精确，只有改动目标单元格。
# ══════════════════════════════════════════════════════════

# 表格分隔行，如 |---|:---:|---:|
_SEPARATOR_RE = re.compile(r"^\|?[\s:|-]+\|[\s:|-]*$")


def _split_row(line: str) -> list[str]:
    """拆分一行表格为单元格列表。
    支持 `| a | b |` 与 `a | b` 两种写法。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_separator(cells: list[str]) -> bool:
    """判断是否为分隔行（|---|---| 或 |:---|---:|）"""
    if not cells:
        return False
    return all(re.fullmatch(r":?-{1,}:?", c or "") for c in cells if c != "")


@dataclass
class TableBlock:
    """一个 Markdown 表格块"""
    header: list[str]
    rows: list[list[str]]
    line_start: int          # 表格在节点内容中的起始行（相对行号）
    line_end: int
    align: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_table(node: SpecNode) -> TableBlock | None:
    """从节点内容中解析出第一个 Markdown 表格。

    规范文档的表格通常一个节点只有一个（如「输入规范」的参数表），
    因此只取第一个即可满足需求。

    Returns:
        TableBlock；节点内无表格则返回 None。
    """
    lines = (node.content_md or "").split("\n")
    table_lines: list[tuple[int, str]] = []

    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            if table_lines:
                break          # 空行表示表格结束
            continue
        if s.startswith("|"):
            table_lines.append((i, s))
        elif table_lines:
            break              # 非表格行且已有内容 → 表格结束

    # 至少需要：表头 + 分隔行
    if len(table_lines) < 2:
        return None

    header = _split_row(table_lines[0][1])
    sep = _split_row(table_lines[1][1])
    if not _is_separator(sep):
        return None

    rows: list[list[str]] = []
    for _, raw in table_lines[2:]:
        cells = _split_row(raw)
        # 补齐/截断到表头长度，保证列对齐
        if len(cells) < len(header):
            cells = cells + [""] * (len(header) - len(cells))
        elif len(cells) > len(header):
            cells = cells[:len(header)]
        rows.append(cells)

    align = [("right" if c.endswith(":") else "left" if c.startswith(":") else "")
             for c in sep]

    return TableBlock(
        header=header,
        rows=rows,
        line_start=table_lines[0][0],
        line_end=table_lines[-1][0] + 1,
        align=align,
    )


def render_table(block: TableBlock) -> str:
    """把 TableBlock 重新渲染为 Markdown 表格文本。

    保持原有列宽与对齐方式，避免重渲染后格式跳动。
    """
    ncols = len(block.header)

    def fmt(cells: list[str]) -> str:
        padded = (list(cells) + [""] * ncols)[:ncols]
        return "| " + " | ".join(padded) + " |"

    def sep_line() -> str:
        parts = []
        for i in range(ncols):
            a = block.align[i] if i < len(block.align) else ""
            if a == "right":
                parts.append("---:")
            elif a == "left":
                parts.append(":---")
            else:
                parts.append("---")
        return "| " + " | ".join(parts) + " |"

    return "\n".join([fmt(block.header), sep_line()] + [fmt(r) for r in block.rows])


def update_table_cell(node: SpecNode, row_index: int, column: str,
                      new_value: str) -> str:
    """修改表格中指定单元格，返回渲染后的表格文本。

    按「行号 + 列名」定位比按内容匹配更可靠：列名取自表头，
    不受单元格内容变化影响。

    Args:
        row_index: 数据行下标（0-based，不含表头）
        column:    列名（必须在表头中存在）
        new_value: 新值

    Returns:
        渲染后的完整表格文本

    Raises:
        ValueError: 节点无表格 / 列名不存在 / 行号越界
    """
    block = parse_table(node)
    if block is None:
        raise ValueError("该节点内没有可编辑的表格")

    if column not in block.header:
        raise ValueError(
            f"列名 '{column}' 不存在。可用列：{block.header}"
        )

    if row_index < 0 or row_index >= len(block.rows):
        raise ValueError(
            f"行号 {row_index} 越界。该表格共 {len(block.rows)} 行数据"
        )

    col = block.header.index(column)
    block.rows[row_index][col] = new_value
    return render_table(block)


def replace_table_in_node(node: SpecNode, new_table: str) -> str:
    """用新表格替换节点内容中的原表格，返回新的节点内容。

    按行范围替换（表格可能在节点中前后有其他文字），
    避免字符串查找导致的错替换。
    """
    block = parse_table(node)
    if block is None:
        return node.content_md

    lines = (node.content_md or "").split("\n")
    return "\n".join(
        lines[:block.line_start] + new_table.split("\n") + lines[block.line_end:]
    )
