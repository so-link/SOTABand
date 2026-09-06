# api-model-register

## 功能概述

将一个 AI 模型注册到模型空间，保存模型的元信息（名称、框架、权重路径、输入输出格式等）。注册后模型可通过 api-model-get 查询获取。

## 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| id | string | 否 | 自动生成 `model-{timestamp}` | 模型唯一标识 |
| name | string | 是 | — | 模型名称，如 "YOLOv8-nano" |
| raw_md | string | 是 | — | MD 规范文档（模型的能力描述） |
| framework | string | 是 | — | 模型框架，如 "PyTorch", "ONNX", "TensorFlow" |
| model_path | string | 是 | — | 模型权重文件路径 |
| input_format | string | 否 | — | 输入格式，如 "image/PNG 640x640" |
| output_format | string | 否 | — | 输出格式，如 "json/bbox" |
| version | string | 否 | "0.1.0" | 模型版本号 |
| tags | list | 否 | [] | 标签列表 |
| associated_tool_id | string | 否 | — | 关联的调用工具 ID |

## 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| model_id | string | 注册的模型 ID |
| name | string | 模型名称 |
| tags | list | 标签列表 |
| _action | string | "register_model"（前端识别标记） |

## 依赖环境

- Python >= 3.12

## 运行机制

1. 解析输入参数，构建模型注册条目
2. 写入 registry.json 注册表
3. 保存 MD 规范文档到 definitions/ 目录
4. 如果没有标签，调用 LLM 自动生成标签
5. 返回 model_id 和基本信息
