---
id: face-detection-opencv
name: 人脸检测与标注
version: 0.1.0
type: function
language: python
status: active
created: 2025-02-25
---

# 人脸检测与标注

## 1. 功能概述
输入一张包含人脸的图片，使用 OpenCV 预训练的人脸检测模型检测人脸位置，在每个检测到的人脸周围绘制矩形框，并在框上方标注人脸序号，最终返回标注后的图片路径。该工具仅依赖 OpenCV，不引入 face_recognition 等第三方高级库，轻量且易于部署。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| image_path | string | 是 | - | 输入图片的文件路径（支持常见格式如 jpg, png, bmp） |
| output_dir | string | 否 | ./output | 输出标注图片的保存目录，若不存在将自动创建 |
| cascade_file | string | 否 | 内置 haarcascade_frontalface_default.xml | OpenCV 级联分类器 xml 文件路径，可使用自定义模型 |
| min_face_size | integer | 否 | 30 | 最小人脸尺寸（像素），用于过滤小区域误检 |
| confidence_threshold | float | 否 | 0.5 | 置信度阈值（仅在使用 DNN 模型时有效，Haar 级联忽略） |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success / failed) |
| message | string | 结果说明，包含检测到的人脸数量等信息 |
| output_format | string | **image** |
| data | dict | 输出数据，格式为 `{"image_path": "/path/to/annotated.jpg"}` |

### 3.2 output_format 说明
- `image`: data 包含 `{"image_path": "..."}` — 界面直接加载并显示标注后的图片（仅支持路径方式，不支持 base64）

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | >=3.8 | 运行环境 |
| opencv-python | >=4.5.0 | 人脸检测与图像处理 |
| numpy | >=1.20.0 | 数组操作 |

## 5. 运行机制

### 5.1 执行流程

1. **加载图片**：使用 OpenCV 读取 `image_path` 指定的图片，若读取失败则返回错误信息。
2. **初始化人脸检测器**：根据配置加载 Haar 级联分类器或 DNN 模型（默认使用 Haar 级联，如需使用 DNN 可指定模型文件路径，但本工具暂简化处理，仅提供 Haar 级联路径）。
3. **检测人脸**：调用检测器获取人脸边界框列表 `(x, y, w, h)`，并依据 `min_face_size` 过滤过小的检测结果。
4. **绘制标注**：在原图上遍历每个有效人脸框，绘制绿色矩形框，并在框上方用白色文字标注序号（从 1 开始）。
5. **保存结果**：将标注后的图片保存到 `output_dir` 下，文件名为 `annotated_<原文件名>`，并返回完整路径。
6. **返回结果**：构造包含状态、消息、`output_format` 和 `data` 的字典。

### 5.2 性能指标

- 预期执行时间: < 经验值 3s（以 1080p 图片为例，含模型加载与保存）
- 内存占用: < 300MB

### 5.3 错误处理

- 参数无效 → 返回 `status: "failed"` 并给出具体验证错误（如文件不存在、格式不支持）。
- 执行异常（如模型文件加载失败、图片写入失败）→ 捕获异常并返回详细错误信息。

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "image_path": "./test/sample.jpg",
  "output_dir": "./test/output",
  "min_face_size": 40
}
```

预期结果：在 `./test/output` 目录下生成 `annotated_sample.jpg`，图片中人脸被框选并标有序号。

### 6.2 边界条件
- 输入图片中无人脸 → 返回消息 `No face detected`，但仍输出原图（无标注），`output_format` 仍为 `image`。
- 图片过大（如 4K）→ 处理时间变长，但仍在可接受范围；若极端情况可考虑先缩放再检测（未实现此特性）。
- `output_dir` 不存在 → 自动创建，若创建失败则报错。

## 7. 调用示例

```python
from your_tool_module import detect_and_annotate_faces

result = detect_and_annotate_faces(
    image_path="./photos/group.jpg",
    output_dir="./annotated",
    min_face_size=50
)
print(result)
# {
#   "status": "success",
#   "message": "Detected 3 face(s), saved to ./annotated/annotated_group.jpg",
#   "output_format": "image",
#   "data": {
#       "image_path": "./annotated/annotated_group.jpg"
#   }
# }
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-02-25 | 初始版本，基于 OpenCV Haar 级联实现人脸检测与序号标注 |