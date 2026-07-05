---
id: api-data-register
name: 数据集注册API
version: 1.0.0
category: resource
status: active
---

# 数据集注册API

## 1. 功能概述
将数据目录注册为数据集，写入 DataRegistry，生成 MD 规范文档。

## 2. 输入规范
| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| id | string | 是 | 数据集唯一标识 |
| name | string | 是 | 数据集名称 |
| raw_md | string | 是 | MD规范文档内容 |
| data_path | string | 否 | 数据目录路径 |

## 3. 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| dataset_id | string | 注册成功的数据集ID |

## 4. 实现
- 模块: core.resource.registry.data_registry.DataRegistry.register()
