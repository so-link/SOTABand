---
id: number-cuber
name: 数字三次方计算器
version: 0.1.0
type: function
language: python
status: active
created: 2025-01-21
---

# 数字三次方计算器

## 1. 功能概述

接收一个数字作为输入，计算并返回该数字的三次方（即 n³）。支持整数和浮点数。三次方运算定义为 n × n × n。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| number | int/float | 是 | 无 | 需要计算三次方的数字，支持正数、负数和零 |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success/failed) |
| message | string | 结果说明 |
| data | dict | 输出数据 |
| data.input | int/float | 原始输入值 |
| data.result | int/float | 三次方计算结果 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | >=3.8 | 运行环境 |

无第三方库依赖，仅使用 Python 标准库。

## 5. 运行机制

### 5.1 执行流程

1. 接收输入参数 `number`，验证其类型是否为 int 或 float。
2. 执行三次方计算：`result = number ** 3` 或 `result = number * number * number`。
3. 将计算结果封装为统一输出格式并返回。

### 5.2 性能指标

- 预期执行时间: < 1ms
- 内存占用: < 10MB

### 5.3 错误处理

- 参数类型无效（非数字） → 返回 `status: failed`，`message` 说明类型错误。
- 数值溢出（极端大数） → Python 原生支持大整数，通常不会溢出；如遇异常则捕获并返回详细错误信息。

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "test_cases": [
    {"input": 2, "expected": 8},
    {"input": -3, "expected": -27},
    {"input": 0, "expected": 0},
    {"input": 1.5, "expected": 3.375},
    {"input": -0.5, "expected": -0.125}
  ]
}
```

### 6.2 边界条件

- 输入为 `0`：应返回 `0`。
- 输入为极大的正整数：如 `10**100`，应正确计算 `10**300`。
- 输入为极小负浮点数：如 `-1e-50`，应正确计算 `-1e-150`。
- 输入为非数字类型（如字符串 `"abc"`）：应返回验证错误。

## 7. 调用示例

```python
def execute(number):
    if not isinstance(number, (int, float)):
        return {
            "status": "failed",
            "message": f"Invalid input type: expected int or float, got {type(number).__name__}",
            "data": None
        }
    try:
        result = number ** 3
        return {
            "status": "success",
            "message": f"Cube of {number} calculated successfully",
            "data": {
                "input": number,
                "result": result
            }
        }
    except Exception as e:
        return {
            "status": "failed",
            "message": f"Calculation error: {str(e)}",
            "data": None
        }

# 示例调用
result = execute(3)
print(result)  # {"status": "success", "message": "...", "data": {"input": 3, "result": 27}}
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-01-21 | 初始版本，支持整数和浮点数的三次方计算 |