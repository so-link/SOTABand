---
id: mnist-digit-recognizer
name: MNIST数字识别器
version: 0.1.0
type: function
language: python
status: active
created: 2025-04-09
---

# MNIST数字识别器

## 1. 功能概述

基于预训练神经网络识别图片中的手写数字（0-9）。模型使用 MNIST 数据集在 PyTorch 框架下训练，可直接应用于输入图片并返回预测数字及置信度。

## 2. 输入规范

| 参数名      | 类型   | 必填 | 默认值 | 说明                                 |
|-------------|--------|------|--------|--------------------------------------|
| image_path  | string | 是   | 无     | 待识别图片的本地文件路径（支持常见格式如 PNG、JPG） |
| model_path  | string | 否   | "./mnist_model.pth" | 预训练模型权重的文件路径 |

## 3. 输出规范

| 字段    | 类型   | 说明                             |
|---------|--------|----------------------------------|
| status  | string | 执行状态 (success / failed)      |
| message | string | 结果说明或错误信息               |
| data    | dict   | 包含识别结果，具体字段如下：     |
| data.predicted_digit | int     | 预测的数字（0-9）                |
| data.confidence     | float   | 预测置信度，范围 [0, 1]          |

## 4. 依赖环境

| 依赖        | 版本    | 用途                     |
|-------------|---------|--------------------------|
| torch       | >=1.8.0| 深度学习框架             |
| torchvision | >=0.9.0| 图像预处理工具           |
| Pillow      | >=8.0  | 图像加载与格式转换       |
| numpy       | >=1.19 | 数值计算基础             |

## 5. 运行机制

### 5.1 执行流程

1. 初始化模型架构（如一个简单的 CNN 或 MLP）并加载预训练权重（`model_path`）。
2. 使用 Pillow 打开图片，转换为灰度模式，缩放到 28×28 像素。
3. 将图片转为 numpy 数组并归一化到 [0, 1] 区间，再转换成 PyTorch 张量，增加 batch 维度。
4. 应用与训练时一致的标准化（如均值 0.1307，标准差 0.3081）或直接使用原像素。
5. 模型前向传播得到 logits，经 softmax 计算各类别概率，取最大概率对应的类别及置信度。
6. 封装结果并返回。

### 5.2 性能指标

- 预期执行时间: < 0.1 秒（不包含模型首次加载）
- 内存占用: < 300 MB（含 PyTorch 运行时）

### 5.3 错误处理

- 图片路径不存在或无法读取 → 返回 `status: failed`，`message: "Image load error: ..."`
- 模型文件缺失 → 返回 `status: failed`，`message: "Model file not found"`
- 图片预处理失败（非合法图片） → 返回相应错误信息
- 执行异常 → 捕获并返回详细 traceback（可配置是否暴露）

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "image_path": "test_digit.png",
  "model_path": "mnist_model.pth"
}
```

测试图片为一个手写数字 7 的 28×28 灰度图。

### 6.2 边界条件

- 空文件或全白图片：置信度较低，可能误判为某一数字，但不应崩溃。
- 非 28×28 尺寸图片：工具自动缩放，尺寸不应影响预测。
- 彩色图片：自动转为灰度，可正常处理。

## 7. 调用示例

```python
from mnist_digit_recognizer import recognize_digit

result = recognize_digit("digit_5.png")
if result["status"] == "success":
    print(f"预测数字: {result['data']['predicted_digit']}, 置信度: {result['data']['confidence']:.4f}")
else:
    print(f"错误: {result['message']}")
```

## 8. 版本历史

| 版本   | 日期       | 变更           |
|--------|------------|----------------|
| 0.1.0  | 2025-04-09 | 初始版本       |