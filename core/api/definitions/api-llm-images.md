# api-llm-images

## 功能概述
图片生成/编辑代理通道。工具只传 prompt / image / model，端点与密钥由服务端
按 provider 自动解析（OpenAI 兼容 `/images/generations`），返回图片 URL 列表。
密钥与完整端点细节不随结果外泄——与 `api-llm-chat-with-config` 同属替代
明文 get-key 族的代理通道。

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| prompt | string | 是 | - | 图片生成/编辑提示词 |
| model | string | 是 | - | 图像模型名，如 doubao-seedream-5-0-lite-260128 |
| provider | string | 否 | doubao | 服务商，密钥取 .env 的 `<PROVIDER>_API_KEY` |
| image | string | 否 | - | 编辑场景的 base64 data URI |
| size | string | 否 | 2K | 输出尺寸 |
| watermark | bool | 否 | false | 是否加水印（Ark 专有参数，经 extra_body 透传） |
| n | int | 否 | 1 | 生成张数 |

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| images | list | 生成图片的 URL 列表 |
| model | string | 实际使用的模型名 |
| error | string | 失败原因（成功时无此字段，文本已脱敏） |

## 依赖环境
无外部依赖（走平台统一 LLM 客户端）

## 实现
模块: `core.api.implementations.api_llm`（`ApiLlmImages`，底层 `LLMClient.images_generate`）
