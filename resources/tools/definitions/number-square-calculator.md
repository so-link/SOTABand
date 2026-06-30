---
id: number-square-calculator
name: 数字平方计算器
version: 0.1.0
type: function
language: python
status: active
created: 2025-03-27
---

# 数字平方计算器

## 1. 功能概述

接收一个数值输入，计算并返回该数值的平方结果。支持整数和浮点数，处理简单数学运算。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| number | float/int | 是 | 无 | 需要计算平方的输入数字 |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success/failed) |
| message | string | 结果说明 |
| data | dict | 输出数据，包含字段 `result` (float/int) 表示平方值 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | ≥3.6 | 运行环境 |

## 5. 运行机制

### 5.1 执行流程

1. 接收输入参数 `number`，验证其是否为数值类型（int 或 float）。
2. 计算 `number * number` 得到平方值。
3. 封装结果并返回成功状态。

### 5.2 性能指标

- 预期执行时间: < 1ms
- 内存占用: < 10MB

### 5.3 错误处理

- 参数非数值类型 → 返回 `status: failed` 并提示“输入必须是数字”。
- 数值溢出或其他计算异常 → 捕获并返回详细错误信息。

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "number": 5
}
```

预期输出：
```json
{
  "status": "success",
  "message": "计算成功",
  "data": {
    "result": 25
  }
}
```

### 6.2 边界条件

- 输入为 0 时，返回 0。
- 输入为负数时，返回正数平方（如 -3 返回 9）。
- 输入为浮点数时，返回浮点数平方（如 2.5 返回 6.25）。
- 输入为非数字字符串时，返回错误。

## 7. 调用示例

```python
def execute(number):
    if not isinstance(number, (int, float)):
        return {"status": "failed", "message": "输入必须是数字"}
    try:
        result = number * number
        return {"status": "success", "message": "计算成功", "data": {"result": result}}
    except Exception as e:
        return {"status": "failed", "message": str(e)}

# 调用
result = execute(8)
print(result)  # {'status': 'success', 'message': '计算成功', 'data': {'result': 64}}
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-03-27 | 初始版本 |