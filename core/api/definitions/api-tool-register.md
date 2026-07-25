# api-tool-register

## 功能概述
注册一个新的工具到系统工具空间。

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| id | string | 是 | — | 工具唯一标识 |
| name | string | 是 | — | 工具名称 |
| raw_md | string | 是 | — | MD 规范文档 |
| code | string | 是 | — | 工具代码 |
| tags | list | 否 | [] | 标签列表 |

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| tool_id | string | 注册的工具ID |

## 依赖环境
无外部依赖

## 实现
模块: `core.resource.registry.tool_registry.ToolRegistry.register`
