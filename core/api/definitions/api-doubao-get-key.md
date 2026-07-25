# api-doubao-get-key

## 功能概述
获取豆包(Doubao)大模型服务的 API Key。

## 输入规范
无参数

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| api_key | string | 豆包 API 密钥 |
| base_url | string | 豆包 API 基础URL |
| model | string | 豆包模型名称 |

## 依赖环境
| 依赖 | 说明 |
|------|------|
| .env | 环境变量 DOUBAO_API_KEY |

## 实现
模块: `core.api.implementations.api_llm.ApiDoubaoGetKey`
