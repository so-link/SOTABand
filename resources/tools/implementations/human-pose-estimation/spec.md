---
id: human-pose-estimation
name: 人体关节提取
version: 0.1.0
type: function
language: python
status: active
created: 2025-03-28
---

# 人体关节提取

## 1. 功能概述

基于   OpenCV 和OpenPose 的**真实人体姿态检测**工具。从输入图片中检测出人体 2D 关键点（关节），并在原图上绘制关键点与骨架连线，输出标注后的图片，用于后续动作分析、姿态评估等场景。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| image_path | string | 是 | 无 | 待检测图片的本地文件路径，支持 jpg/png 等常见格式 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success/failed) |
| message | string | 结果说明 |
| output_format | string | **image** — 输出标注后的图片 |
| data | dict | 包含 `{"image_path": "/path/to/result.png"}` |

### 3.2 output_format 说明
- `image`: data 含 `{"image_path": "/path/to/result.png"}` — 界面直接绘制图片（仅支持路径方式，不支持 base64）

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | ≥3.8 | 运行环境 |
| PyTorch | ≥1.12 | 模型推理引擎 |
| torchvision | ≥0.13 | 图像预处理 |
| opencv-python | ≥4.7 | 图像读取、关键点及骨架绘制 |
| numpy | ≥1.21 | 数值计算 |
| 预训练模型权重 | — | 人体姿态估计模型（例如 `pose_resnet50` 或 OpenPose COCO 权重），需放置于指定模型目录 |

## 5. 运行机制

### 5.1 执行流程

1. **加载模型**：初始化姿态估计网络（例如基于 COCO 数据集的 17 关键点模型），加载预训练权重至 CPU/GPU。
2. **图像读取与预处理**：使用 OpenCV 读取图片，转换为 RGB，调整尺寸并归一化为 Tensor。
3. **模型推理**：将预处理后的 Tensor 输入网络，获得每个关键点的热图（heatmap）。
4. **关键点解析**：从热图中提取最高置信度位置，得到 `(x, y, confidence)` 列表。
5. **骨架绘制**：根据预定义的关键点连接关系（如鼻子-左眼、左肩-左肘等），用 OpenCV 在原始图像上绘制圆圈和彩色连线。
6. **结果保存**：将绘制好的图像保存到输出目录（同输入目录或临时目录），生成唯一文件名。
7. **返回路径**：在 data 中返回绘制后图片的绝对路径。

### 5.2 性能指标

- 预期执行时间: < 5s（单张 1920×1080 图片，CPU 推理）
- 内存占用: < 1GB（模型加载后）

### 5.3 错误处理

- 图片不存在或无法读取 → 返回 `status: failed`，message 描述具体错误。
- 模型文件缺失 → 捕获异常，提示检查模型路径。
- 图片中未检测到任何人 → 返回 `status: success`，但 data 中仍包含原图路径（可附加“未检测到人体”告警）。
- GPU 不可用时自动回退至 CPU，消息中注明。

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "image_path": "./samples/person.jpg"
}
```

预期：输出图片中清晰标记出人体 17 个关节点（如鼻、眼、耳、肩、肘、腕、臀、膝、踝）及对应的骨架连线，关节点颜色与连线颜色符合 COCO 标准。

### 6.2 边界条件

- 输入图片内无人：输出原图，message 提示“未检测到人体姿态”。
- 输入图片尺寸极小（如 64×64）：模型可能无法检测，message 提示分辨率过低。
- 输入非图片文件：错误提示“无法解析的图像文件”。

## 7. 调用示例

```python
# 独立函数调用
from pose_estimator import estimate_pose

result = estimate_pose(image_path="/data/input/action.jpg")
# result = {
#     "status": "success",
#     "message": "成功检测到2个人体姿态",
#     "output_format": "image",
#     "data": {"image_path": "/data/output/action_pose.png"}
# }
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-03-28 | 初始版本，支持单张图片的人体 17 关键点检测与可视化 |