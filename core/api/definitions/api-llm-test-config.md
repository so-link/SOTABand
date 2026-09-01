# LLM配置验证API

**一键验证**自定义配置是否可用，在正式使用前先确认配置无误。

## 解决什么问题

使用者填完「供应商 / 模型 / Key」后，最容易出错的是：

1. **模型名填错** —— 比如把 `doubao-vision-pro-32k` 写成 `doubao-vision-pro`
2. Key 无效或已过期
3. 端点不通
4. 该 Key 没有某个模型的权限

这些问题如果等到工具运行时才暴露，会浪费大量时间。本 API 一次全部检查完。

## 与 LLM连接测试API 的区别

| | LLM连接测试API | LLM配置验证API |
|---|---|---|
| 测什么 | 项目 `.env` 的全局配置 | 调用方传入的临时配置 |
| 影响范围 | 全局 | 不影响全局配置 |
| 模型校验 | 只测连通 | **校验模型是否存在 + 列出可用模型** |
| 端点自适应 | 按 Key 前缀选端点 | 按 Key 前缀选端点，失败时**自动试遍其他端点** |

## 输入

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| api_key | string | 是 | 要验证的 Key |
| provider | string | 否 | 服务商 id；不填则按下面顺序解析 |
| base_url | string | 否 | 自定义端点；通常无需填写 |
| model | string | 否 | 填了则校验该模型是否存在 |

端点解析顺序：显式 `base_url` → `provider` 查目录 → 报错并列出已知服务商。

## 输出

| 字段 | 类型 | 说明 |
|------|------|------|
| ok | bool | 是否连通且鉴权通过 |
| provider / base_url | string | 实际解析出的服务商与端点 |
| api_key_masked | string | 脱敏的 Key（**永不返回原始 Key**） |
| models | list | 该 Key 当前可用的全部模型，每项含 `id` / `capabilities` |
| count | int | 可用模型数量 |
| model_valid | bool | 传入 model 时，该模型是否存在 |
| suggestion | string | 模型不存在时，给出最接近的正确模型名 |
| capabilities | list | 模型存在时的能力标注（text/vision/reasoning） |
| hint | string | 提示，如"推理模型 max_tokens 建议 >= 1500" |
| error | string | 失败原因（已脱敏） |
| endpoint_note | string | 默认端点不通、改用另一套端点后成功时的说明 |
| tried_endpoints | list | 全部端点都失败时，逐个端点的失败原因 |

### 端点自适应（同厂商多套端点）

不少厂商按计费方式 / 订阅套餐 / 站点区域拆分出**互不通用的多套端点 + 多套 Key**
（如小米 MiMo：按量付费 `sk-` → `api.xiaomimimo.com`，Token Plan 订阅 `tp-` →
`token-plan-cn.xiaomimimo.com`）。用错端点时服务端只回 401，使用者看不出该换哪个。

本 API 会先用 Key 前缀自动选端点；若仍失败，则把该厂商登记的其余端点逐个试一遍，
命中即在 `endpoint_note` 中告知应改用哪个端点，全部失败则在 `tried_endpoints`
列出每个端点的具体错误。

## 调用示例

```python
result = _call_api(
    "api-llm-test-config",
    provider="doubao",
    api_key="sk-xxxxxx",
    model="doubao-vision-pro-32k",
)
if result["ok"]:
    if result.get("model_valid"):
        print("配置正确，可直接使用")
    else:
        print(f"模型名有误，建议用：{result.get('suggestion')}")
else:
    print(f"连接失败：{result.get('error')}")
```

## 安全说明

- 使用 `/v1/models` 端点，只读取模型列表，不发送任何业务数据
- 返回的 Key 一律脱敏
- 错误信息统一脱敏，防止凭据随报错外泄

## 关于模型列表的时效性

模型列表是**实时从服务商拉取**的，不依赖系统内置的静态目录。
因此厂商上线/下线模型后，本 API 立即反映最新状态，无需等待系统更新。

静态目录（`core/llm/providers.py`）只保留 API 不提供的信息：
端点、能力标注、文档链接、Key 申请地址。
