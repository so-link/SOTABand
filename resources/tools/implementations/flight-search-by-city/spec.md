---
id: flight-search-by-city
name: 航班查询工具
version: 0.1.0
type: api-wrapper
language: python
status: active
created: 2026-07-29
---

# 航班查询工具

## 1. 功能概述

根据用户提供的城市名称（支持中文）和期望返回的最大数量，调用 Aviationstack API 实时查询该城市相关的航班信息，筛选出仅与该城市相关的航班，并以表格形式呈现航班列表。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| city  | string | 是 | - | 要查询的城市名称，支持中文（内部自动转换为 API 所需的英文或 IATA 代码） |
| n     | integer | 是 | - | 最多返回的航班数量，必须为正整数 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明 |
| output_format | string | table |
| data | dict | 包含表格定义的字典 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `table` | `{"columns": [...], "rows": [[...]]}` | 渲染表格 |

**航班表格字段**  
- 航班号 (flight_number)  
- 航空公司 (airline)  
- 出发机场 (departure_airport)  
- 到达机场 (arrival_airport)  
- 计划出发时间 (scheduled_departure)  
- 计划到达时间 (scheduled_arrival)  

每一行对应一个航班的信息。

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| requests | >=2.28.0 | 发送 HTTP 请求调用 Aviationstack API |
| 中文城市名映射文件 | 内置 | 提供从中文城市名到英文城市名或 IATA 代码的映射，确保 API 可正确查询 |

> **注意**：Aviationstack API 密钥 `7aa3e2da4ba27c8a7d386ef8820cddad` 已内置在工具代码中，无需用户提供。

## 5. 运行机制

### 5.1 执行流程
1. **读取输入数据**：接收 `city` 和 `n` 两个参数。
2. **校验参数**：检查 `city` 是否非空，`n` 是否为正整数。
3. **中文城市名转换**：通过内置映射文件将中文城市名转换为 Aviationstack API 接受的查询格式（如英文城市名或 IATA 代码）。
4. **调用 API**：向 `https://api.aviationstack.com/v1/flights` 发送 GET 请求，传入转换后的城市名、API 密钥以及必要的筛选参数。
5. **解析响应**：从返回的 JSON 中提取航班数据，筛选出与指定城市严格相关的航班（出发或到达属于该城市）。
6. **数量限制**：仅保留前 `n` 条符合条件的航班记录。
7. **构建表格输出**：按照规定的表格列整理数据，返回标准输出结构。

### 5.2 错误处理
- **城市名无法转换** → 返回 `{"status": "failed", "message": "不支持的城市名，请检查输入"}`。
- **API 调用失败** (网络问题、密钥无效等) → 返回 `{"status": "failed", "message": "航班信息查询失败，请稍后重试"}`。
- **无匹配航班** → 返回 `{"status": "success", "message": "未找到相关航班信息", "output_format": "table", "data": {"columns": [...], "rows": []}}`。
- **参数 `n` 无效** (非数字、<=0 等) → 返回 `{"status": "failed", "message": "参数 n 必须为正整数"}`。

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-07-29 | 初始版本，实现基于城市名的航班查询与表格输出 |