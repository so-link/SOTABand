---
id: image-synthesis-doubao
name: 图片合成工具（豆包大模型）
version: 0.1.0
type: function
language: python
status: active
created: 2025-04-16
---

# 图片合成工具（豆包大模型）

## 1. 功能概述

本工具通过调用豆包大模型（Doubao-Seedream）根据用户提供的描述生成图片，并将生成的图片保存到项目目录下的 `/data/downloads/{timestamp}/` 中，同时通过【数据集注册API】将生成的图片集合注册为一个数据集。最终返回第一张生成图片的路径，供界面直接展示。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| prompt | string | 是 | — | 图片生成的自然语言描述要求 |
| num_images | int | 是 | — | 需要合成的图片数量（正整数） |
| dataset_name | string | 是 | — | 数据集名称，用于注册图片集合 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success/failed) |
| message | string | 结果说明 |
| output_format | string | **必须指定** — image |
| data | dict | 输出数据，格式由 output_format 决定 |

### 3.2 output_format 说明
- 本工具固定为 `image`，data 结构为 `{"image_path": "/data/downloads/{timestamp}/{first_image_filename.png}"}`，界面将直接绘制该图片。

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| requests | >=2.28 | 调用豆包大模型API及内部系统API |
| os / json / time / uuid | 标准库 | 文件目录管理、时间戳生成、JSON处理 |

## 5. 运行机制

### 5.1 执行流程

1. 通过【LLM配置获取API】获取豆包大模型的API_KEY、Endpoint等配置信息。
2. 构建豆包图片生成API请求（参考 curl 示例），使用用户提供的 `prompt` 和 `num_images` 参数。
3. 调用 `https://ark.cn-beijing.volces.com/api/v3/images/generations`，请求体中的 `n` 设为 `num_images`，`response_format` 设为 `url`，模型使用 `doubao-seedream-5-0-260128`。
4. 解析API返回结果，获取所有生成图片的URL。
5. 生成以当前时间戳命名的子目录 `/data/downloads/{timestamp}/`。
6. 依次下载每一张图片并保存到上述目录中，文件名可使用序号或随机ID。
7. 通过【数据集注册API】将该目录下所有图片注册为一个数据集，使用用户输入的 `dataset_name`。
8. 返回第一张图片的本地路径。

### 5.2 性能指标

- 预期执行时间: < 30s（取决于图片数量和网络延迟）
- 内存占用: < 200MB

### 5.3 错误处理

- 参数无效（如 `num_images` ≤0）→ 返回 status: failed，message: “参数无效”
- 【LLM配置获取API】调用失败 → 返回 status: failed，message: “获取LLM配置失败”
- 豆包API调用失败（网络、鉴权等） → 返回 status: failed，message: “图片生成失败: {具体错误}”
- 下载图片失败或目录创建失败 → 返回 status: failed，message: “文件操作失败: {具体错误}”
- 【数据集注册API】调用失败 → 返回 status: failed，message: “数据集注册失败”，但已生成的文件保留

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "prompt": "治愈系春日樱花街道，日系插画，柔和光影，8K高清",
  "num_images": 2,
  "dataset_name": "spring_sakura_set"
}
```

### 6.2 边界条件

- `num_images` = 1：正常生成单张图片并注册，返回该图片路径。
- `prompt` 过长或包含特殊字符：由豆包API自行处理，不限制输入长度。
- 时间戳冲突（极低概率）：使用微秒级时间戳保证唯一性。

## 7. 调用示例

```python
result = execute(
    prompt="治愈系春日樱花街道，日系插画，柔和光影，8K高清",
    num_images=2,
    dataset_name="spring_sakura_set"
)
# 期望输出
# {
#   "status": "success",
#   "message": "已生成2张图片并注册数据集",
#   "output_format": "image",
#   "data": {
#       "image_path": "/data/downloads/20250416101234/img_1.png"
#   }
# }
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-04-16 | 初始版本 |