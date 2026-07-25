# api-data-get

## 功能概述
根据数据集名称，从已注册的数据集中查找并返回该数据集的详细信息。

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| name | string | 是 | — | 数据集名称（精确匹配或模糊匹配） |

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| dataset | dict | 数据集详细信息（id, name, data_path, file_count, total_size, formats 等），未找到时返回 null |

## 依赖环境
无外部依赖

## 实现
模块: `core.api.implementations.api_data.ApiDataGet`
