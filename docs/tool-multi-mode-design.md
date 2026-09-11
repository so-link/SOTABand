# 工具多模式（多接口）能力设计文档

> 版本：v1.0（设计稿，未实现）
> 目标：让单个工具支持多个「模式」（mode），每个模式有独立的输入、输出与处理过程，
> 在工具入口处统一分发，在对话界面中先引导用户选择模式、再逐一收集该模式的参数。

---

## 一、需求背景与目标

### 1.1 现状（单模式）

当前工具只支持**单一接口**：

- 工具代码唯一入口 `execute(**kwargs) -> dict`
- MD 规范中只有一套「输入规范」表格
- 注册表的 `param_meta` 是一维参数列表
- 交互 Agent 的参数引导按 `param_meta` 逐项收集

问题：当一个工具天然包含多个子功能（如「数字识别」的「训练」与「测试」），
现有模型只能拆成多个独立工具，或在一个 `execute` 里用大量互斥的可选参数堆叠，
既不优雅，也难以在对话中清晰引导。

### 1.2 目标

1. 工具支持声明**多个模式**，每个模式有独立的：
   - 模式名 / 描述
   - 输入参数列表
   - 输出格式
   - 处理逻辑（实现为工具代码中的独立函数）
2. 工具入口 `execute` 负责**分发**：根据 `mode` 参数调用对应函数。
3. 对话界面调用工具时：**先提示用户选择模式，再针对该模式逐一提问参数**。
4. 向后兼容：**单模式工具（现有所有工具）行为完全不变**。

---

## 二、概念模型

### 2.1 术语

| 术语 | 含义 |
|------|------|
| 模式（mode） | 工具的某个独立子功能，有独立输入/输出/处理过程 |
| 模式标识 | 模式的唯一 key，如 `train` / `test` |
| 分发函数 | `execute` 入口，按 `mode` 路由到具体模式函数 |

### 2.2 示例（数字识别）

```
工具：数字识别（digit-recognition）
├── 模式 train（训练模式）
│   ├── 输入：epoch（训练轮次，int）、n（隐含层规模，int）
│   ├── 过程：用 MNIST 训练三层 CNN，保存模型文件
│   └── 输出：训练集准确率、测试集准确率
└── 模式 test（测试模式）
    ├── 输入：file（图片文件路径，string）
    ├── 过程：加载模型 → 识别数字
    └── 输出：识别到的数字
```

---

## 三、方案设计

### 3.1 总体思路

以「最小侵入 + 向后兼容」为原则，引入一个 `mode` 参数贯穿全链路：

```
MD 规范（声明 modes）
   ↓
参数元数据 param_meta（增加 mode 维度）
   ↓
代码生成（execute 分发 + 各模式函数）
   ↓
执行器（传入 mode 参数）
   ↓
交互 Agent（先选模式 → 再收参数）
   ↓
前端（模式选择 + 参数引导展示）
```

单模式工具**完全不写 mode**，走原有逻辑，行为不变。

---

## 四、MD 规范文档改造

### 4.1 多模式工具的新模板

在现有 MD 基础上，当工具含多模式时，用「模式」章节组织：

```markdown
---
id: digit-recognition
name: 数字识别
version: 0.1.0
type: function
language: python
status: active
created: 2026-09-11
modes: 2            # ← 新增：模式数量（可选，便于解析）
---

# 数字识别

## 1. 功能概述

基于卷积神经网络实现手写数字识别，支持训练与测试两种模式。

## 2. 模式定义

### 模式 1：train（训练模式）

- 模式标识：train
- 描述：用 MNIST 训练三层卷积神经网络，保存模型

#### 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| epoch | int | 是 | 无 | 训练轮次 |
| n | int | 是 | 无 | 隐含层规模 |

#### 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| train_acc | float | 训练集识别准确率 |
| test_acc | float | 测试集识别准确率 |

#### 处理过程
1. 采用 PyTorch 构建三层 CNN
2. 加载 MNIST 训练 {epoch} 轮
3. 保存模型文件
4. 返回训练集/测试集准确率

### 模式 2：test（测试模式）

- 模式标识：test
- 描述：加载已训练模型，对输入图片进行数字识别

#### 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| file | string | 是 | 无 | 待识别的图片文件路径 |

#### 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| digit | int | 识别到的数字 |

#### 处理过程
1. 对图片做必要变换
2. 加载保存的模型
3. 识别并返回数字

## 3. 依赖环境
| 依赖 | 版本 | 用途 |
|------|------|------|
| torch | >=2.0 | 神经网络训练与推理 |
| torchvision | >=0.15 | MNIST 数据集加载 |
| pillow | >=9.0 | 图片读取与变换 |

## 4. 运行机制
### 4.1 通用说明
- 通过 PyTorch 实现，如有 GPU 则采用 GPU 加速。
- 训练模式会保存模型文件到数据目录。

## 5. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-09-11 | 初始版本 |
```

