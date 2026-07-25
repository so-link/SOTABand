# api-llm-get-config

## 功能概述
获取 LLM 服务的配置信息（API Key、Base URL、Model）。

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| provider | string | 否 | deepseek | 服务商 (deepseek/doubao) |

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| api_key | string | API 密钥 |
| base_url | string | API 基础URL |
| model | string | 模型名称 |
| provider | string | 服务商标识 |

## 依赖环境
无外部依赖

## 实现
模块: `config.settings.get_llm_api_config`
