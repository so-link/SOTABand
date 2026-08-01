---
id: function-plotter
name: 函数画图
version: 0.1.0
type: function
language: python
status: active
created: 2026-08-01
---

# 函数画图

## 1. 功能概述
接收一个一元函数表达式，解析后在给定区间内绘制 y 关于 x 的函数图像，并以图片形式返回。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| equation | string | 是 | 无 | 一元函数表达式，例如 `sin(x)+2*x`（无需写 `y=`）。支持 Python 数学运算符及部分常用函数。 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明 |
| output_format | string | image |
| data | dict | `{"image_path": "/path/to/figure.png"}` |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `image` | `{"image_path":"/path/to/figure.png"}` | 直接绘制图片 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| numpy | >=1.21 | 数值计算，生成 x 采样点 |
| matplotlib | >=3.5.0 | 绘制函数图像 |
| sympy | >=1.9 | 表达式解析与 safe eval |
| Pillow | >=9.0 | 图片保存支持（通常随 matplotlib 安装） |

## 5. 运行机制

### 5.1 执行流程
1. 读取输入参数 `equation`。
2. 使用 sympy 解析表达式，转换为可计算的 lambda 函数。
3. 生成默认 x 区间（`-10` 到 `10`）内的均匀采样点。
4. 计算对应的 y 值，利用 matplotlib 绘制曲线并添加坐标轴、网格。
5. 将图形保存为临时 PNG 文件，返回图片路径。

### 5.2 错误处理
- 表达式非法（语法错误或包含不支持运算） → 返回 `status: failed` 及具体语法错误信息。
- 无法解析为一元函数 → 返回提示信息。
- 内存或磁盘写入异常 → 捕获异常并返回详细错误，`status: failed`。

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-08-01 | 初始版本 |