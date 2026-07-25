# api-data-delete

## 功能概述
从系统资源中心删除一个数据集。

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| resource_id | string | 是 | — | 数据集ID |

## 输出规范
无输出数据

## 依赖环境
无外部依赖

## 实现
模块: `core.resource.registry.data_registry.DataRegistry.unregister`
