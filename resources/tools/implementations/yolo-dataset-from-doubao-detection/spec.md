---
id: yolo-dataset-from-doubao-detection
name: 基于豆包大模型的目标检测标注与YOLO数据集构建
version: 0.1.0
type: script
language: python
status: active
created: 2026-07-26
---

# 基于豆包大模型的目标检测标注与YOLO数据集构建

## 1. 功能概述

该工具针对指定数据集中的图片，利用豆包大模型进行目标检测，自动生成符合YOLO训练格式的标签文件，并按照YOLO目录结构整理数据集。工具通过调用系统API获取数据集路径与API密钥，最终返回整理后的文件目录结构。

输入：数据集名 `{dataset}` 和检测目标 `{req}`。  
过程：
1. 通过系统API【获取数据集信息】获取数据集 `{dataset}` 的信息，从中解析 `data_path` 字段作为文件目录。
2. 通过系统API【获取豆包API KEY】获取豆包大模型的 `API_KEY`。
3. 遍历 `{data_path}` 目录下的每一张图片，调用豆包大模型 `{doubao-seed-2-1-pro-260628}` 进行目标检测，检测目标为 `{req}`。若检测到目标，则提取目标的 `bounding-box`，并构建YOLO格式的标签文件（每行：`class_id x_center y_center width height`，归一化坐标）。
4. 按YOLO训练数据集的目录结构整理 `{data_path}`，即生成 `images/` 和 `labels/` 子目录，图片放入 `images/`，标签文件放入 `labels/`，并创建 `data.yaml` 数据集配置文件。

输出：整理后的目录 `{data_path}` 的文件目录结构。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| dataset | string | 是 | - | 数据集名称，用于通过系统API获取数据集信息 |
| req | string | 是 | - | 目标检测描述，传递给大模型的检测需求，如“猫”、“狗、车”等 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明，包括处理图片数量、检测到目标的图片数量等 |
| output_format | string | text |
| data | dict | 输出数据，包含目录结构文本 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `text` | `{"text":"<目录树文本>"}` | 纯文本展示目录结构 |

示例 data 内容：
```
data_path/
├── images/
│   ├── img001.jpg
│   ├── img002.jpg
│   └── ...
├── labels/
│   ├── img001.txt
│   ├── img002.txt
│   └── ...
└── data.yaml
```

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| requests | >=2.28.0 | 调用豆包大模型API |
| pillow | >=9.0.0 | 读取图片尺寸，用于坐标归一化 |
| pyyaml | >=6.0 | 生成 data.yaml 配置文件 |
| python | >=3.8 | 运行环境 |
| 系统API | - |【获取数据集信息】、【获取豆包API KEY】 |

## 5. 运行机制

### 5.1 执行流程
1. **获取数据集路径**  
   调用系统API【获取数据集信息】，传入 `{dataset}`，从返回结果解析 `data_path`。
2. **获取API密钥**  
   调用系统API【获取豆包API KEY】，获取豆包大模型的 `API_KEY`。
3. **遍历图片并进行检测**  
   扫描 `data_path` 下的所有图片文件（支持 jpg、png、bmp 等常见格式），对每张图片：
   - 使用 `PIL` 读取图片尺寸（宽、高）。
   - 调用豆包大模型 `{doubao-seed-2-1-pro-260628}` 的视觉理解接口，传入图片和检测需求 `{req}`，要求返回识别到的目标边界框（如 `[x_min, y_min, x_max, y_max]` 像素坐标）。
   - 若返回有效的边界框，将坐标归一化（除以宽和高），计算YOLO格式的 `class_id`（固定为0，如果多类别可根据 `req` 映射）、`x_center`、`y_center`、`width`、`height`，写入对应的 `.txt` 标签文件。
4. **整理目录结构**  
   - 创建 `data_path/images/` 目录，将所有图片移动或复制到此目录下。
   - 创建 `data_path/labels/` 目录，将所有生成的标签文件放置于此。
   - 在 `data_path/` 根目录生成 `data.yaml`，内容包含训练数据路径、类别名称列表、类别数量等。
5. **生成目录树文本**  
   使用 `os.walk` 生成 `data_path` 的目录树结构，组合为文本。
6. **返回结果**  
   返回成功状态、处理摘要及目录树文本。

### 5.2 错误处理
- **数据集不存在** → `status: failed`，`message: "数据集 {dataset} 不存在，无法获取 data_path"`。
- **API密钥获取失败** → `status: failed`，`message: "无法获取豆包API KEY，请检查系统API配置"`。
- **图片目录为空** → `status: success`，`message: "未找到任何图片文件"`，目录树仅包含空文件夹。
- **检测不到目标** → `status: failed`，`message: "大模型调用失败，未检测到目标"`。
- **豆包大模型调用异常** → 记录错误并跳过当前图片，继续处理后续图片，最后在 `message` 中汇总失败信息。
- **参数无效** → 返回验证错误，指明缺少必要参数。

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-07-26 | 初始版本 |