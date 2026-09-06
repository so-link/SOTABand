---
id: electricity-data-forecast-model-training
name: 电力数据预测模型训练
version: 0.1.0
type: script
language: python
status: active
created: 2026-08-13
---

# 电力数据预测模型训练

## 1. 功能概述

读取 CSV 文件 {path}，提取第 {n} 列时序数据，使用 PyTorch 构建并训练 LSTM 时序预测模型。根据长度 {w} 的历史数据预测未来长度 {v} 的数据，训练 {epoch} 轮。训练完成后保存模型文件到 {model}，调用【模型注册API】注册模型，并返回训练集 MAE、测试集 MAE、模型名称和模型路径。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| path | string | 是 | 无 | CSV 文件路径，第一行为表头 |
| n | integer | 是 | 无 | 列编号，从0开始；第 {n} 列传感器数据 |
| w | integer | 是 | 无 | 历史时间窗口大小，表示使用过去 {w} 个时刻的数据 |
| v | integer | 是 | 无 | 预测时间窗口大小，表示预测未来 {v} 个时刻的数据 |
| epoch | integer | 是 | 无 | LSTM 模型训练轮次 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明 |
| output_format | string | table |
| data | dict | 输出数据，包含训练集 MAE、测试集 MAE、模型名称、模型路径 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `table` | `{"columns":["train_mae","test_mae","model_name","model_path"], "rows":[[...]]}` | 渲染表格 |

其中 `data` 示例：
```json
{
  "train_mae": 0.123,
  "test_mae": 0.145,
  "model_name": "electricity_lstm_model.pt",
  "model_path": "/path/to/electricity_lstm_model.pt"
}
```

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| python | >=3.9 | 运行环境 |
| torch | >=2.0 | LSTM 模型构建与训练 |
| pandas | >=1.5 | 读取 CSV 和数据处理 |
| numpy | >=1.24 | 数值计算 |
| scikit-learn | >=1.2 | 数据集划分和 MAE 计算 |

## 5. 运行机制

```python
要求使用pytorch实现LSTM
```

### 5.1 执行流程
1. 读取 CSV 文件 {path}，校验文件存在且包含表头
2. 提取第 {n} 列时序数据，构建长度为 {w} 的输入序列和长度为 {v} 的目标序列
3. 划分训练集和测试集，使用 PyTorch 构建 LSTM 时序预测模型
4. 训练 {epoch} 轮，保存模型文件至 {model}
5. 调用【模型注册API】注册模型 {model}
6. 计算训练集 MAE 和测试集 MAE，返回结果

### 5.2 错误处理
- 文件不存在 → 返回错误信息「CSV 文件不存在」
- 参数无效（如列编号超出范围、窗口大小小于1、epoch小于1） → 返回验证错误
- 处理异常 → 捕获并返回详细错误

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-08-13 | 初始版本 |