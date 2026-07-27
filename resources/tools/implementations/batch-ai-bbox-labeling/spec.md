---
id: batch-ai-bbox-labeling
name: 大模型批量标框
version: 0.1.0
type: script
language: python
status: active
created: 2026-07-27
---

# 大模型批量标框

## 1. 功能概述
本工具利用豆包大模型对指定数据集中的图片进行批量目标检测，自动生成 YOLO 格式的标注框，并在原图上绘制加粗红框保存可视化结果，最后将标注后的数据集注册为新的数据集供后续使用。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| dataset | string | 是 | - | 原始图片数据集名称，系统通过【获取数据集信息】获取其实际路径 |
| req | string | 是 | - | 检测目标描述，例如 `"person, car"`，将作为提示词传给豆包大模型 |
| output_dataset | string | 是 | - | 标注完成后生成的新数据集名称，系统将通过【数据集注册API】进行注册 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | `success` 或 `failed` |
| message | string | 结果说明，例如 "标注完成，共处理 5 张图片" |
| output_format | string | 固定为 `image` |
| data | dict | 包含字段 `image_path`，指向第一张标框图片的路径 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `image` | `{"image_path": "/data/download/20260727120000/labeled/img001.jpg"}` | 直接绘制图片 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| requests | >=2.28.0 | 调用豆包大模型 API |
| Pillow | >=9.0.0 | 图片缩放与绘制检测框 |

## 5. 运行机制

### 5.1 执行流程
1. 通过系统 API `【获取豆包API KEY】` 获取豆包大模型的 `{api_key}`。
2. 调用系统 API `【获取数据集信息】` 获取数据集 `{dataset}` 的实际路径 `{data_path_src}`。
3. 在项目目录下创建基于当前时间戳的目录 `./data/download/{timestamp}/`，记作 `{data_path_target}`。
4. 在 `{data_path_target}` 下创建子目录 `labeled/`。
5. 遍历 `{data_path_src}` 下的每张图片文件：
   - 保持长宽比缩放图片，使其最长边不超过 640 像素。
   - 调用豆包大模型 `doubao-seed-2-0-lite-260428`（API Key 为 `{api_key}`），请求检测目标 `{req}`，获得 YOLO 风格的边界框列表（归一化于缩放后的 640 分辨率图像）。
   - 根据原始图片尺寸，将归一化边界框逆缩放为原图的像素坐标。
   - 在原始图片上绘制加粗红框，保存至 `{data_path_target}/labeled/` 目录下（文件名与原图相同）。
   - 按照 YOLO 训练集格式，生成标签文件（每行为 `class_id x_center y_center width height`，坐标归一化于原图宽高），与原始图片一并保存至 `{data_path_target}` 目录中。
6. 全部图片处理完毕后，调用系统 API `【数据集注册API】` 将目录 `{data_path_target}` 注册为数据集 `{output_dataset}`。
7. 将 `{data_path_target}/labeled/` 下第一张图片的路径封装为输出结果返回。

### 5.2 错误处理
- API Key 获取失败 → `status: failed`，返回错误信息 `"无法获取豆包API KEY"`。
- 数据集信息获取失败或路径不存在 → `status: failed`，返回错误信息 `"数据集不存在或无法访问"`。
- 图片处理或 API 调用异常 → 捕获详细异常，`status: failed`，返回 `"图片处理失败: {错误详情}"`。
- 注册数据集失败 → `status: failed`，返回 `"数据集注册失败"`。

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-07-27 | 初始版本 |