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
| os | 标准库 | 文件路径操作 |
| time | 标准库 | 生成时间戳 |

## 5. 运行机制

### 5.1 执行流程
1. 调用【获取数据集信息】获取数据集 `{dataset}` 的目录 `{data_path}`
2. 新建项目目录下的子目录: `./data/download/{timestamp}/`，`{timestamp}` 为当前时间戳
3. 遍历 `{data_path}` 中的每张图片：
   - 经系统 API【LLM图片生成API】（`api-llm-images`）调用豆包图像模型，按照 `{req}` 进行图片编辑。密钥与端点由平台按 provider（默认 doubao）在服务端解析，不经过工具代码
   - 将生成的图片下载保存到 `./data/download/{timestamp}/`
4. 调用【数据集注册API】将目录 `./data/download/{timestamp}/` 注册为合成图片数据集 `{output_dataset}`
5. 返回第一张编辑后的图片路径和成功消息

### 5.2 错误处理
- 图片编辑调用失败（如 `.env` 未配置 `DOUBAO_API_KEY`）→ 该图片计入失败并继续，全部失败时返回错误详情
- 数据集不存在或路径无效 → 返回验证错误
- 图片处理异常 → 捕获并返回详细错误（如网络错误、模型返回异常等）
- 注册数据集失败 → 返回注册失败原因

### 5.3 核心代码示例
```python
# 经平台代理通道调用豆包图片编辑（密钥不进工具代码）：
result = _call_api(
    "api-llm-images",
    model="doubao-seedream-5-0-lite-260128",
    prompt="保持模特姿势和液态服装的流动形状不变。将服装材质从银色金属改为完全透明的清水（或玻璃）。",
    image=f"data:image/png;base64,{img_b64}",   # 本地图片的 base64 data URI
    size="2K",
    watermark=False,
)
image_url = result["images"][0]   # 失败时 result 含 error 字段
```

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-04-07 | 初始版本 |
```