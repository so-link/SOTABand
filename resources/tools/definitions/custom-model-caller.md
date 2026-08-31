---
id: custom-model-caller
name: 自定义模型调用器
version: 0.1.0
type: function
language: python
status: active
created: 2026-08-30
---

# 自定义模型调用器

## 1. 功能概述

用使用者**临时指定的服务商与模型**调用大模型，不影响项目全局配置。

典型场景：项目 `.env` 用 `deepseek-v4-flash`（纯文本），但某次任务需要
多模态能力（看图）、或需要长上下文、或想试用其他厂商的模型 ——
此时无需修改全局 `.env`，直接在本工具里填入服务商、模型名与 Key 即可。

与全局配置完全隔离：本工具的调用不影响其他工具，其他工具仍用 `.env` 的配置。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| prompt | string | 是 | 无 | 要发送给模型的内容 |
| provider | string | 否 | 空 | 服务商 id（如 doubao/openai/qwen/mimo）。推荐填写，系统据此自动解析端点 |
| model | string | 是 | 无 | 模型名称，如 `doubao-vision-pro-32k` |
| api_key | string | 是 | 无 | 该服务商的 API Key |
| base_url | string | 否 | 空 | 仅当服务商不在目录中时才需要填 |
| image_path | string | 否 | 空 | 可选。要分析的图片路径，填写后以多模态方式发送 |
| max_tokens | integer | 否 | 4096 | 最大生成长度。推理模型建议 >= 1500 |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明或错误信息 |
| output_format | string | 固定为 `text` |
| data | dict | 见下表 |

data 结构：

| 字段 | 说明 |
|------|------|
| text | 模型返回的文本内容 |
| model | 实际使用的模型 |
| base_url | 实际使用的端点 |
| api_key_masked | 脱敏后的 Key（前 4 后 4），**永不返回原始 Key** |
| has_image | 本次是否以多模态方式发送 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| Pillow | >= 9.0 | 图片读取与 base64 编码（仅填写 image_path 时需要） |

## 5. 运行机制

### 5.1 执行流程

1. 校验必填参数（prompt / model / api_key）。
2. 若填写了 `image_path`：读取图片，等比缩放至最长边 1024 像素，
   转为 JPEG 后 base64 编码，与 prompt 一起以多模态消息格式发送。
3. 调用【【LLM自定义配置对话API】】，传入 provider / model / api_key /
   base_url / max_tokens。
4. 端点解析顺序：显式 base_url → provider 查服务商目录 → 按 model 名推断。
5. 取返回值的 content 字段作为模型回答，组装输出。

### 5.2 错误处理

- 缺少必填参数 → status:failed，message 指明缺少哪个参数
- 服务商无法解析 → status:failed，message 列出所有已知服务商
- Key 无效 / 网络不通 → status:failed，message 给出脱敏后的错误原因
- 图片路径不存在 → status:failed，message 说明文件未找到
- 所有异常信息统一脱敏，避免密钥随报错外泄

## 6. 安全说明

- api_key 只在本次调用的内存中使用，不写入任何配置文件或日志
- 返回值中的 Key 一律脱敏
- 工具不会打印或记录 api_key

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-08-30 | 初始版本 |
