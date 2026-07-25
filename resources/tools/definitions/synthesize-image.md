---
id: synthesize-image
name: 合成图片
version: 0.1.0
type: api-wrapper
language: python
status: active
created: 2025-04-12
---

# 合成图片

## 1. 功能概述

根据用户提供的文本描述（`req`）、生成数量（`n`）和数据集名称（`dataset`），自动调用豆包大模型生成相应数量的图片，下载至本地时间戳子目录，并通过【数据集注册API】将整个目录注册为一个合成图片数据集。最终返回第一张生成图片的本地路径。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| req | string | 是 | - | 生成图片的提示词（prompt） |
| n | int | 是 | - | 需要生成的图片数量 |
| dataset | string | 是 | - | 合成图片数据集的名称 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明 |
| output_format | string | image |
| data | dict | 输出数据，包含图片路径 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `image` | `{"image_path":"/path/to/file.png"}` | 直接绘制图片 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| volcenginesdkarkruntime | >=1.0.0 | 调用豆包大模型生成图片 |
| requests | >=2.28 | 下载生成的图片 |
| os | 标准库 | 路径与目录操作 |
| time | 标准库 | 生成时间戳目录名 |

## 5. 运行机制

### 5.1 执行流程
1. 通过【获取豆包API KEY】获得 `{api_key}`。
2. 计算当前时间戳 `{xxxx}`，在项目目录下创建子目录 `./data/download/{xxxx}/`。
3. 利用 `{api_key}` 初始化豆包客户端，循环调用大模型生成 `{n}` 张图片。
4. 将每张生成图片的 URL 下载至 `./data/download/{xxxx}/` 中。
5. 调用【数据集注册API】，将目录 `./data/download/{xxxx}/` 注册为名为 `{dataset}` 的合成图片数据集。
6. 返回状态与第一张生成图片的本地路径。

### 5.2 核心代码参考（豆包图片生成API调用示例）

```python
#调用豆包大模型请参考下面代码：
 import os
from volcenginesdkarkruntime import Ark

client = Ark(
    # The base URL for model invocation
    base_url="https://ark.cn-beijing.volces.com/api/v3",
    api_key={api_key},
)

imagesResponse = client.images.generate(
    # Replace with Model ID
    model="doubao-seedream-5-0-lite-260128",
    prompt="充满活力的特写编辑肖像，模特眼神犀利，头戴雕塑感帽子，色彩拼接丰富，眼部焦点锐利，景深较浅，具有Vogue杂志封面的美学风格，采用中画幅拍摄，工作室灯光效果强烈。",
    size="2K",
    output_format="png",
    response_format="url",
    watermark=False
)
```

### 5.3 错误处理
- 获取 API KEY 失败 → 返回错误信息并终止。
- 目录创建失败 → 返回错误，提示检查文件系统权限。
- 豆包 API 调用失败 → 返回详细错误信息（网络异常、参数错误、额度不足等）。
- 图片下载失败 → 记录具体失败张数，其他成功的仍继续注册数据集。
- 数据集注册失败 → 返回错误信息，但已下载的图片仍保留在目录中。

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-04-12 | 初始版本 |