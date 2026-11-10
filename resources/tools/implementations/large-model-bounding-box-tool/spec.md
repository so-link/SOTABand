---
id: large-model-bounding-box-tool
name: 大模型标框工具
version: 0.1.0
type: api-wrapper
language: python
status: active
created: 2026-07-27
---

# 大模型标框工具

## 1. 功能概述

本工具利用豆包大模型对输入图片进行目标检测，将检测到的目标以 YOLO 风格的边界框返回，并在原图上绘制加粗的红色边界框。  
工具经系统 API 【LLM自定义配置对话API】（`api-llm-chat-with-config`，provider 为 doubao）调用豆包视觉模型 `doubao-seed-2-0-lite-260428` 进行目标检测，最后输出带有红框标注的图片。密钥由平台按 provider 在服务端解析，不经过工具代码。

## 2. 输入规范

| 参数名     | 类型   | 必填 | 默认值 | 说明                     |
|------------|--------|------|--------|--------------------------|
| file_path  | string | 是   | 无     | 待检测图片的本地文件路径 |
| req        | string | 是   | 无     | 需要检测的目标描述       |

## 3. 输出规范

### 3.1 标准输出字段
| 字段          | 类型         | 说明                                      |
|---------------|--------------|-------------------------------------------|
| status        | string       | 执行结果：`success` 或 `failed`           |
| message       | string       | 结果说明或错误信息                        |
| output_format | string       | 固定为 `image`，表示输出为图片文件        |
| data          | dict         | 包含生成的图片路径及相关信息              |

**data 字段详情（成功时）**：
```json
{
  "image_path": "/tmp/boxed_result.jpg",
  "boxes": [[x_center, y_center, width, height], ...],
  "original_width": 1920,
  "original_height": 1080
}
```
- `image_path`：保存的带红框的图片绝对路径。
- `boxes`：检测到的目标边界框列表，YOLO 格式（归一化坐标）。
- `original_width` / `original_height`：原始图片的宽高。

### 3.2 可视化输出格式
| output_format | data 格式                               | 界面渲染方式     |
|---------------|-----------------------------------------|------------------|
| `image`       | `{"image_path":"/path/to/boxed.jpg"}`   | 直接绘制图片     |

## 4. 依赖环境

| 依赖     | 版本    | 用途                               |
|----------|---------|------------------------------------|
| Pillow   | ≥9.0.0  | 图片读取、压缩、绘制边界框及保存   |
| json     | 内置   | 解析 API 返回的 JSON               |
| base64   | 内置   | 图片编码为 base64 发送给大模型     |

## 5. 运行机制

### 5.1 执行流程
1. 打开并校验 `file_path` 指定的图片文件存在且格式正确。
2. 将图片保持长宽比例不变，压缩至短边为 640 像素（即最大边不超过 640）。
3. 将压缩后的图片转换为 Base64 编码，经系统 API 【LLM自定义配置对话API】（`api-llm-chat-with-config`，provider 为 doubao）调用豆包视觉模型 `doubao-seed-2-0-lite-260428` 进行目标检测，检测目标为 `req`。
4. 解析大模型返回的 JSON，提取 YOLO 风格的归一化边界框列表（`[x_center, y_center, width, height]`）。
5. 使用原始尺寸的图片，遍历边界框并绘制加粗的红色矩形框（线宽根据图片大小自适应，例如 2–5 像素）。
6. 将绘制后的图片保存到临时目录（如 `/tmp/boxed_<uuid>.jpg`），返回图片路径及相关信息。

### 5.2 错误处理
- 图片文件不存在或无法读取 → 返回 `status: "failed"`，`message` 包含具体文件错误。
- 参数 `req` 为空 → 返回验证错误。
- 大模型调用失败（网络错误、超时、`.env` 未配置 `DOUBAO_API_KEY` 等） → 返回 `status: "failed"` 及详细错误。
- 大模型返回结果中无有效边界框 → 返回成功但 `boxes` 为空，`message` 提示未检测到目标，仍可返回原图（可选项）。
- 图片绘制或保存失败 → 返回文件系统错误。

## 6. 版本历史
| 版本   | 日期       | 变更         |
|--------|------------|--------------|
| 0.1.0  | 2026-07-27 | 初始版本     |