---
id: paper-abstract-tool
name: 论文摘要工具
version: 0.1.0
type: script
language: python
status: active
created: 2026-07-27
---

# 论文摘要工具

## 1. 功能概述
从指定数据集目录下的所有 PDF 论文中提取文本，调用 DeepSeek 大模型生成不超过指定字数的中文摘要，并将所有摘要合并为一个 Markdown 文件保存至数据集目录。最终返回处理的文件数量和数据集名称。

## 2. 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| dataset | string | 是 | - | 数据集名称，用于定位论文目录 |
| n | integer | 是 | - | 每篇论文摘要的最大字数 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明 |
| output_format | string | text / file |
| data | dict | 输出数据 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `text` | `{"text":"处理完成：共 5 篇论文，摘要已保存至 /path/dataset/"}` | 纯文本 |
| `file` | `{"file_path":"/path/to/dataset/abstracts.md"}` | 下载链接 |

## 4. 依赖环境
| 依赖 | 版本 | 用途 |
|------|------|------|
| python | >=3.9 | 运行环境 |
| openai | >=1.0.0 | 调用 DeepSeek 模型（兼容 OpenAI SDK） |
| PyPDF2 | >=3.0.0 | 提取 PDF 文本内容 |
| pdfplumber | >=0.9.0 | 备选 PDF 文本提取 |

## 5. 运行机制

### 5.1 执行流程
1. 调用系统 API【获取数据集信息】获取数据集 `{dataset}` 的目录 `{data_path}`。
2. 调用系统 API【获取DeepSeek API KEY】获取 DeepSeek 的 API KEY。
3. 遍历 `{data_path}` 下的所有 PDF 文件：
   a. 使用 PyPDF2 或 pdfplumber 提取全文。
   b. 构造 Prompt，要求模型生成一篇不超过 `{n}` 字的中文摘要，并识别论文标题。
   c. 调用 DeepSeek v4 pro 模型获取摘要和标题。
   d. 将“标题”作为二级标题，摘要内容作为正文，拼接到 Markdown 文本中。
4. 将所有文件的摘要合并写入 `{data_path}/abstracts.md`。
5. 返回成功状态，并附带处理文件数量与数据集名。

### 5.2 错误处理
- 数据集不存在 → 返回错误信息，status 为 failed。
- 目录下无 PDF 文件 → 返回提示信息，处理文件数为 0。status 为 failed。
- API KEY 获取失败 → 返回验证错误。status 为 failed。
- PDF 读取异常 → 跳过该文件并记录警告，继续处理其他文件。status 为 failed。
- 大模型调用异常 → 捕获异常并返回具体错误信息。status 为 failed。

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-07-27 | 初始版本 |