---
id: meal-replacement-shape-prediction-training
name: 代餐奶昔形状预测模型训练
version: 0.1.0
type: script
language: python
status: active
created: 2025-04-01
---

# 代餐奶昔形状预测模型训练

## 1. 功能概述

本工具基于配方与工艺参数，训练回归模型以精准预测代餐奶昔的沉淀率和整体喜好度。  
用户提供包含7个特征字段和2个目标字段的CSV数据集，工具自动完成数据划分、模型训练、性能评估，并保存训练好的模型文件。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| data_path | string | 是 | - | CSV数据集文件路径，需包含7个特征列和2个目标列 |
| model_type | string | 否 | random_forest | 模型类型，支持：random_forest, gradient_boosting, linear_regression, svr, mlp |
| random_state | int | 否 | 42 | 随机种子，保证结果可复现 |
| test_size | float | 否 | 0.2 | 测试集比例 |
| output_dir | string | 否 | ./models | 模型保存目录 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success/failed) |
| message | string | 结果说明，包含训练集和测试集的R²、RMSE以及模型类型 |
| output_format | string | **file** — 输出模型文件 |
| data | dict | 输出数据 |

### 3.2 output_format 说明
- `file`: data 含 `{"file_path": "/path/to/model.pkl"}` — 提供模型文件下载/路径

**补充说明**：实际返回的 `data` 字典中除 `file_path` 外，还会附带 `model_type`、`train_r2`、`test_r2`、`train_rmse`、`test_rmse` 等关键评估指标，`message` 字段以文本形式汇总这些信息。

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| python | >=3.8 | 运行环境 |
| pandas | >=1.3 | 数据读取与处理 |
| scikit-learn | >=1.0 | 模型训练与评估 |
| joblib | >=1.1 | 模型持久化 |

## 5. 运行机制

### 5.1 执行流程

1. **读取数据**：从指定路径加载CSV文件，自动推断特征列（前7个数值列）和目标列（后2个数值列），验证无缺失值。
2. **数据划分**：按 `test_size` 比例随机划分为训练集和测试集，使用 `random_state` 固定随机种子。
3. **模型选择**：根据 `model_type` 参数实例化对应模型（默认随机森林回归器）。
4. **训练与评估**：使用训练集拟合模型，分别在训练集和测试集上计算决定系数 R² 和均方根误差 RMSE。
5. **模型保存**：将训练好的模型序列化为 `model.pkl`，保存至 `output_dir` 目录（自动创建若不存在）。
6. **结果输出**：返回状态、消息（含评估指标）、模型文件路径及模型类型。

### 5.2 性能指标

- 预期执行时间: < 10s（取决于数据集大小，1000行数据约2s）
- 内存占用: < 1GB

### 5.3 错误处理

- 文件不存在 → 返回 `status: failed`，提示文件路径无效
- 数据列数不符合预期 → 提示CSV应包含7个特征列和2个目标列
- 存在缺失值 → 提示数据存在空值，需先清洗
- 模型类型不支持 → 列出有效选项并终止
- 训练或保存异常 → 捕获并返回详细错误信息

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "data_path": "./data/shake_sample.csv",
  "model_type": "random_forest",
  "random_state": 123,
  "test_size": 0.2,
  "output_dir": "./models"
}
```

预期结果：
- 训练集R² > 0.85，测试集R² > 0.7（示例基线）
- 模型保存至 `./models/model.pkl`，返回路径正确

### 6.2 边界条件

- `test_size` = 0.0 或 1.0 时，应提示比例不合理或直接报错
- `random_state` 为负数时仍应正常工作
- 极小数据集（样本数 < 10）应提示数据不足

## 7. 调用示例

```python
from meal_replacement_trainer import train_model

result = train_model(
    data_path="./data/dataset.csv",
    model_type="gradient_boosting",
    random_state=42,
    test_size=0.25,
    output_dir="./models"
)

# result 示例返回值
{
    "status": "success",
    "message": "Training completed. Train R²=0.923, RMSE=3.45; Test R²=0.876, RMSE=4.12. Model type: gradient_boosting",
    "output_format": "file",
    "data": {
        "file_path": "/absolute/path/to/models/model.pkl",
        "model_type": "gradient_boosting",
        "train_r2": 0.923,
        "train_rmse": 3.45,
        "test_r2": 0.876,
        "test_rmse": 4.12
    }
}
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-04-01 | 初始版本，支持5种回归模型，默认随机森林 |