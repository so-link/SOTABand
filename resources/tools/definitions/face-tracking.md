---
id: face-tracking
name: 人脸检测与框选
version: 0.1.0
type: script
language: python
status: active
created: 2025-03-26
---

# 人脸检测与框选

## 1. 功能概述

使用 OpenCV 内置的 Haar 级联分类器检测图片中的正面人脸，为每个检测到的人脸绘制矩形框，并返回标注后的图像、人脸数量以及所有人脸的位置列表（左上角坐标、宽度、高度）。无需额外深度学习框架，仅依赖 opencv-python。

## 2. 输入规范

| 参数名      | 类型   | 必填 | 默认值 | 说明                         |
|-------------|--------|------|--------|------------------------------|
| image_path  | string | 是   | -      | 输入图片的本地文件路径        |

## 3. 输出规范

### 3.1 标准输出字段
| 字段          | 类型   | 说明                                       |
|---------------|--------|--------------------------------------------|
| status        | string | 执行状态 (success/failed)                  |
| message       | string | 结果说明，包含人脸数目和简要信息           |
| output_format | string | **image**                                  |
| data          | dict   | 输出数据，结构见下方说明                   |

### 3.2 output_format 说明
- `image`: data 必须包含 `{"image_path": "/path/to/result.jpg"}`，同时可附加其他数据。

本工具 output_format 固定为 `image`，额外在 data 中提供：
- `image_path`: 标注后人脸的图像路径
- `faces_count`: 检测到的人脸总数
- `faces_locations`: 列表，每个元素为 `[x, y, w, h]`，表示人脸左上角坐标和宽高

### 3.3 data 结构示例
```json
{
  "image_path": "/tmp/face_output.jpg",
  "faces_count": 3,
  "faces_locations": [
    [120, 100, 80, 80],
    [400, 200, 90, 90],
    [250, 50, 70, 70]
  ]
}
```

## 4. 依赖环境

| 依赖          | 版本   | 用途                       |
|---------------|--------|----------------------------|
| opencv-python | >=4.5  | 图像处理与人脸检测         |
| numpy         | >=1.20 | 图像数组处理               |

## 5. 运行机制

### 5.1 执行流程

1. 使用 `cv2.imread()` 读取输入图片，检查是否成功加载。
2. 将图像转换为灰度图 `cv2.cvtColor()`。
3. 加载 OpenCV 内置的 Haar 级联分类器 `haarcascade_frontalface_default.xml`。
4. 调用 `detectMultiScale()` 检测人脸，获得矩形列表 `(x, y, w, h)`。
5. 在原图上绘制蓝色矩形框 `cv2.rectangle()`。
6. 将标注后的图像保存至 `output_path` 指定路径。
7. 统计人脸数目，构造结果字典，返回标准输出。

### 5.2 性能指标

- 预期执行时间: < 3s（图片分辨率 1920×1080 以内）
- 内存占用: < 300MB

### 5.3 错误处理

- 图片文件不存在或无法读取 → 返回 status=failed, message 包含错误详情。
- 图像为空或尺寸为0 → 返回提示信息。
- 分类器文件缺失 → 提示 OpenCV 安装不完整，返回错误。

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "image_path": "test_faces.jpg",
  "output_path": "result.jpg",
  "scale_factor": 1.1,
  "min_neighbors": 5,
  "min_size": [30, 30]
}
```

预期结果：`result.jpg` 中所有人脸均被蓝色矩形框标注，`faces_count` 与实际人脸数一致，`faces_locations` 包含所有矩形坐标。

### 6.2 边界条件

- 图片中不包含任何人脸：应返回 `faces_count=0`，`faces_locations=[]`，标注图像与原图相同。
- 超高分辨率图片（>4000px）：处理时间可能增加，但不应崩溃。
- 仅仅包含侧脸或遮挡严重的人脸：可能无法检测，工具不强制要求识别。

## 7. 调用示例

```python
# 作为模块调用
from face_tracking import face_tracker

result = face_tracker.execute(
    image_path="input.jpg",
    output_path="output.jpg"
)
print(result)
# 输出: {
#   "status": "success",
#   "message": "成功检测到2张人脸",
#   "output_format": "image",
#   "data": {
#       "image_path": "output.jpg",
#       "faces_count": 2,
#       "faces_locations": [[100, 150, 75, 75], [300, 200, 80, 80]]
#   }
# }
```

## 8. 版本历史

| 版本   | 日期       | 变更             |
|--------|------------|------------------|
| 0.1.0  | 2025-03-26 | 初始版本，Haar 级联人脸检测与框选 |