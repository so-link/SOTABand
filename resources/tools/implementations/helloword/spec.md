---
id: helloword
name: helloword
version: 0.1.0
type: function
language: python
status: active
created: 2026-10-12
---

# helloword

## 1. 功能概述

接收一个输入字符串 `str`，返回拼接后的字符串 `hello{str}`。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| str | string | 是 | 无 | 需要拼接的字符串 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明 |
| output_format | string | text / image / table / file |
| data | dict/list | 输出数据 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `text` | `{"text":"..."}` | 纯文本 |
| `image` | `{"image_path":"/path/to/file.png"}` | 直接绘制图片 |
| `table` | `{"columns":[...], "rows":[[...]]}` | 渲染表格 |
| `file` | `{"file_path":"/path/to/result.csv"}` | 下载链接 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|

## 5. 运行机制

### 5.1 执行流程
1. 读取输入数据
2. 校验参数
3. 执行核心逻辑
4. 返回结果

### 5.2 错误处理
- 参数无效 → 返回验证错误
- 处理异常 → 捕获并返回详细错误

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-10-12 | 初始版本 |