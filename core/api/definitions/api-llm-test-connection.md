# api-llm-test-connection

## 功能概述
测试 LLM 服务连接是否可用。发送一个极短请求（1 token）验证 API Key、Base URL、Model 三者是否配置正确，并返回可读的失败原因（Key 无效 / 模型不存在 / 网络不通）。

未显式传入的参数自动从 .env / 服务商预设表读取，因此可以**不切换默认 provider** 单独测试某个服务商。

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| provider | string | 否 | 当前默认 | 服务商名（deepseek/openai/mimo/...），缺省用 LLM_PROVIDER |
| api_key | string | 否 | 环境变量 | 覆盖 API Key（用于测试未保存到 .env 的配置） |
| base_url | string | 否 | 环境变量/预设 | 覆盖 API 端点 |
| model | string | 否 | 环境变量/预设 | 覆盖模型名 |

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| ok | boolean | 连接是否成功 |
| provider | string | 实际测试的服务商 |
| model | string | 实际测试的模型 |
| base_url | string | 实际测试的端点 |
| api_key_masked | string | 脱敏后的 API Key（前4后4） |
| latency_ms | int | 请求耗时（毫秒），失败也可能有值 |
| message | string | 成功提示或失败原因（中文可读） |

## 错误分类
| 场景 | message 提示 |
|------|--------------|
| Key 无效 / 未授权 | API Key 无效或未授权，请检查 LLM_API_KEY |
| 模型不存在 / 套餐不含 | 模型名不正确，或当前套餐不包含该模型，请检查 LLM_MODEL |
| 网络不通 / 超时 | 无法连接，请检查 LLM_BASE_URL 与网络 |
| 未配置 Key / 端点 / 模型 | 对应配置项为空的提示 |

前两类错误会**追加端点提示**：同一厂商的多套端点（按量付费 / 订阅套餐 / 区域站点）
Key 互不通用，用错端点时服务端同样只回"Key 无效"。因此提示里会列出该厂商的
备选端点与各自适用的 Key 前缀，方便对照自查。

系统已按 Key 前缀自动选择端点（如 MiMo 的 `sk-` / `tp-`）；
前缀无区分度的厂商（Moonshot / Qwen / MiniMax 的中外站、智谱通用端点与
GLM Coding Plan 端点）需用 `<PROVIDER>_BASE_URL` 或 `LLM_BASE_URL` 显式指定。

## 依赖环境
openai SDK（与 LLM 主链路一致）

## 实现
模块: `core.api.implementations.api_llm.ApiLlmTestConnection.call`
