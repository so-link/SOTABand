---
id: train-regression-model
name: 训练回归模型
version: 0.1.0
type: script
language: python
status: active
created: 2026-08-05
---

# 训练回归模型

## 1. 功能概述

从指定CSV文件加载数据，按给定比例划分训练集和测试集，使用所选回归模型对数据进行训练，并保存训练好的模型文件；随后调用【模型注册API】将模型信息注册到系统中，最终输出模型文件路径、训练误差以及训练准确率。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| path | string | 是 | - | CSV数据文件的完整路径（第1列为标识或不被使用，其余列作为特征预测最后一列目标值） |
| model_type | string | 是 | - | 回归模型类型，支持：`random_forest`、`xgboost`、`linear_regression` |
| r | float | 是 | - | 测试集占比，取值范围 (0, 1)，训练集为 (1-r) |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明，成功时包含简要信息，失败时描述错误原因 |
| output_format | string | text |
| data | dict | 包含 `model_path`、`training_error`、`training_accuracy` 三个字段 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `text` | `{"model_path":"/tmp/model_xxx.pkl", "training_error":0.123, "training_accuracy":0.876}` | 纯文本展示 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| pandas | >=1.3.0 | 数据加载与处理 |
| scikit-learn | >=1.0.0 | 数据集划分、线性回归、随机森林模型及评估指标 |
| xgboost | >=1.7.0 | XGBoost回归模型（仅在 model_type=xgboost 时需要） |
| joblib | >=1.2.0 | 模型持久化保存 |
| requests | >=2.28.0 | 调用【模型注册API】 |

## 5. 运行机制

### 5.1 执行流程
1. 读取输入数据  
   - 使用 `pandas.read_csv(path)` 加载CSV文件
2. 校验参数  
   - 确认 `path` 文件存在且可读  
   - 确认 `r` 在 (0,1) 范围内  
   - 确认 `model_type` 为允许的类型
3. 执行核心逻辑  
   - 提取特征列（除最后一列外的所有数值列，忽略第一列作为ID的情况）和目标列（最后一列）  
   - 按 `(1-r):r` 比例划分训练集与测试集  
   - 根据 `model_type` 实例化对应的回归模型并训练  
   - 使用训练集评估训练误差（MSE或RMSE）和训练准确率（R²分数）  
   - 将训练好的模型保存至临时文件，路径记录为 `{model}`
4. 调用【模型注册API】  
   - 将模型路径 `{model}` 及相关元信息（如模型类型、训练集大小等）发送至注册API
5. 返回结果  
   - 构造输出结构，包含 `model_path`、`training_error`、`training_accuracy`

### 5.2 错误处理
- 文件不存在 → 返回 `{"status":"failed", "message":"文件不存在: {path}"}`
- 参数无效 → 返回 `{"status":"failed", "message":"参数 {参数名} 无效: ..."}`
- 模型类型不支持 → 返回 `{"status":"failed", "message":"不支持的模型类型: {model_type}"}`
- 处理过程异常 → 捕获并返回 `{"status":"failed", "message":"处理异常: {异常详情}"}`

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-08-05 | 初始版本 |