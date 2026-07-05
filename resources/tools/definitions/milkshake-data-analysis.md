---
id: milkshake-data-analysis
name: 奶昔数据分析
version: 0.1.0
type: function
language: python
status: active
created: 2025-04-14
---

# 奶昔数据分析

## 1. 功能概述

本工具基于奶昔的配方与工艺参数，利用机器学习模型精准预测产品性状。通过输入分离乳清蛋白(g)、麦芽糊精(g)、植物油(g)、大豆卵磷脂(g)、均质压力(MPa)、杀菌温度(℃)和存放时间(天)七个特征，可对沉淀率(%)和整体喜好度(1‑9)进行回归预测。工具提供训练和预测两种工作模式：

- **训练模式**：使用包含完整特征与目标值的CSV文件训练选定的回归模型，输出训练集与测试集的评估指标（R²、RMSE），并保存模型文件。
- **预测模式**：加载已训练模型，对新样本CSV（仅含特征）进行预测，以表格形式返回每个实验的预测沉淀率和整体喜好度。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| mode | string | 是 | 无 | 功能选项，取值为 `train`（训练）或 `predict`（预测） |
| csv_file | string | 是 | 无 | CSV数据文件的绝对路径或相对路径（相对于工作目录） |
| model_type | string | 是 | 无 | 模型名称，从预定义集合中选择：`random_forest`, `xgboost`, `gradient_boosting`, `svr`, `linear_regression`, `ridge`（至少5种） |

### CSV文件格式要求

- **训练模式**：文件为逗号分隔值（CSV）格式，包含10列，首行为表头。列顺序为：实验编号、分离乳清蛋白(g)、麦芽糊精(g)、植物油(g)、大豆卵磷脂(g)、均质压力(MPa)、杀菌温度(℃)、存放时间(天)、沉淀率(%)、整体喜好度(1-9)。所有数值字段为整数或浮点数，无缺失值。
- **预测模式**：文件为CSV格式，包含8列，首行为表头。列顺序为：实验编号、分离乳清蛋白(g)、麦芽糊精(g)、植物油(g)、大豆卵磷脂(g)、均质压力(MPa)、杀菌温度(℃)、存放时间(天)。所有数值字段为整数或浮点数，无缺失值。

## 3. 输出规范

### 3.1 标准输出字段

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态：`success` 或 `failed` |
| message | string | 结果说明或错误信息 |
| output_format | string | 输出内容的展示方式，依据当前模式自动确定 |
| data | dict | 输出数据，具体结构由 `output_format` 决定 |

### 3.2 output_format 说明

- **训练模式**：`output_format` 为 `table`，同时附加模型文件路径信息，具体结构如下：
  ```
  {
    "columns": ["数据集", "R²", "RMSE"],
    "rows": [
      ["Training", 0.96, 0.08],
      ["Test", 0.93, 0.12]
    ],
    "model_path": "./models/model_rf.pkl"
  }
  ```
  前端分别以表格展示评估指标，并通过消息提供模型文件路径。

- **预测模式**：`output_format` 为 `table`，结构如下：
  ```
  {
    "columns": ["实验编号", "预测沉淀率(%)", "整体喜好度(1-9)"],
    "rows": [
      ["EXP001", 3.5, 7.2],
      ["EXP002", 4.1, 6.8]
    ]
  }
  ```

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | ≥3.8 | 运行基础 |
| pandas | ≥1.3 | 数据读取与处理 |
| numpy | ≥1.20 | 数值计算 |
| scikit-learn | ≥0.24 | 模型训练、评估（随机森林、梯度提升、SVR、线性回归、岭回归） |
| xgboost | ≥1.4 | XGBoost 回归模型 |
| joblib | ≥1.0 | 模型持久化 |

## 5. 运行机制

### 5.1 执行流程

1. **参数校验**：检查 `mode`、`csv_file`、`model_type` 是否有效，文件是否存在且格式正确。
2. **数据加载**：使用 pandas 读取 CSV，验证列数与数据类型。
3. **分支处理**：
   - **训练模式**：
     1. 构建特征矩阵 X（7列）和目标矩阵 y（2列）。
     2. 按 80% 训练集、20% 测试集随机划分。
     3. 根据 `model_type` 实例化对应回归器（默认超参数）。
     4. 对两个目标分别训练单独的模型（使用 `MultiOutputRegressor` 包装单输出回归器），确保两个目标均被预测。
     5. 在训练集和测试集上计算决定系数（R²）和均方根误差（RMSE）。
     6. 将训练好的模型保存为 `./models/model_{model_type}.pkl`。
     7. 组装包含评估指标表格和模型路径的输出数据。
   - **预测模式**：
     1. 加载 `./models/model_{model_type}.pkl`。
     2. 用加载的模型对输入特征进行预测。
     3. 将预测结果与实验编号合并，构建表格结构。
4. **返回结果**：按照输出规范返回 JSON 序列化结构。

### 5.2 性能指标

- 预期执行时间：< 60秒（取决于数据量和模型复杂度）
- 内存占用：< 500MB

### 5.3 错误处理

- 参数 `mode` 不在 [`train`, `predict`] 中 → 返回验证错误，`status: failed`，`message` 说明合法取值。
- CSV 文件不存在或格式不符合列要求 → 返回文件解析错误。
- 训练模式下数据包含缺失值 → 返回数据质量错误。
- 模型类型不支持 → 返回模型选择错误。
- 预测时模型文件不存在 → 返回模型缺失错误，提示需先训练。
- 模型保存目录（`./models/`）无写权限 → 返回权限错误。

## 6. 测试用例

### 6.1 测试数据描述

**训练请求示例**

```json
{
  "mode": "train",
  "csv_file": "/data/milkshake_train.csv",
  "model_type": "random_forest"
}
```

**预测请求示例**

```json
{
  "mode": "predict",
  "csv_file": "/data/new_batches.csv",
  "model_type": "random_forest"
}
```

### 6.2 边界条件

- CSV 中仅有一条数据：训练时应能正常划分并训练（测试集大小为1）。
- 所有特征值完全相同：模型应能给出预测（可能为常数），评估指标显示 R² 极低。
- 模型名称输入为不同大小写（建议统一转为小写匹配）：如 “Random_Forest” 应识别为有效模型。

## 7. 调用示例

```python
from milkshake_analysis import execute

# 训练随机森林模型
result_train = execute(
    mode="train",
    csv_file="data/train.csv",
    model_type="random_forest"
)
print(result_train)
# 预期输出：status="success", output_format="table", data 包含评估表格和 model_path

# 使用已有模型进行预测
result_pred = execute(
    mode="predict",
    csv_file="data/new.csv",
    model_type="random_forest"
)
print(result_pred)
# 预期输出：status="success", output_format="table", data 包含预测结果表格
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-04-14 | 初始版本，支持训练与预测两种模式，提供随机森林、XGBoost、梯度提升、SVR、线性回归、岭回归等模型 |