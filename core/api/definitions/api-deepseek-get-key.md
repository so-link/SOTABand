# api-deepseek-get-key

## 功能概述
获取 DeepSeek 大模型服务的 API Key 和模型名称，从项目根目录的 .env 文件中读取。

## 输入规范
无参数

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| provider | string | 提供商名称 (deepseek) |
| api_key | string | DeepSeek API 密钥 |
| base_url | string | DeepSeek API 基础URL |
| model | string | DeepSeek 模型名称 |

## 依赖环境
| 依赖 | 说明 |
|------|------|
| .env | 环境变量 DEEPSEEK_API_KEY |

## 实现
模块: `core.api.implementations.api_llm.ApiDeepseekGetKey`
