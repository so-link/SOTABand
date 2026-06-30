```markdown
---
id: calculate-circle-area
name: 计算圆面积
version: 0.1.0
type: function
language: python
status: active
created: 2025-03-20
---

# 计算圆面积

## 1. 功能概述

根据给定的圆的半径，计算并返回该圆的面积。使用标准数学公式：面积 = π × 半径²。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| radius | float | 是 | 无 | 圆的半径，必须为非负数值 |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success/failed) |
| message | string | 结果说明 |
| data | dict | 输出数据，包含 area 字段（float，圆的面积，保留适当精度） |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| math | 内建 | 提供圆周率 π 常量 |

## 5. 运行机制

### 5.1 执行流程

1. 接收输入参数 radius。
2. 校验 radius 是否为数值且非负，否则立即返回参数无效错误。
3. 使用 `math.pi` 和公式 `area = math.pi * radius ** 2` 计算面积。
4. 构建输出结果并返回。

### 5.2 性能指标

- 预期执行时间: < 1ms
- 内存占用: < 1MB

### 5.3 错误处理

- 参数无效（非数值或负数） → 返回 `status: "failed"`，`message` 说明具体参数错误。
- 执行异常（如意外情况） → 捕获异常并返回详细错误信息。

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "test_cases": [
    {
      "input": {"radius": 5},
      "expected_output": {"status": "success", "data": {"area": 78.53981633974483}}
    },
    {
      "input": {"radius": 0},
      "expected_output": {"status": "success", "data": {"area": 0.0}}
    },
    {
      "input": {"radius": -3},
      "expected_output": {"status": "failed", "message": "半径必须为非负数"}
    },
    {
      "input": {"radius": "abc"},
      "expected_output": {"status": "failed", "message": "半径必须为数值"}
    }
  ]
}
```

### 6.2 边界条件

- 半径为 0 时，面积应为 0。
- 半径极大值（如 1e10）应能正确计算，不出现溢出或精度问题。
- 半径输入空值或非预期类型时应被拦截。

## 7. 调用示例

```python
# 执行计算
result = execute(radius=5.0)
print(result)
# 输出: {'status': 'success', 'message': '计算成功', 'data': {'area': 78.53981633974483}}
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-03-20 | 初始版本 |
```