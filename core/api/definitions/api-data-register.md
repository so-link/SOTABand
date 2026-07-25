# api-data-register

## 功能概述
注册一个新的数据集到系统资源中心。

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| id | string | 是 | — | 数据集唯一标识 |
| name | string | 是 | — | 数据集名称 |
| raw_md | string | 是 | — | MD 规范文档 |
| data_path | string | 是 | — | 数据文件路径 |
| file_count | int | 是 | — | 文件数量 |
| total_size | int | 是 | — | 总大小(字节) |
| formats | list | 是 | — | 数据格式列表 |

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| dataset_id | string | 注册的数据集ID |

## 依赖环境
无外部依赖

## 实现
模块: `core.resource.registry.data_registry.DataRegistry.register`
