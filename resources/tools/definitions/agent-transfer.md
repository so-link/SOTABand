```markdown
---
id: agent-transfer
name: Agent 迁移器
version: 1.0.0
type: function
language: python
status: active
created: 2026-10-29
---

# Agent 迁移器

## 1. 功能概述

把 SOTABand 里的一个 Agent 打包成单文件迁移包（`.sotaagent.zip`），或把这样的包导入当前环境。

Agent 在磁盘上同样是三元组：规范文档 `agents/definitions/<id>.md`、实现目录
`agents/implementations/<id>/`（`agent.py` + `config.yaml` + `spec.md`），
以及 `agents/registry.json` 里的注册条目（含 `role` 与依赖的 `tools`）。

导入解到**私有 Agent 空间**（`resources-private/agents/`）并合并注册表，因此**无需重启**即可在
Agent 空间看到。与工具迁移的区别：Agent 还依赖**其它工具**，导入后会检查这些工具是否齐备，
缺了会明确列出来（`missing_tools`），否则要到运行时才失败。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| action | string | 是 | 无 | 动作：`export` / `import` / `list` / `info` |
| agent_id | string | 否 | 无 | `export` 时必填，要导出的 Agent id |
| bundle_path | string | 否 | 无 | `import` 时必填，包路径（绝对路径，或本人导出目录 `<USER_HOME>/<uid>/exports/` 下的文件名） |
| bundle_base64 | string | 否 | 无 | `import` 时的包内容（base64） |
| overwrite | bool | 否 | false | 同 id 已存在时是否覆盖（旧文件备份为 `.bak.<时间戳>`） |
| include_assets | bool | 否 | true | `export` 时是否带上二进制资产 |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | `success` / `failed` |
| output_format | string | 固定 `text` |
| message | string | 人话结果（含包路径、体积、依赖工具） |
| data | dict | `export`：bundle_path / file_name / size / sha256 / file_count / tools；`import`：agent_id / name / role / files / overwritten / required_tools / missing_tools |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| 无 | 无 | 仅用 Python 标准库与平台 `core.migration` |

## 5. 运行机制

### 5.1 执行流程

1. `export`：读注册条目 → 定位规范与实现目录 → 收集文件（跳过 `__pycache__`、`.venv`、`*.pyc`）→ 写 `manifest.json`（含 role / 依赖工具 / 依赖包）+ `registry_entry.json` + 两类文件 → 落到本人导出目录 `<USER_HOME>/<uid>/exports/`。
2. `import`：校验格式版本与 id → 逐成员校验路径 → 解到 `resources-private/agents/` → 合并私有 `registry.json` → 检查依赖工具是否齐备。
3. `list`：列出本人导出目录下的 Agent 迁移包。
4. `info`：返回包格式与导出目录。

### 5.2 性能指标

- 单 Agent 导出：< 1s
- 导入：< 1s

### 5.3 错误处理

- Agent 不存在 / 规范与实现都缺失 → `failed` 并说明原因。
- 包格式不符、版本过高、路径非法、超限 → 拒绝导入。
- 同 id 已存在且 `overwrite=false` → 拒绝；内置同名 Agent → 拒绝（执行时内置优先）。
- 依赖的工具缺失 → 导入仍成功，但 `missing_tools` 列出，提示先导入对应工具。

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "test_cases": [
    {"input": {"action": "info"}, "expected_output": {"status": "success"}},
    {"input": {"action": "list"}, "expected_output": {"status": "success"}},
    {"input": {"action": "export", "agent_id": "city-info-query-agent"}, "expected_output": {"status": "success"}},
    {"input": {"action": "export"}, "expected_output": {"status": "failed"}}
  ]
}
```

### 6.2 边界条件

- 导出不存在的 Agent → failed。
- 导入普通 zip → failed。
- 导入只带定义 MD、没有实现目录的包 → 成功但不可运行。

## 7. 调用示例

```python
# 1) 源环境导出
execute(action="export", agent_id="city-info-query-agent")

# 2) 目标环境导入（先确认它依赖的工具已导入）
execute(action="import", bundle_path="agent-city-info-query-agent-0.1.0-20261029-160000.sotaagent.zip")

# 3) 覆盖导入
execute(action="import", bundle_path="agent-xxx.sotaagent.zip", overwrite=True)
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-10-29 | 初始版本：Agent 导出 / 导入 / 列表 / 自述 |
```
