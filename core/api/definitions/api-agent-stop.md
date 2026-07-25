# api-agent-stop

## 功能概述
停止一个 Agent 进程。

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| agent_id | string | 是 | — | Agent 唯一标识 |

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| agent_id | string | Agent ID |
| status | string | 停止状态 |

## 依赖环境
无外部依赖

## 实现
模块: `core.agent.factory.AgentFactory.stop`
