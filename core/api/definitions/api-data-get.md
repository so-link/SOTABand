# api-data-get

## 功能概述
根据数据集名称（或 id）从已注册的数据集中**精确**查找并返回该数据集的详细信息。

只返回当前用户有权限访问的数据集。只做精确匹配——数据处理工具一旦模糊匹配
猜错数据集，会用错数据产出错误结果还报告成功，这是不可接受的。歧义（重名）
或未命中一律返回明确信号，由上层追问用户，绝不自行猜测。

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| name | string | 是 | — | 数据集名称或数据集 id（精确匹配，不做模糊/包含匹配） |

## 匹配规则（按优先级）
1. **name 精确匹配** → 命中唯一：返回该数据集
2. **name 精确匹配命中多个（重名）**：返回 `error: "ambiguous"` + `candidates`
   列表（每项含 id/name），由上层追问用户指定具体 id
3. **id 精确匹配**（name 未命中时）：返回该数据集
   ——这是 Agent 把用户输入的 name 解析成 id 后传给工具的场景
4. **全库精确匹配到但属于他人**：返回 `error: "not_found"` + 明确的无权访问提示
5. **都未命中**：返回 `error: "not_found"`

> 不做任何模糊/包含/相似匹配。相似名称、片段名称一律视为未命中。

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| dataset | dict | 数据集详细信息（id, name, data_path, file_count, total_size, formats 等），未找到时为 null |
| error | string | 仅失败时：`not_found` / `ambiguous` |
| message | string | 仅失败时：失败原因说明 |
| candidates | list | 仅 `error=ambiguous` 时：同名数据集列表 `[{id, name}]` |

## 依赖环境
无外部依赖

## 实现
模块: `core.api.implementations.api_data.ApiDataGet`
