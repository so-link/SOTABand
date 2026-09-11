---
id: paper-summary
name: 论文摘要
version: 0.1.0
type: script
language: python
status: active
created: 2026-09-10
---

# 论文摘要

## 1. 功能概述

对指定目录 `{dir_path}` 下的所有 PDF 格式论文进行摘要分析。工具首先通过调用【获取DeepSeek API KEY】获取 DeepSeek 大模型的 API Key，然后调用 DeepSeek v4 pro 模型对每篇论文进行摘要分析，汇总生成一份总的 Markdown 报告（保存在 `{dir_path}` 目录下），并返回论文信息列表。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| dir_path | string | 是 | 无 | 包含 PDF 论文的目录路径 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明（含报告保存路径） |
| output_format | string | table |
| data | dict | 论文信息列表，格式见 3.2 中 `table` 行 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `text` | `{"text":"..."}` | 纯文本 |
| `image` | `{"image_path":"/path/to/file.png"}` | 直接绘制图片 |
| `table` | `{"columns":["序号","文件名","标题","摘要","关键词"], "rows":[["1","paper1.pdf","论文标题","摘要内容","关键词1,关键词2"]]}` | 渲染表格 |
| `file` | `{"file_path":"/path/to/result.csv"}` | 下载链接 |

> 实际 `data` 为 `table` 格式，其中 `columns` 建议包含：序号、文件名、标题、摘要、关键词；`rows` 为每篇论文对应的信息。

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | >=3.9 | 运行环境 |
| pdfplumber | 0.11.4 | 解析 PDF 文本 |
| openai | 1.40.0 | 调用 DeepSeek API（兼容 OpenAI SDK） |
| requests | 2.32.3 | HTTP 请求 |
| 标准库 os/json/time | 内置 | 文件操作、数据处理、重试控制 |

## 5. 运行机制

### 5.1 执行流程
1. 通过调用【获取DeepSeek API KEY】获取 DeepSeek 大模型的 API Key。
2. 读取并校验输入参数 `dir_path`，检查目录是否存在。
3. 扫描 `dir_path` 目录下所有 `.pdf` 格式的论文文件。
4. 使用 `pdfplumber` 提取每篇 PDF 的文本内容。
5. 调用 DeepSeek v4 pro 大模型对每篇论文进行摘要分析，生成论文标题、摘要、关键词等信息，分析其核心创新点，主要实验结果，发展潜力等。
6. 汇总所有论文信息，生成总的 Markdown 报告，并保存为 `paper_summary_report.md` 文件到 `dir_path` 目录下。
7. 返回论文信息列表（`table` 格式）。

### 5.2 错误处理
- 目录不存在 → 返回 `status=failed`，`message` 说明目录不存在。
- 目录下无 PDF 文件 → 返回 `status=failed`，`message` 说明未找到 PDF 文件。
- PDF 文本提取失败 → 跳过该文件并在报告中标注失败，继续处理其他文件。
- DeepSeek API 调用失败 → 自动重试 2 次，仍失败则返回错误信息。
- 获取 API KEY 失败 → 返回 `status=failed`，`message` 说明 API Key 获取失败。

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-09-10 | 初始版本 |