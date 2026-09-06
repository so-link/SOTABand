# api-llm-chat-stream

## 功能概述
调用 LLM 进行流式对话。

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| messages | list | 是 | — | 消息列表 |
| temperature | float | 否 | 0.7 | 温度参数 |
| max_tokens | int | 否 | 100000 | 最大输出token数（建议设置较大值，如 100000，以处理大文件内容） |

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| stream | AsyncGenerator | 流式token生成器 |

## 依赖环境
| 依赖 | 版本 | 用途 |
|------|------|------|
| openai | >=1.0 | LLM 客户端 |

## 实现
模块: `core.llm.client.DeepSeekClient.chat_stream`
