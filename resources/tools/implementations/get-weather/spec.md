---
id: get-weather
name: 获取当前天气
version: 0.1.0
type: api-wrapper
language: python
status: active
created: 2025-04-07
---

# 获取当前天气

## 1. 功能概述

通过调用公开天气 API，根据提供的城市名称获取该城市当前的实时天气信息，包括温度、湿度、天气状况等。适用于需要快速获取天气数据的场景，如聊天机器人、信息展示面板等。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| city | string | 是 | 无 | 需要查询天气的城市名称，支持中文或英文，例如：“北京”、“London” |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success/failed) |
| message | string | 结果说明，成功时返回“获取成功”，失败时返回具体错误原因 |
| data | dict | 天气数据，包含以下子字段 |
| data.city | string | 城市名称 |
| data.temperature | float | 当前温度（摄氏度） |
| data.humidity | int | 相对湿度（百分比） |
| data.condition | string | 天气状况描述（如“晴”、“多云”） |
| data.wind_speed | float | 风速（m/s） |
| data.updated_time | string | 数据更新时间 (ISO-8601) |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| python | >=3.8 | 运行环境 |
| requests | >=2.28.0 | HTTP 请求库，用于调用天气 API |
| python-dotenv | >=1.0.0 | 管理 API 密钥等环境变量（可选） |

## 5. 运行机制

### 5.1 执行流程

1. 接收输入参数 `city`，进行基本校验（非空、合法字符）。
2. 从环境变量读取天气 API 的访问密钥。
3. 构造 API 请求 URL，将城市名称作为查询参数。
4. 发送 HTTP GET 请求到天气服务提供商。
5. 检查响应状态码：若失败，则返回失败状态及错误信息。
6. 解析 JSON 响应，提取所需的天气字段。
7. 按输出规范包装数据，返回标准结果。

### 5.2 性能指标

- 预期执行时间: < 3s（取决于网络延迟和第三方 API 响应速度）
- 内存占用: < 50MB

### 5.3 错误处理

- 参数无效（city 为空或包含非法字符）→ 返回 status=failed，message="参数 'city' 不能为空或包含非法字符"
- API 密钥未配置 → 返回 status=failed，message="天气 API 密钥未正确配置"
- 网络请求超时 → 捕获异常，返回 status=failed，message="请求天气服务超时，请稍后重试"
- API 返回非 200 状态 → 返回 status=failed，message 包含 API 错误信息
- 城市不存在 → API 返回特定错误码，映射为提示城市名称无效

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "valid_city": "北京",
  "invalid_city_empty": "",
  "invalid_city_special": "@#$",
  "english_city": "London",
  "non_existent_city": "Xyzabc123"
}
```

### 6.2 边界条件

- 城市名包含空格（如“New York”）需正确处理，URL 编码后查询。
- 城市名大小写不敏感，API 内部应做归一化。
- 返回的温度值应保留一位小数，极端高温或低温应有明确数值。
- 当 API 返回的数据部分缺失时（如缺少风速），对应字段可设为 null 或默认值，并在 message 中注明“部分数据缺失”。

## 7. 调用示例

```python
from get_weather import execute

result = execute(city="上海")
if result["status"] == "success":
    data = result["data"]
    print(f"{data['city']}当前温度：{data['temperature']}℃，天气：{data['condition']}")
else:
    print(f"获取天气失败：{result['message']}")
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-04-07 | 初始版本，支持根据城市名称获取实时天气 |