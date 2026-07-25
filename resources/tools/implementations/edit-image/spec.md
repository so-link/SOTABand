```markdown
---
id: edit-image
name: 编辑图片
version: 0.1.0
type: script
language: python
status: active
created: 2025-04-07
---

# 编辑图片

## 1. 功能概述

根据用户指定的编辑要求，使用豆包大模型对数据集中的图片进行批量编辑，并将编辑后的图片自动注册为新的数据集。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| dataset | string | 是 | - | 原始数据集名称 |
| req | string | 是 | - | 图片编辑要求（prompt） |
| output_dataset | string | 是 | - | 合成图片数据集名称 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明 |
| output_format | string | text / image / table / file |
| data | dict | 输出数据 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `image` | `{"image_path":"/path/to/first_edited_image.png"}` | 直接绘制图片 |
| `text` | `{"text":"编辑完成，数据集已注册为 {output_dataset}"}` | 纯文本 |

标准输出会包含第一张编辑后的图片路径，以及数据集注册成功的信息。

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| volcengine-python-sdk[ark] | latest | 调用豆包大模型图片编辑 API |
| os | 标准库 | 文件路径操作 |
| time | 标准库 | 生成时间戳 |

## 5. 运行机制

### 5.1 执行流程
1. 调用【获取豆包API KEY】获取 `api_key`
2. 调用【获取数据集信息】获取数据集 `{dataset}` 的目录 `{data_path}`
3. 新建项目目录下的子目录: `./data/download/{timestamp}/`，`{timestamp}` 为当前时间戳
4. 遍历 `{data_path}` 中的每张图片：
   - 使用 `{api_key}` 调用豆包大模型，按照 `{req}` 进行图片编辑
   - 将生成的图片下载保存到 `./data/download/{timestamp}/`
5. 调用【数据集注册API】将目录 `./data/download/{timestamp}/` 注册为合成图片数据集 `{output_dataset}`
6. 返回第一张编辑后的图片路径和成功消息

### 5.2 错误处理
- API 密钥获取失败 → 返回错误信息 “无法获取豆包API KEY”
- 数据集不存在或路径无效 → 返回验证错误
- 图片处理异常 → 捕获并返回详细错误（如网络错误、模型返回异常等）
- 注册数据集失败 → 返回注册失败原因

### 5.3 核心代码示例
```python
注：调用doubao进行图片编辑的代码示例： 
 
import os
# Install SDK:  pip install 'volcengine-python-sdk[ark]'
from volcenginesdkarkruntime import Ark

client = Ark(
    # The base URL for model invocation
    base_url="https://ark.cn-beijing.volces.com/api/v3",
    # Get API Key: https://console.volcengine.com/ark/region:ark+cn-beijing/apikey
    api_key=os.getenv('ARK_API_KEY'),
)

imagesResponse = client.images.generate(
    # Replace with Model ID
    model="doubao-seedream-5-0-lite-260128",
    prompt="保持模特姿势和液态服装的流动形状不变。将服装材质从银色金属改为完全透明的清水（或玻璃）。透过液态水流，可以看到模特的皮肤细节。光影从反射变为折射。",
    image="https://ark-project.tos-cn-beijing.volces.com/doc_image/seedream4_5_imageToimage.png",
    size="2K",
    output_format="png",
    response_format="url",
    watermark=False
)
```

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-04-07 | 初始版本 |
```