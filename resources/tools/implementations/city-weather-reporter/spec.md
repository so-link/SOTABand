---
id: city-weather-reporter
name: 城市天气预报
version: 0.1.0
type: api-wrapper
language: python
status: active
created: 2026-07-26
---

# 城市天气预报

## 1. 功能概述
根据用户输入的城市名称，自动查询该城市的实时天气信息，并以文本或结构化表格形式返回温度、体感温度、湿度、气压、降雨量、风速风向等关键指标。工具基于免费且无需 API Key 的气象数据接口实现：优先使用 wttr.in（备用源），失败时自动回退到 Open-Meteo（含城市地理编码），两级兜底保障获取成功率。

## 2. 输入规范

| 参数名   | 类型   | 必填 | 默认值   | 说明                                          |
|----------|--------|------|----------|-----------------------------------------------|
| city     | string | 是   | -        | 城市名称，如"北京"、"上海"、"Guangzhou"        |
| lang     | string | 否   | "zh_cn"  | 返回信息的语言，支持 `zh_cn` / `en`            |
| units    | string | 否   | "metric" | 单位制：`metric`（摄氏度）/ `imperial`（华氏度）|
| output_format | string | 否 | "text" | 输出格式：`text`（文本）/ `table`（表格）     |

## 3. 输出规范

### 3.1 标准输出字段
| 字段          | 类型       | 说明                                              |
|---------------|------------|---------------------------------------------------|
| status        | string     | success / failed                                  |
| message       | string     | 结果说明，如“查询成功”或错误原因                  |
| output_format | string     | table                                             |
| data          | dict       | 输出数据，包含天气指标与预警信息                  |

`data` 结构：
```json
{
  "city": "北京",
  "temperature": 24.5,
  "feels_like": 25.0,
  "humidity": 65,
  "pressure": 1013.0,
  "wind_speed": 4.5,
  "wind_direction": "东南",
  "rainfall": 0.0,
  "weather_description": "晴",
  "alerts": [],
  "typhoon_warning": null
}
```

### 3.2 可视化输出格式
| output_format | data 格式                               | 界面渲染方式               |
|---------------|-----------------------------------------|---------------------------|
| `table`       | `{"columns": ["指标","数值"], "rows": [["降雨量","0.0 mm"], ...]}` | 渲染表格，支持多组指标区块 |

## 4. 依赖环境

| 依赖         | 版本     | 用途                           |
|--------------|----------|--------------------------------|
| Python       | ≥ 3.8    | 运行环境                       |
| python-dotenv | ≥ 0.19 | 加载 `.env` 环境变量（可选，缺失时自动跳过） |
| wttr.in      | 免费 API | 第一级天气数据源（无需 API Key） |
| Open-Meteo   | 免费 API | 第二级兜底天气数据源（含 Geocoding，无需 API Key） |

## 5. 运行机制

### 5.1 执行流程
1. 接收 `city`、`units`、`lang`、`output_format` 参数并校验取值范围。
2. 第一级：调用 wttr.in（`https://wttr.in/{city}?format=j1`），按 `units`/`lang` 映射查询参数，解析当前天气（温度、体感温度、湿度、气压、风速、降雨量、天气描述）。常用中文城市名（北京、上海、广州等 13 个）自动映射为英文名以提高命中率。
3. 若第一级失败，第二级：调用 Open-Meteo Geocoding API 将城市名解析为经纬度，再请求 `https://api.open-meteo.com/v1/forecast` 当前小时级预报，提取温度、湿度、气压、降水量、风速风向，并按 WMO 天气代码映射为文字描述。
4. 将结构化数据按 `output_format`（`text`/`table`）封装返回。
5. wttr.in 与 Open-Meteo 均不提供台风/灾害预警数据，返回结果中预警字段为空。

### 5.2 错误处理
- 城市名称无法找到 → 返回 `status: "failed"`, `message: "未找到城市 '{city}' 的天气信息/坐标信息"`
- 两级数据源均失败 → 返回 `status: "failed"`，`message` 携带最后一次请求的错误详情
- 网络请求超时（10s）→ 自动切换下一级数据源；全部失败时提示"天气服务暂时不可用，请稍后重试"
- 数据解析异常 → 返回 `status: "failed"`, `message: "数据格式异常，原始响应: {details}"`
- 完全无降雨/风速数据 → 对应字段填写 0.0，不视作错误

## 6. 版本历史
| 版本   | 日期       | 变更                               |
|--------|------------|------------------------------------|
| 0.1.0  | 2026-07-26 | 初始版本，支持城市天气基础指标查询 |