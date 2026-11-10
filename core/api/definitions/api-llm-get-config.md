# api-llm-get-config

## 功能概述
获取 LLM 服务的配置信息（掩码视图）。api_key 只返回脱敏值，用于展示与诊断；
调用方如需发起对话请使用 `api-llm-chat` / `api-llm-chat-with-config`，
图片生成请使用 `api-llm-images`，均无需接触明文密钥。

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| provider | string | 否 | deepseek | 服务商 (deepseek/doubao) |

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| api_key | string | API 密钥（脱敏值，如 `sk-1***abcd`） |
| api_key_configured | bool | 该服务商是否已配置密钥 |
| base_url | string | API 基础URL |
| model | string | 模型名称 |
| provider | string | 服务商标识 |

## 依赖环境
无外部依赖

## 实现
模块: `core.api.implementations.api_llm`（配置来源 `config.settings.get_llm_api_config`）