### 4.2 向后兼容

- **单模式工具**：MD 文档保持现状（`## 2. 输入规范` / `## 3. 输出规范`），
  解析器检测到「模式定义」章节时才启用多模式解析，否则回退到现有单模式解析。
- 判断依据：是否存在 `## 模式定义` 或 `### 模式 1：` 等标题。

---

## 五、注册表（registry.json）改造

### 5.1 `param_meta` 增加 mode 维度

当前 `param_meta` 是一维数组，每项 `{name, type, required, default, desc, hints}`。

**改造方案 A（推荐）**：`param_meta` 每项新增可选字段 `mode`：

```json
{
  "param_meta": [
    {"name": "epoch", "type": "int", "required": true, "mode": "train", "desc": "训练轮次"},
    {"name": "n",     "type": "int", "required": true, "mode": "train", "desc": "隐含层规模"},
    {"name": "file",  "type": "string", "required": true, "mode": "test", "desc": "图片文件路径"}
  ],
  "modes": [
    {"id": "train", "name": "训练模式", "desc": "用 MNIST 训练三层 CNN"},
    {"id": "test",  "name": "测试模式", "desc": "加载模型识别数字"}
  ]
}
```

- 单模式工具：`param_meta` 各项**无 `mode` 字段**，`modes` 字段**缺省**，行为不变。
- 新增顶层 `modes` 字段：模式列表（供前端下拉、Agent 引导使用）。

### 5.2 存储不变

- 工具代码仍存 `implementations/{tool-id}/tool.py`
- MD 仍存 `definitions/{tool-id}.md` 与 `implementations/{tool-id}/spec.md`

---

## 六、代码生成改造

### 6.1 TOOL_TEMPLATE 不变

模板头部（`_call_api` / `_llm_chat` / `_call_tool` / `_resolve_path` 等辅助）不变。

### 6.2 CRITICAL RULES 增加多模式规则

在 `_llm_generate` 的 prompt 中新增规则（仅当 MD 含多模式时注入，或始终注入但注明"仅多模式工具适用"）：

```
多模式规则（仅当 MD 定义了多个模式时适用）：
1. execute(**kwargs) 作为唯一入口，负责模式分发：
   def execute(**kwargs):
       mode = kwargs.get("mode", "默认模式id")
       if mode == "train":
           return _mode_train(kwargs)
       elif mode == "test":
           return _mode_test(kwargs)
       else:
           return {"status":"failed","message":f"未知模式: {mode}"}
2. 每个模式实现为独立函数 _mode_<id>(kwargs)，函数内用 kwargs.get 取参数。
3. 各模式函数统一返回 {"status","output_format","message","data"}。
4. 模式之间可共享模块级辅助函数与常量（如模型文件路径）。
5. 训练模式需把模型文件保存到 _DATA_DIR 下的稳定路径，测试模式从同一路径加载。
```

### 6.3 生成流程

1. 解析 MD，检测是否含多模式
2. 若多模式：提取各模式名/id/参数，注入「多模式规则」到 prompt
3. LLM 生成含 `execute` 分发 + 各 `_mode_xxx` 函数的完整代码

---

## 七、执行器（tool_executor）改造

### 7.1 现状

`_build_script` 硬编码：`result = execute(**params)`。

### 7.2 改造

