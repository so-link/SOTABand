# api-agent-start

## 功能概述
启动一个 Agent 进程。

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| agent_id | string | 是 | — | Agent 唯一标识 |
| impl_path | string | 是 | — | 实现代码路径 |

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| agent_id | string | Agent ID |
| status | string | 启动状态 |

## 依赖环境
无外部依赖

## 实现
模块: `core.agent.factory.AgentFactory.start`
