```markdown
---
id: tool-transfer
name: 工具迁移器
version: 1.0.0
type: function
language: python
status: active
created: 2026-10-29
---

# 工具迁移器

## 1. 功能概述

把 SOTABand 里的一个工具打包成单文件迁移包（`.sotatool.zip`），或把这样的包导入当前环境。

一个工具在磁盘上是三元组：规范文档 `definitions/<id>.md`、实现目录 `implementations/<id>/`、
以及 `registry.json` 里的注册条目。本工具把三者打成一个自包含的 zip，导入时解到**私有资源根**
（`resources-private/tools/`）并合并注册表，因此**导入后无需重启、立即可以调用**。

本工具是平台内置的初始化工具（干净部署包里自带的唯一工具），本身不需要导出。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| action | string | 是 | 无 | 动作：`export` / `import` / `list` / `info` |
| tool_id | string | 否 | 无 | `export` 时必填，要导出的工具 id |
| bundle_path | string | 否 | 无 | `import` 时必填，包路径（绝对路径，或本人导出目录 `<USER_HOME>/<uid>/exports/` 下的文件名） |
| bundle_base64 | string | 否 | 无 | `import` 时的包内容（base64），适合跨机器粘贴 |
| overwrite | bool | 否 | false | `import` 时同 id 工具已存在是否覆盖（旧文件会备份为 `.bak.<时间戳>`） |
| include_tests | bool | 否 | true | `export` 时是否带上实现目录下的 `tests/` |
| include_assets | bool | 否 | true | `export` 时是否带上 `.xml/.onnx/.pt` 等二进制资产 |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | `success` / `failed` |
| output_format | string | 固定 `text` |
| message | string | 人话结果（含包路径、体积、依赖提示） |
| data | dict | `export`：bundle_path / file_name / size / sha256 / file_count / dependencies；`import`：tool_id / name / version / files / overwritten / backups / dependencies / impl_dir |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| 无 | 无 | 仅用 Python 标准库与平台 `core.migration` |

## 5. 运行机制

### 5.1 执行流程

1. `export`：读注册条目 → 定位定义文件与实现目录 → 收集文件（跳过 `__pycache__`、`.venv`、`*.pyc` 等噪声）→ 写 `manifest.json` + `registry_entry.json` + 两类文件 → 落到本人导出目录 `<USER_HOME>/<uid>/exports/`。
2. `import`：校验格式版本与工具 id → 逐成员校验路径（拒绝 `..`、绝对路径、反斜杠）→ 解到 `resources-private/tools/` → 合并私有 `registry.json`。
3. `list`：列出本人导出目录下所有迁移包及其工具元数据。
4. `info`：返回包格式版本与导出目录，便于其他程序探测。

### 5.2 性能指标

- 单工具导出：< 1s（不含大二进制资产）
- 导入：< 1s

### 5.3 错误处理

- 工具不存在 / 定义与实现都缺失 → `status: "failed"` 并说明原因。
- 包格式不符、版本过高、路径非法、超限（包 64MB / 解压 256MB / 单文件 32MB / 2000 个成员）→ 拒绝导入并给出具体原因。
- 同 id 已存在且 `overwrite=false` → 拒绝，提示加 `overwrite`。

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "test_cases": [
    {"input": {"action": "info"}, "expected_output": {"status": "success"}},
    {"input": {"action": "list"}, "expected_output": {"status": "success"}},
    {"input": {"action": "export", "tool_id": "calculate-circle-area"}, "expected_output": {"status": "success"}},
    {"input": {"action": "export"}, "expected_output": {"status": "failed"}}
  ]
}
```

### 6.2 边界条件

- 导出不存在的工具 → failed。
- 导入一个普通 zip（无 manifest）→ failed。
- 导入已被修改过的包（缺 `implementations/<id>/tool.py`）→ 仍导入成功，但执行时会报“工具代码不存在”。

## 7. 调用示例

```python
# 1) 在源环境导出
execute(action="export", tool_id="calculate-circle-area")
# → data.bundle_path = .../exports/tool-calculate-circle-area-0.1.0-20261029-101500.sotatool.zip

# 2) 把 zip 拷到目标环境后导入
execute(action="import", bundle_path="tool-calculate-circle-area-0.1.0-20261029-101500.sotatool.zip")

# 3) 覆盖导入
execute(action="import", bundle_path="tool-xxx.sotatool.zip", overwrite=True)
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-10-29 | 初始版本：工具导出 / 导入 / 列表 / 自述 |
```