**无需改动**！因为 `mode` 就是普通参数，会随 `params` 一起传入 `execute`：

```python
result = execute(**params)   # params 里已包含 mode="train"
```

执行器天然支持。唯一要注意：`mode` 参数必须在 params 里，由上层（交互 Agent / 前端）正确传入。

---

## 八、交互 Agent 参数引导改造（核心）

### 8.1 现状

`_execute_impl` 流程：
1. 匹配工具 → 2. `_extract_params` 填路径参数 → 3. `_check_missing_params` 找缺失 → 4. 逐一引导

`_check_missing_params` 遍历 `param_meta`，找 `required` 且未填的参数。

### 8.2 改造点

引入「模式选择」作为参数收集的**前置步骤**：

```
匹配到工具（多模式）
   ↓
检测工具是否多模式（registry.modes 存在且非空）
   ↓ 是
先引导用户选择模式：
  yield "该工具支持以下模式：1. 训练模式 2. 测试模式，请选择"
  记录 pending_mode 状态（session_key → {tool_id, tool_name}）
   ↓ 用户回复「训练」「1」等
识别模式 → 确定 mode id
   ↓
按该模式的参数引导（只收集 mode 匹配的 param_meta）
   ↓
参数齐备 → 执行时 params 加入 mode=<id>
```

### 8.3 具体函数改动

| 函数 | 改动 |
|------|------|
| `_pending_calls` 状态结构 | 增加 `mode` 字段；或新增 `_pending_modes: dict[session_key, dict]` |
| 新增 `_get_tool_modes(tool_id)` | 从 registry 读取 `modes` 列表（空表示单模式） |
| 新增 `_resolve_mode_selection(answer, modes)` | 识别用户选择的模式（编号/名称/id），复用 `_resolve_tool_selection` 思路 |
| `_check_missing_params` | 增加 `mode` 过滤：只检查 `param_meta` 中 `mode` 匹配（或无 mode）的参数 |
| `_execute_tool` | 执行前把 `mode` 合并进 params |

### 8.4 引导话术

- 模式选择提示：
  `「数字识别」工具支持以下模式：\n1. 训练模式 - 用 MNIST 训练三层CNN\n2. 测试模式 - 加载模型识别数字\n请回复编号或模式名选择。`
- 选择后引导参数（沿用现有）：
  `要使用「数字识别 · 训练模式」，请提供以下参数:\n\n**epoch** — 训练轮次`

---

## 九、前端改造

### 9.1 工具编辑器（ToolEditorView / tool-editor-store）

- **Step 1（需求描述）**：提示用户在描述多模式时，可用「模式1：xxx」「模式2：xxx」结构化描述；NL→MD 的 prompt 增加多模式识别说明。
- **Step 2（MD 审阅）**：无需特改（MD 文本可编辑）。
- **Step 3（代码预览 + 测试）**：
  - `param_meta` 提取结果若含 `mode`，测试输入区**增加模式下拉框**，切换模式时动态显示对应参数输入框。
  - 沙箱测试时把 `mode` 一并传入。

### 9.2 对话界面（InlineCard / chat 卡片）

- 模式选择引导目前用 `content` 文本 + 用户手动回复即可（复用现有 pending 机制），**无需新增卡片类型**。
- 可选增强：新增 `mode-select` 卡片类型，列出模式供点击选择（类似已有的 `tool-confirm` 卡片），体验更佳。此为可选，非必须。

### 9.3 工具详情视图（ToolDetailView）

- 若工具含多模式，展示各模式的参数说明（可选增强）。

---

## 十、需要修改的文件清单

### 后端

| 文件 | 改动内容 |
|------|---------|
| `core/resource/builder/tool_builder.py` | ① `_parse_spec_inputs` / `extract_param_metadata` 支持解析模式；② `_llm_generate` 的 prompt 增加多模式规则；③ 生成后把 modes 写入注册 |
| `core/resource/registry/tool_registry.py` | `register` 支持写入 `modes` 字段与带 `mode` 的 `param_meta`（若需要） |
| `resources/agents/implementations/interactive_agent/agent.py` | ① 新增 `_get_tool_modes` / `_resolve_mode_selection`；② `_pending_calls` 增加 mode 状态；③ `_check_missing_params` 按 mode 过滤；④ `_execute_tool` 合并 mode 参数；⑤ 模式选择引导话术 |

