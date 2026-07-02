---
id: etth-line-plot
name: ETTh 时序可视化
version: 0.1.0
type: script
language: python
status: active
created: 2025-03-24
---

# ETTh 时序折线图生成器

## 1. 功能概述

读取指定路径的 ETTh 数据 CSV 文件，根据用户选择的字段，选取指定长度的最新时序数据，绘制成折线图并保存为图片。该工具专注于电力变压器温度（ETT）数据集的可视化，支持任意字段的时序趋势展示。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| csv_path | string | 是 | - | ETTh 数据 CSV 文件的绝对路径或相对路径 |
| field | string | 是 | - | 需要展示的字段名称，如 HUFL, HULL, MUFL, MULL, LUFL, LULL, OT 之一 |
| length | int | 是 | - | 需要展示的时序点数量，从数据末尾向前取指定长度的记录 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success/failed) |
| message | string | 结果说明 |
| output_format | string | **必须指定** — image |
| data | dict | 输出数据，格式由 output_format 决定 |

### 3.2 output_format 说明
- `image`: data 含 `{"image_path": "/path/to/result.png"}` — 界面直接绘制图片（仅支持路径方式，不支持 base64）

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| pandas | >=1.3.0 | 读取和分析 CSV 数据 |
| matplotlib | >=3.5.0 | 绘制折线图 |
| os | 标准库 | 路径操作 |

## 5. 运行机制

### 5.1 执行流程

1. 验证输入参数：检查文件是否存在、字段是否在支持的列表中、length 是否为合理正整数。
2. 使用 pandas 读取 CSV 文件，解析 date 列为时间类型并按时间升序排列。
3. 检查指定字段是否存在，提取最后 `length` 行数据。
4. 使用 matplotlib 绘制折线图，设置 X 轴为 date，Y 轴为指定字段值，添加标题、坐标轴标签，并自动格式化日期。
5. 将生成的图表保存为临时 PNG 文件，返回该文件路径。

### 5.2 性能指标

- 预期执行时间: < 2s（对于 20000 行以内的数据）
- 内存占用: < 200MB

### 5.3 错误处理

- 文件不存在 → 返回 `{"status": "failed", "message": "CSV 文件未找到: ..."}`
- 字段名无效 → 返回 `{"status": "failed", "message": "无效字段，支持: HUFL, HULL, MUFL, MULL, LUFL, LULL, OT"}`
- length 超过数据总量 → 自动截断为数据总量并继续执行
- 其他异常 → 捕获并返回详细错误信息

## 6. 测试用例

### 6.1 测试数据描述

假设存在文件 `ETTh1.csv`，包含至少 100 行数据。

```json
{
  "csv_path": "./data/ETTh1.csv",
  "field": "OT",
  "length": 50
}
```

预期输出：返回图片路径，图片显示最近 50 个时间点的 OT 折线图。

### 6.2 边界条件

- `length = 0`：应返回错误或空图警告。
- `length` 大于总行数：自动使用全部数据绘制。
- 字段名大小写敏感：严格匹配要求。

## 7. 调用示例

```python
result = execute(
    csv_path="./ETTh1.csv",
    field="HUFL",
    length=200
)
# 若成功：
# {
#   "status": "success",
#   "message": "折线图已生成",
#   "output_format": "image",
#   "data": {
#       "image_path": "/tmp/etth_line_plot_20250324_123456.png"
#   }
# }
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-03-24 | 初始版本，支持 ETTh 单字段折线图绘制 |