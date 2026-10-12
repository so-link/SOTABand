---
id: timeseries-classification-runner
name: 通用时序分类训练评测
version: 0.2.0
type: function
language: python
status: active
created: 2026-09-17
---

# 通用时序分类训练评测

## 1. 功能概述

面向 SOTABand Data、Model、Agent 资源体系的通用时序分类工作流。当前支持仓库内组件中
适用于 `SpO2, HR, PR, RR, PI` 五通道输入的模型目录、结构化模型参数和训练参数。

当前目录仅包含可直接接收五通道数据的模型。

## 2. 模式定义

### 模式 1：list-models（查看可用模型）

无需参数。返回当前五通道低氧血症任务可创建的模型目录。

### 模式 2：dataset-source（选择数据集类型）

先选择数据集是原始监护数据，还是已经处理好的五通道窗口数据。

| 参数名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| dataset_source_type | string | 是 | `raw`：原始数据；`processed`：已处理的五通道窗口数据 |

### 模式 3：process-raw-dataset（处理原始数据）

调用仓库内 `resources/workflows/hypoxemia/runtime/raw/run_dedup_pipeline.py` 的 `all` 流程，依次执行：

```text
raw → preprocess → merge → windows
```

如果输入是 ZIP 文件，或目录中包含 ZIP 文件，平台任务会先安全解压到临时目录，
再扫描其中的 `ParameterData/*.csv`。普通目录输入仍直接交给原流水线处理。

当前固定生成 `256:30` 窗口，输出目录中的 `balanced_windows_256s_30s_all` 作为后续五通道数据检查的输入。该模式返回任务请求，由平台任务层执行。

| 参数名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| source_path | string | 是 | 原始数据目录、单个 ZIP 文件，或包含 ZIP 文件的目录 |
| output_root | string | 否 | 处理输出目录 |
| staging_root | string | 否 | ZIP 临时解压目录；建议指定到有足够空间的磁盘 |
| python_executable | string | 否 | 数据处理解释器 |

### 模式 4：model-creation-guide（创建模型引导）

无需参数。区分以下两类请求：

- 派生模型：以目录内模型或已有模型资源为基础，仅覆盖受约束的结构或训练参数；可直接训练。
- 全新网络结构：描述项目中不存在的网络拓扑；必须先新增并验证 PyTorch 实现，不能仅凭文字注册为可训练模型。

### 模式 3：custom-model-design（解析自定义模型结构）

接收自然语言结构描述，提取输入、编码器、时序聚合、分类头和训练设置，并回显为待实现设计。该模式不注册可训练资源。

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| description | string | 是 | 无 | 自定义模型的自然语言结构描述 |

### 模式 3：validate（检查数据集）

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| source_path | string | 是 | 无 | 数据集目录、特征文件路径或已注册数据集 ID |

### 模式 4：register-dataset（注册数据集）

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| source_path | string | 是 | 无 | 已通过检查的本地数据集目录 |
| dataset_name | string | 否 | 目录名 | 数据资源名称 |

### 模式 5：model-config（查看模型配置）

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| model_id | string | 是 | 无 | 已创建的模型资源 ID |

### 模式 6：create-model（创建目录模型资源）

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| dataset_id | string | 是 | 无 | 已注册数据集 ID |
| model_name | string | 是 | 无 | 模型目录中的五通道模型名称 |
| model_resource_name | string | 否 | 模型名称 | 模型资源名称 |
| model_config | object | 否 | 默认结构参数 | 受约束的结构参数 JSON |
| training_config | object | 否 | 默认训练参数 | 受约束的训练参数 JSON |

### 模式 7：derive-model（创建派生模型）

复制已有模型资源的实现和配置，覆盖提供的参数后注册为一个新的、可训练的模型资源。

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| source_model_id | string | 是 | 无 | 源模型资源 ID |
| dataset_id | string | 否 | 源模型数据集 | 关联数据集 ID |
| model_resource_name | string | 否 | 源模型名_variant | 新资源名称 |
| model_config | object | 否 | 无 | 覆盖的结构参数 |
| training_config | object | 否 | 无 | 覆盖的训练参数 |

### 模式 8：train（启动训练）

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| dataset | string | 是 | 无 | 已注册数据集 ID 或受信任本地目录 |
| model_id | string | 否 | SegRNNDualScaleV2 | 模型资源 ID 或目录中的模型名称 |
| python_executable | string | 否 | 环境变量 | 受管理的 Python 3.12 计算解释器 |
| epochs | int | 否 | 140 | 最大训练轮数 |
| batch_size | int | 否 | 32 | 批大小 |
| learning_rate | float | 否 | 0.00025 | 学习率 |
| model_config | object | 否 | 无 | 覆盖模型资源中的结构参数 |
| training_config | object | 否 | 无 | 覆盖模型资源中的训练参数 |

### 模式 9：evaluate（读取评测）

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| task_id | string | 否 | 无 | 已完成训练任务 ID |
| artifact_path | string | 否 | 环境变量 | 模型产物或训练目录 |

### 模式 10：inspect-model（检查或导入模型）

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| artifact_path | string | 否 | 环境变量 | V3 模型产物目录 |
| register_model | bool | 否 | true | 检查通过后注册到模型空间 |

### 模式 11：predict（预测，预留）

第一版训练评测闭环完成后启用单窗口预测适配器。

## 3. 固定约束

- 输入长度 256 秒，采样率 1 Hz，通道顺序固定。
- 按 `group_id` 分组划分，seed=42，validation/test=0.15/0.15。
- 阈值只在验证集选择，测试集仅用于最终报告。
- 不反序列化上传区中的任意 pickle 文件。
- 所有输出仅供研究，不用于临床诊断。
- 参数仅允许工具 Schema 中的字段和值域；未知字段和越界值会在提交任务前拒绝。
