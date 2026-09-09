# 服务商目录查询API

查询系统已登记的模型服务商，包括其**能力**（是否支持图像输入、是否推理模型）、
**推荐模型列表**、**Key 获取地址**与**官方文档**。

## 用途

回答这类问题：

- 「我想用多模态，哪家支持？」
- 「这个服务商有哪些模型可用？」
- 「去哪里申请 API Key？」

配合 `@LLM自定义配置对话API` 使用：先查目录选出 `provider`，再把 provider
传给它——**base_url 由系统自动解析，使用者无需填写**。

## 输入

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| capability | string | 否 | 按能力过滤。`text` 纯文本 / `vision` 图像理解 / `reasoning` 推理模型 |
| provider | string | 否 | 只查指定服务商的详情；不填则返回全部 |

## 输出

| 字段 | 类型 | 说明 |
|------|------|------|
| providers | list | 服务商列表；查单个或按能力过滤时只含匹配项 |
| error | string | 服务商 id 不存在时的提示（含可用列表） |

每项服务商含：

| 字段 | 说明 |
|------|------|
| id | 传给 `@LLM自定义配置对话API` 的 provider 值 |
| name | 显示名，如「豆包 (火山方舟)」 |
| base_url | 系统自动使用的端点（**无需使用者填写**） |
| capabilities | 能力列表：text / vision / reasoning |
| models | 推荐模型名列表，可直接作为 model 参数 |
| default_model | 默认模型 |
| key_url | 申请 API Key 的地址 |
| docs_url | 官方文档地址 |
| notes | 注意事项（如"推理模型 max_tokens 需给足"） |

## 调用示例

```python
# 查所有支持图像输入的服务商
result = _call_api("api-llm-list-providers", capability="vision")
for p in result["providers"]:
    print(p["id"], p["name"], p["models"])

# 查单个服务商详情
result = _call_api("api-llm-list-providers", provider="doubao")
info = result["providers"][0]
print(info["key_url"], info["docs_url"])
```

## 典型流程

```
1. 查目录：_call_api("api-llm-list-providers", capability="vision")
2. 使用者选定 provider = "doubao"，模型 = "doubao-vision-pro-32k"
3. 调用：_call_api("api-llm-chat-with-config",
            provider="doubao", model="doubao-vision-pro-32k",
            api_key=..., messages=...)
   → base_url 自动解析，无需填写
```

## 不在目录中的服务商

仍可使用：显式传 `base_url` 即可（custom 模式）。目录的作用是覆盖常见
服务商、免去查端点，以及为界面提供可选项，不是硬性限制。
