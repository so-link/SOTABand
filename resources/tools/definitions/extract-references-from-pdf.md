---
id: extract-references-from-pdf
name: PDF论文参考文献提取
version: 0.1.0
type: script
language: python
status: active
created: 2026-07-28
---

# PDF论文参考文献提取

## 1. 功能概述

从指定的 PDF 论文文件中提取参考文献列表，并以表格形式返回。该工具通过调用【获取DeepSeek API KEY】API 自动获取 DeepSeek 大模型的 API 密钥，并利用 deepseek v4 pro 模型解析论文中的参考文献内容，最终输出结构化表格数据，方便检索与引用管理。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| path | string | 是 | - | 待解析的 PDF 论文文件路径（本地绝对路径或可访问的网络路径） |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明（成功时提示提取的条数） |
| output_format | string | table |
| data | dict | 包含 columns 和 rows 的表格数据 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `table` | `{"columns":["序号","参考文献"], "rows":[["1","Author A. Title. Journal, Year."], ["2","Author B. ..."]]}` | 渲染表格 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| requests | ≥2.28 | 发送 HTTP 请求调用 DeepSeek API 及获取 API KEY 的系统接口 |
| python-dotenv | ≥1.0 | 管理本地环境变量（可选） |
| PyPDF2 或 pdfplumber | ≥3.0 | PDF 文本提取备用（当 DeepSeek 不支持直接文件解析时） |

## 5. 运行机制

### 5.1 执行流程
1. **接收输入**：读取 `path` 参数。
2. **校验文件**：检查指定路径的 PDF 文件是否存在且可读，若不存在立即返回错误。
3. **获取 API KEY**：调用系统 API 【获取DeepSeek API KEY】，获取有效期内的 DeepSeek 大模型访问密钥。
4. **调用大模型**：
   - 将 PDF 文件以 base64 编码或直接上传（根据 DeepSeek v4 pro 的文件接口要求）并携带解析指令：`请提取这篇论文中的所有参考文献，以列表形式返回，每条一行。`
   - 使用步骤 3 获得的 API KEY 进行鉴权，调用 deepseek v4 pro 模型。
5. **解析结果**：从模型返回的文本中提取每一条参考文献记录，生成序号-参考文献对。
6. **构建输出**：将提取结果封装为表格格式（columns：["序号","参考文献"]，rows 为具体条目）。
7. **返回数据**：按标准输出规范返回 JSON 结构。

### 5.2 错误处理
- **文件不存在** → `status: failed`, `message: "文件 {path} 未找到"`
- **API KEY 获取失败** → `status: failed`, `message: "无法获取 DeepSeek API KEY，请检查系统配置"`
- **大模型调用异常**（如超时、鉴权失败） → `status: failed`, `message: "DeepSeek 模型调用失败: {具体错误原因}"`
- **PDF 内容无参考文献** → `status: success`, `message: "未检测到参考文献条目"`, `data.rows 为空数组`
- **其他未知异常** → 捕获全部异常并返回 `status: failed` 及详细错误信息

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-07-28 | 初始版本 |