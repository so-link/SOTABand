---
id: face-detection
name: 人脸检测
version: 0.1.0
type: function
language: python
status: active
created: 2024-07-22
---

# 人脸检测

## 1. 功能概述

该工具用于对输入图片进行人脸检测，返回检测到的人脸数量以及每个人脸的边界框坐标（x, y, width, height）。适用于需要快速定位图片中人脸位置的应用场景，如照片整理、安防监控、图像预处理等。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| image_path | string | 是 | 无 | 待检测图片文件的路径，支持常见格式（如 .jpg, .png, .bmp） |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success/failed) |
| message | string | 结果说明 |
| data | dict | 检测结果数据 |

**data 结构 (status=success 时):**
```json
{
  "faces_count": 3,
  "faces": [
    {"x": 120, "y": 80, "width": 200, "height": 200},
    {"x": 450, "y": 110, "width": 180, "height": 180},
    {"x": 800, "y": 60, "width": 220, "height": 220}
  ]
}
```

- `faces_count`: 检测到的人脸数量。
- `faces`: 每个人脸边界框的坐标列表，每个对象包含：
  - `x` (int): 边界框左上角 x 坐标。
  - `y` (int): 边界框左上角 y 坐标。
  - `width` (int): 边界框宽度。
  - `height` (int): 边界框高度。

若未检测到人脸，`faces_count` 为 0，`faces` 为空列表。

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| opencv-python | >=4.5.0 | 图像加载与预处理 |
| opencv-contrib-python | >=4.5.0 | 提供预训练的人脸检测模型（DNN） |
| numpy | >=1.19.0 | 数组操作与数据转换 |

*注：使用 OpenCV DNN 模块加载预训练的 Caffe 模型（res10_300x300_ssd_iter_140000.caffemodel 及其 prototxt 文件）进行人脸检测，模型文件需预先下载至指定目录。*

## 5. 运行机制

### 5.1 执行流程

1. 读取输入参数 `image_path`，验证文件是否存在且为有效图片格式。
2. 使用 OpenCV 加载图片并进行预处理（缩放、减均值等）。
3. 通过预训练的深度学习模型进行前向推理，获取人脸的置信度及边界框坐标。
4. 过滤低置信度（默认阈值 0.5）的检测结果，将保留的边界框转换为原始图片坐标系下的整数坐标。
5. 汇总结果，返回标准 JSON 输出。

### 5.2 性能指标

- 预期执行时间: < 5s（对于 1920x1080 分辨率图片在普通 CPU 环境下）
- 内存占用: < 500MB

### 5.3 错误处理

- 参数无效（`image_path` 类型错误或为空） → 返回 `status: failed`, `message: 参数 image_path 无效`
- 文件不存在或无法读取 → 返回 `status: failed`, `message: 文件不存在或无法读取`
- 图片格式不支持 → 返回 `status: failed`, `message: 不支持的图片格式`
- 模型加载或推理异常 → 捕获异常并返回 `status: failed`, `message: 人脸检测执行失败: {详细错误信息}`

## 6. 测试用例

### 6.1 测试数据描述

测试图片 `sample.jpg` 中存在 3 张人脸，大小 800x600，预计检测结果 `faces_count=3`，边界框坐标大致合理。

```json
{
  "image_path": "./test_images/sample.jpg"
}
```

### 6.2 边界条件

- 图片中无人脸：输入不含人脸的风景照，期望返回 `faces_count=0`, `faces=[]`，状态成功。
- 极小图片（如 10x10）：应能被处理，但可能因尺寸过小无法检测，返回空结果或失败（取决于预处理）。
- 极大图片（如 8000x6000）：处理时间可能超出预期，但应能在内存限制内完成。
- 非图片文件（如文本文件重命名为 .jpg）：无法解码，应返回失败状态。

## 7. 调用示例

```python
from face_detection_tool import execute

result = execute(image_path="./photos/family.jpg")
if result["status"] == "success":
    print(f"检测到 {result['data']['faces_count']} 张人脸")
    for i, face in enumerate(result["data"]["faces"]):
        print(f"人脸{i+1}: x={face['x']}, y={face['y']}, width={face['width']}, height={face['height']}")
else:
    print(f"错误: {result['message']}")
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2024-07-22 | 初始版本，基于 OpenCV DNN 的人脸检测功能 |