### 前端

| 文件 | 改动内容 |
|------|---------|
| `frontend/src/stores/tool-editor-store.ts` | 状态增加 `modes` / 当前测试 `mode` |
| `frontend/src/components/center-panel/ToolEditorView.tsx` | Step3 测试区增加模式下拉框，按模式切换参数输入 |
| `frontend/src/services/api/tool.ts` | 测试/调试接口透传 mode（若参数结构变化） |
| （可选）`frontend/src/components/center-panel/InlineCard.tsx` | 新增 `mode-select` 卡片 |

### 文档

| 文件 | 改动 |
|------|------|
| `docs/tool-design.md` | 补充多模式章节 |
| `docs/tool-multi-mode-design.md` | 本文档 |

---

## 十一、兼容性与风险评估

| 风险 | 说明 | 应对 |
|------|------|------|
| 单模式工具被误判为多模式 | MD 解析需严格检测「模式定义」章节 | 用明确的章节标题判断；无则走原逻辑 |
| `_check_missing_params` 改动影响现有工具 | 现有 param_meta 无 `mode` 字段 | 过滤逻辑：`p.get("mode") in (None, "", 当前mode)` 时才纳入 |
| 模式名与参数名冲突 | 用户回复可能既是模式名又是参数值 | 模式选择是独立 pending 阶段，阶段内只解析模式 |
| LLM 生成的多模式代码不规范 | 分发函数写错 | 加强 prompt 约束 + 自动调试循环兜底 |
| 训练模式耗时过长 | 训练可能超过默认超时 | 已有「无超时」机制（timeout=None），训练模式沿用 |

---

## 十二、实现顺序建议

1. **后端 MD 解析 + 参数元数据**（tool_builder）→ 能生成含 modes 的 registry
2. **代码生成 prompt**（tool_builder）→ 能生成 execute 分发代码
3. **交互 Agent 模式引导**（interactive_agent）→ 对话中能选模式、收参数、执行
4. **前端工具编辑器模式选择**（ToolEditorView）→ 测试时可切模式
5. **（可选）前端 mode-select 卡片** → 体验增强
6. 回归测试：验证单模式工具行为不变

---

## 十三、数字识别工具落地示例（目标产物）

### registry.json 条目

```json
{
  "id": "digit-recognition",
  "name": "数字识别",
  "version": "0.1.0",
  "type": "function",
  "language": "python",
  "status": "active",
  "spec_path": "definitions/digit-recognition.md",
  "impl_path": "implementations/digit-recognition/",
  "modes": [
    {"id": "train", "name": "训练模式", "desc": "用 MNIST 训练三层卷积神经网络"},
    {"id": "test", "name": "测试模式", "desc": "加载模型对图片进行数字识别"}
  ],
  "param_meta": [
    {"name": "epoch", "type": "int", "required": true, "mode": "train", "desc": "训练轮次", "hints": [5, 10]},
    {"name": "n", "type": "int", "required": true, "mode": "train", "desc": "隐含层规模", "hints": [64, 128]},
    {"name": "file", "type": "string", "required": true, "mode": "test", "desc": "待识别图片路径", "hints": ["/data/digit.png"]}
  ]
}
```

### tool.py 入口

```python
def execute(**kwargs) -> dict:
    mode = kwargs.get("mode", "test")
    if mode == "train":
        return _mode_train(kwargs)
    elif mode == "test":
        return _mode_test(kwargs)
    return {"status": "failed", "message": f"未知模式: {mode}"}
```

### 对话交互示例

```
用户：用数字识别工具训练模型
Agent：「数字识别」支持以下模式：1.训练模式 2.测试模式，请选择
用户：训练
Agent：请提供参数：epoch（训练轮次）
用户：10
Agent：请提供参数：n（隐含层规模）
用户：128
Agent：（执行训练）训练集准确率 99.1%，测试集准确率 98.7%
```
