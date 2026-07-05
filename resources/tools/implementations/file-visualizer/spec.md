---
id: file-visualizer
name: 文件可视化器
version: 0.1.0
type: function
language: python
status: active
created: 2025-03-18
---

# 文件可视化器

## 1. 功能概述

根据输入文件的类型，自动选择合适的可视化方式：
- **CSV 文件** → 返回表格数据，用于界面渲染为交互式表格
- **图片文件**（如 PNG, JPG, GIF, BMP） → 返回图片路径，用于界面直接绘制图片

该工具通过检测文件扩展名或 MIME 类型来分流处理，统一接口，简化前端可视化逻辑。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| file_path | string | 是 | - | 待可视化的文件路径（支持绝对路径或相对路径） |
| max_rows | integer | 否 | 1000 | CSV 模式下返回的最大行数，超出部分截断 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success/failed) |
| message | string | 结果说明 |
| output_format | string | 由文件类型决定：`table`（CSV）或 `image`（图片） |
| data | dict | 输出数据，格式见下方说明 |

### 3.2 output_format 说明

**当文件为 CSV 时（output_format = "table"）**
data 包含：
```json
{
  "columns": ["col1", "col2", ...],
  "rows": [["val1", "val2", ...], ...]
}
```
- `columns`：表头列表
- `rows`：数据行，每行为列表，长度与列数一致
- 超出 `max_rows` 时截断行数

**当文件为图片时（output_format = "image"）**
data 包含：
```json
{
  "image_path": "/path/to/original/image.png"
}
```
- 直接返回原始图片文件路径（不进行复制，要求文件可访问）
- 只支持路径方式，不支持 base64

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| pandas | ≥1.3.0 | 读取 CSV 文件，解析为表格数据 |
| Pillow | ≥9.0.0 | 验证图片文件完整性（可选，仅做格式验证） |
| mimetypes | 内置 | 根据文件扩展名判断 MIME 类型 |

## 5. 运行机制

### 5.1 执行流程

1. 接收 `file_path`，检查文件是否存在，不存在则返回 `failed`。
2. 通过 `mimetypes.guess_type(file_path)` 获取 MIME 类型，辅助判断文件类别。
3. 如果 MIME 类型属于 `text/csv` 或文件扩展名为 `.csv`：
   - 使用 `pandas.read_csv()` 读取，若读取失败返回错误。
   - 提取 `columns` 和 `rows`（转换为列表），截取最多 `max_rows` 行。
   - 设置 `output_format` 为 `"table"`，返回数据。
4. 如果 MIME 类型以 `image/` 开头或扩展名为常见图片格式（.png, .jpg, .jpeg, .gif, .bmp, .webp）：
   - 使用 Pillow 打开验证是否为有效图片，无效则返回 `failed`。
   - 直接使用原始 `file_path` 构建 `image_path` 返回，`output_format` 为 `"image"`。
5. 其他文件类型返回不支持错误。

### 5.2 性能指标

- 预期执行时间: < 5s（中小规模 CSV 或图片验证）
- 内存占用: < 500MB（依赖 Pandas 数据大小）

### 5.3 错误处理

- 文件不存在 → `{"status": "failed", "message": "文件不存在"}`
- 类型不支持 → `{"status": "failed", "message": "不支持的文件类型"}`
- CSV 解析失败 → `{"status": "failed", "message": "CSV 格式错误: ..."}`
- 图片损坏 → `{"status": "failed", "message": "无效的图像文件"}`

## 6. 测试用例

### 6.1 测试数据描述

**CSV 输入**：`test.csv` 包含列 title, value，两行数据。
```json
{
  "file_path": "test.csv"
}
```

**图片输入**：`test.png` 为有效 PNG 图片。
```json
{
  "file_path": "test.png"
}
```

### 6.2 边界条件

- 超大 CSV 文件，自动截断到 `max_rows`（默认 1000）。
- 文件无扩展名但 MIME 正确，仍能正常工作。
- 文件扩展名为 `.csv` 但实际内容非 CSV，应返回解析错误。
- 图片文件路径正确但内容损坏，返回 `failed`。

## 7. 调用示例

```python
# 假设函数定义为 execute(params: dict) -> dict
result = execute({
    "file_path": "/data/sales.csv",
    "max_rows": 500
})
# 期望 CSV 返回:
# {
#   "status": "success",
#   "message": "表格数据准备完成",
#   "output_format": "table",
#   "data": {
#     "columns": ["product", "sales"],
#     "rows": [["A", "100"], ["B", "200"]]
#   }
# }

result = execute({
    "file_path": "/images/chart.png"
})
# 期望图片返回:
# {
#   "status": "success",
#   "message": "图片可渲染",
#   "output_format": "image",
#   "data": {
#     "image_path": "/images/chart.png"
#   }
# }
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-03-18 | 初始版本，支持 CSV 表格和图片可视化 |