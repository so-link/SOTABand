# api-model-get

## 功能概述

根据模型名称，从已注册的模型中查找并返回该模型的详细信息（包括 model_path、framework、input/output_format 等）。

## 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| name | string | 是 | — | 模型名称（精确匹配优先，回退模糊匹配） |

## 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| model | dict | 模型详细信息，未找到时返回 null |
| message | string | 未找到时的提示信息 |

model 对象字段：
- id: 模型唯一标识
- name: 模型名称
- framework: 模型框架
- model_path: 权重文件路径
- input_format: 输入格式
- output_format: 输出格式
- version: 版本号
- associated_tool_id: 关联的调用工具 ID
- tags: 标签列表

## 依赖环境

- Python >= 3.12

## 运行机制

1. 接收模型名称参数
2. 同步读取 registry.json
3. 先精确匹配 name，再模糊匹配（大小写不敏感）
4. 找到则返回完整模型信息，未找到返回 null + 提示信息
