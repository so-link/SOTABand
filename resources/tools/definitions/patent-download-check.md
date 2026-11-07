---
id: patent-download-check
name: 专利本地查重
version: 0.2.0
type: function
language: python
status: active
created: 2026-09-30
---

# 专利本地查重

## 1. 功能概述

针对**待申请专利的技术方案**，在**本地专利数据库**中检索相关现有专利，
进行技术方案对比分析，评估**新颖性**与**侵权风险**。

- **程序粗筛**：`0.6×实词覆盖度 + 0.4×4-gram 重合度` 给每篇打相关分，选出 Top-N。
- **大模型对比分析**：按「新颖性单独对比原则」+「侵权全面覆盖原则」，
  对每篇候选专利分别给出新颖性影响等级、侵权风险等级、理由和规避建议。

用户先输入待申请技术方案的本地文件路径，再输入专利数据库的本地目录路径。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| tech_doc | string | 是 | - | 待申请专利技术方案的本地文件路径（txt/md/pdf/docx） |
| patent_db | string | 是 | - | 专利数据库的本地目录路径（内含 pdf/txt/md/docx 等现有专利文件） |
| top | int | 否 | 5 | 进入大模型对比分析的候选专利数（1~30） |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 对比分析结论（含新颖性 + 侵权风险两维度） |
| output_format | string | file |
| data.file_path | string | 报告文件路径（report.md） |
| data.patent_db | string | 专利数据库目录 |
| data.columns / rows | list | 相关度排序表（排名/公开号/标题/相关度） |
| data.compared | int | 数据库比对的专利篇数 |
| data.ai_review | string | 大模型对比分析全文（新颖性 + 侵权风险） |

## 4. 依赖环境

| 依赖 | 用途 | 必需 |
|------|------|------|
| pypdf / PyPDF2 | 读取 PDF 专利文件 | 是（读 pdf 时） |
| python-docx | 读取 docx 文件 | 是（读 docx 时） |
| core.llm | 大模型对比分析 | 是 |

> 本工具始终调用大模型进行新颖性 + 侵权风险分析。

## 5. 运行机制

1. 读待申请技术方案（`_read_document`）。
2. 读专利数据库目录（`_load_corpus_from_dir`，支持 pdf/txt/md/docx 混合格式）。
3. `_rough_rank` 程序粗筛出相关度 Top-N。
4. `_ai_review` 大模型对 Top-N 做新颖性 + 侵权风险两维度对比分析。
5. `_write_report` 生成 report.md，并在对话中返回完整分析结论。

## 6. 典型用法

- 「把我的技术方案 data/my_tech.md 跟 data/patent-db 里的现有专利做对比分析」
  → `tech_doc=./data/my_tech.md, patent_db=./data/patent-db`

## 7. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-09-30 | 初始版本：待检文件 + 已下载 .md 语料目录 → 粗筛 + 复核 |
| 0.2.0 | 2026-10-05 | 改为技术方案对比分析：参数改 tech_doc/patent_db，支持混合格式专利目录，评估维度改为新颖性+侵权风险两维度 |
