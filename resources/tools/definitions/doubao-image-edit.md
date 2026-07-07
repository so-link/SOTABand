---
id: doubao-image-edit
name: 图片编辑（Doubao）
version: 0.1.0
type: api-wrapper
language: python
status: active
created: 2025-04-02
---

# 图片编辑（Doubao）

## 1. 功能概述

本工具通过调用火山引擎 Doubao 图片编辑大模型，根据用户提供的原始图片和编辑描述，生成编辑后的图片。工具的 API Key 与模型端点通过 **【LLM配置获取API】** 动态获取，生成的图片将自动下载到项目目录下的 `/data/download/<timestamp>/` 子目录中，其中 `<timestamp>` 为当前时间戳。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| image_path | string | 是 | - | 待编辑的图片文件路径（支持常见格式如 jpg/png） |
| prompt | string | 是 | - | 编辑要求描述，如“高清优化，细节拉满，写实画质” |
| negative_prompt | string | 否 | "模糊，水印，文字，畸形" | 不希望出现的负面内容 |
| strength | float | 否 | 0.7 | 编辑强度，范围 0~1，值越大修改幅度越大 |
| size | string | 否 | "2048x2048" | 输出图片尺寸，格式如“宽x高” |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success/failed) |
| message | string | 结果说明 |
| output_format | string | **必须指定** — image |
| data | dict | 输出数据，格式由 output_format 决定 |

### 3.2 output_format 说明
- `image`: data 含 `{"image_path": "/data/download/20250402_153045/output.jpg"}` — 界面直接绘制图片

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| volcengine-python-sdk[ark] | >=1.0.0 | 调用火山引擎 Ark 图片编辑接口 |
| Python | >=3.8 | 运行环境 |

## 5. 运行机制

### 5.1 执行流程

1. 通过 **【LLM配置获取API】** 获取火山引擎的 API Key（AK）和 Doubao 图片编辑模型的接入点 ID（ENDPOINT_ID）。
2. 校验输入图片是否存在，读取并转换为 Base64 编码。
3. 使用获取的 AK 初始化 `ArkService` 客户端。
4. 调用 `images_edit` 方法，传入模型 ID、图片 Base64、提示词、负向提示词、强度、尺寸等参数。
5. 接收返回的 Base64 图片数据，解码并保存到项目根目录下的 `/data/download/<当前时间戳>/` 文件夹中，文件名为 `edited_<时间戳>.jpg`。
6. 返回 `success` 状态及图片路径。

### 5.2 性能指标

- 预期执行时间: < 10s（取决于图片大小和模型响应）
- 内存占用: < 500MB

### 5.3 错误处理

- 配置获取失败 → 返回 `failed`，提示“无法获取 LLM 配置”
- 图片文件不存在 → 返回参数验证错误
- API 调用失败 → 捕获异常并返回详细错误信息

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "image_path": "tests/test_input.jpg",
  "prompt": "变为水墨画风格",
  "negative_prompt": "颜色鲜艳，现代元素",
  "strength": 0.8,
  "size": "1024x1024"
}
```

### 6.2 边界条件

- `strength` 为 0 时，应保持原图不变。
- 输入图片尺寸过大（>10MB）时，应有合理提示或自动压缩。
- `prompt` 为空时应拒绝执行。

## 7. 调用示例

```python
result = execute(
    image_path="photo.jpg",
    prompt="将背景替换成星空",
    negative_prompt="人物变形",
    strength=0.6,
    size="2048x1024"
)
# 成功时 result.data["image_path"] 为生成的图片绝对路径
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-04-02 | 初始版本 |