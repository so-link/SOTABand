---
id: pose-extraction
name: 姿态提取
version: 0.1.0
type: function
language: python
status: active
created: 2025-03-17
---

# 姿态提取

## 1. 功能概述

基于 OpenCV 实现的人体姿态估计工具。输入一张包含人物的图像，检测人体关键关节（如肩、肘、腕、髋、膝、踝等），在原图上绘制关节连线，输出标注了姿态骨架的图像。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| image_path | string | 是 | - | 输入图像的本地路径（支持常见格式如jpg、png） |
| conf_threshold | float | 否 | 0.5 | 关键点置信度阈值，范围0-1，低于此值的关节不绘制 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success/failed) |
| message | string | 结果说明 |
| output_format | string | `image` — 表示输出为图像文件 |
| data | dict | 输出数据，格式由 output_format 决定 |

### 3.2 output_format 说明
- `image`: data 含 `{"image_path": "/path/to/output_pose.png"}` — 界面直接绘制图片（仅支持路径方式，不支持 base64）

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| opencv-python | >=4.5.0 | 图像处理、DNN 推理 |
| numpy | >=1.19.0 | 数值计算 |
| (内建模型文件) | (例如 OpenPose 的 caffemodel 和 prototxt) | 姿态估计模型，工具首次运行时自动下载或使用本地缓存 |

## 5. 运行机制

### 5.1 执行流程

1. 校验 `image_path` 是否存在，否则返回错误。
2. 加载预训练姿态估计模型（若本地不存在则从指定源下载）。
3. 读取输入图像，进行尺寸归一化和均值减法等预处理。
4. 前向推理，获得各关键点的置信度图和亲和度场。
5. 解析出关键点坐标，过滤掉低于 `conf_threshold` 的点。
6. 在原图上绘制关键点（圆点）和骨架连接线（彩色线段）。
7. 保存带标注的图像到临时输出目录，生成唯一文件名。
8. 返回 `status=success`，`data` 中包含该图像的绝对路径。

### 5.2 性能指标

- 预期执行时间: < 5s（CPU 下，640x480 分辨率）
- 内存占用: < 500MB

### 5.3 错误处理

- 输入图像路径无效 → 返回 `status=failed`，`message` 说明文件不存在
- 模型加载失败 → 返回 `status=failed`，`message` 包含异常信息
- 图像中未检测到任何关键点 → 仍返回标注图像（可能为空），`message` 提示未检测到关键点
- 推理异常（如内存不足） → 捕获并返回详细错误信息

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "image_path": "./test_data/person.jpg",
  "conf_threshold": 0.3
}
```

预期结果：返回 `status=success`，`data.image_path` 指向一张标记了人体骨架的 JPEG 图像。

### 6.2 边界条件

- 多人图像：同时标注所有人物的关节骨架。
- 低分辨率图像（<100px）：可能无法检测，返回空骨架图像。
- 置信度阈值设为 0→ 所有候选点均绘制；设为 1→ 仅完美置信点绘制，通常骨架稀疏。

## 7. 调用示例

```python
result = execute(image_path="input.jpg", conf_threshold=0.5)
if result["status"] == "success":
    print("输出图像路径:", result["data"]["image_path"])
else:
    print("错误:", result["message"])
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-03-17 | 初始版本，基于 OpenCV DNN 实现姿态提取与骨架绘制 |