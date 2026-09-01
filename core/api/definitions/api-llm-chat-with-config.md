# LLM自定义配置对话API

用调用方**临时指定**的配置调用大模型，不影响项目 `.env` 的全局配置。

## 与 LLM对话API 的区别

| | LLM对话API | LLM自定义配置对话API |
|---|---|---|
| 配置来源 | 固定用项目 `.env` | 每次调用单独指定（key 也可自动读 `.env`） |
| 切换服务商 | 需改 `.env` 并重启 | 传参即可，互不影响 |
| 适用场景 | 绝大多数工具 | 需要特殊能力的工具（多模态、长上下文、特定模型） |

## 典型场景

项目全局主 key（`LLM_PROVIDER` 指向的那家）是纯文本模型，但某个工具需要**看图**
（多模态）、长上下文或特定模型。此时无需改动全局配置：在 `.env` 里放好
副 key（如 `DOUBAO_API_KEY=sk-xxx`），工具内只传 `provider + model` 即可，
**密钥全程不进聊天框、不进工具代码**。

## 输入

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| messages | list | 是 | OpenAI 格式消息数组，支持 `image_url` 多模态内容 |
| model | string | 是 | 模型名称，可先查 `@服务商目录查询API` 获取推荐列表 |
| api_key | string | 否 | 显式指定 Key。**推荐留空**——留空时自动读 `.env`（见下方降级链） |
| provider | string | 否 | 服务商 id，如 `doubao`/`openai`。**推荐填写**，系统据此解析端点与 Key |
| base_url | string | 否 | OpenAI 兼容端点。**通常无需填写**；仅当服务商不在目录中时才需要 |

### api_key 的降级链（三层）

1. 显式传入了 `api_key` → 直接使用（适合临时试用一把还没进 `.env` 的 key）
2. 未传 → 读 `.env` 中该 provider 的 `<PROVIDER>_API_KEY`（副 key）
3. 该 provider 未配置 → 回落全局主 key（`LLM_API_KEY`）
4. 仍为空 → 报错，提示在 `.env` 添加 `<PROVIDER>_API_KEY`

只给了 `model` 时会先按模型名推断 provider（如 `gpt-4o` → openai）再走降级链。

**因此推荐写法只填 provider + model 两项**，密钥管理收敛在 `.env` 一处。

### base_url 的解析顺序

1. 显式给了 `base_url` → 自定义模式，直接采用
2. 给了 `provider` → 查服务商目录自动解析
3. 只给了 `model` → 按模型名推断服务商（如 `gpt-4o` → openai、`glm-4v-plus` → zhipu）
4. 都无法解析 → 报错，并在 error 中列出所有已知服务商

## 输出

| 字段 | 类型 | 说明 |
|------|------|------|
| content | string | 模型返回的文本内容 |
| api_key_masked | string | 脱敏后的 Key（前4后4），**永不返回原始 Key** |
| model | string | 实际使用的模型 |
| base_url | string | 实际使用的端点 |
| error | string | 失败原因（已脱敏）；成功时无此字段 |

## 安全说明

- `api_key` **只在本次调用的内存中使用**，不写入配置文件、注册表或日志
- 未传 `api_key` 时从 `.env` 读取——读取动作只发生在内存，Key 同样不落日志
- 调用结束立即关闭连接，不保留任何凭据
- 异常信息统一脱敏后返回，Key 不会随报错文本外泄
- 工具作者**不要**把 `api_key` 打印到 stdout —— 调试日志会记录工具输出
- **推荐让 key 走 `.env`**：使用者把各家 key 配进 `.env` 一次，之后所有工具
  只传 provider，密钥永远不进聊天框与工具参数

## 调用示例

**最推荐**（key 走 `.env` 副 key，全程不出现密钥）：

```python
# 前提：.env 中已配置 DOUBAO_API_KEY=sk-xxx
result = _call_api(
    "api-llm-chat-with-config",
    messages=[{"role": "user", "content": "描述这张图"}],
    provider="doubao",              # 自动解析端点，并读取 DOUBAO_API_KEY
    model="doubao-vision-pro-32k",
    max_tokens=1500,
)
content = result.get("content", "")
```

**临时试用一把还没进 `.env` 的 key**（显式传入，用完即弃）：

```python
result = _call_api(
    "api-llm-chat-with-config",
    messages=[{"role": "user", "content": "描述这张图"}],
    api_key="sk-xxxxxx",            # 显式 key 优先于 .env
    provider="doubao",
    model="doubao-vision-pro-32k",
)
```

**自定义服务商**（目录中不存在时才填 base_url）：

```python
result = _call_api(
    "api-llm-chat-with-config",
    messages=[{"role": "user", "content": "你好"}],
    api_key="sk-xxxxxx",
    model="my-model",
    base_url="https://my-gateway.example.com/v1",
)
```

## 其他参数

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| temperature | float | 0.7 | 采样温度 |
| max_tokens | int | 4096 | **推理模型务必 ≥1500**，否则思考会耗尽额度，返回空字符串且不报错 |

## 多模态调用

图片以 base64 内联，走 `image_url` 字段：

```python
messages = [{
    "role": "user",
    "content": [
        {"type": "image_url",
         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        {"type": "text", "text": "评估这张图片的清晰度"}
    ]
}]
```
