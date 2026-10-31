# Tool 完整生命周期设计

> 工具的规格化、生成、测试、注册、发现、调用全流程。
> 核心取舍：简化接口、代码无后处理、用户驱动测试、自动化调试。

---

## 一、核心理念

工具（Tool）是系统中最小的可执行单元。每个工具必须：
1. 有标准化的 MD 规范描述文档
2. 实现统一的 `execute(**kwargs) -> dict` 接口
3. 在资源注册中心登记
4. 通过资源发现器检索
5. 被 Agent 或编排流程调用

与 Agent 的区别：Agent 是独立进程，有自主行为；Tool 是被动调用，无状态，同步执行。

---

## 二、工具接口规范

### 2.1 唯一入口函数

```python
def execute(**kwargs) -> dict:
    """工具唯一入口，所有调用方通过此函数调用工具"""
```

### 2.2 参数访问

```python
# 正确：使用 kwargs.get 提供默认值
param = kwargs.get("param_name", default_value)

# 禁止：直接使用 kwargs["param_name"]（KeyError 风险）
```

### 2.3 返回值规范

```python
{
    "status": "success" | "failed",                           # 必须：执行状态
    "output_format": "text" | "image" | "table" | "file",    # 必须：输出类型
    "message": "...",                                          # 必须：成功说明或失败原因
    "data": { ... }                                            # 必须：实际结果数据
}
```

### 2.4 错误处理

```python
try:
    # 核心逻辑
    return {"status":"success", ...}
except Exception as e:
    return {"status":"failed", "message":str(e)}
```

### 2.5 调用约定

| 规则 | 说明 |
|------|------|
| 参数传入 | 始终通过 `**kwargs` 键值对传入 |
| 返回值 | 始终是 `dict`，方便 JSON 序列化 |
| 无状态 | 每次调用独立，不依赖全局状态 |
| 子进程隔离 | 工具代码写入临时脚本，在独立子进程中 `exec` 执行（永不与 FastAPI 同进程），统一注入 `TOOL_DIR`、强制 UTF-8 输出、屏蔽 `sys.argv` |
| 超时控制 | `ToolExecutor.execute` 默认**无超时**（timeout=None，工具可能长耗时，`/api/tool/{id}/execute` 保持此语义）；沙箱测试与自动调试的单轮执行带超时（`DEBUG_EXEC_TIMEOUT`，默认 60 秒，可在系统设置调整），防止死循环把调试流永久挂起；工具间调用 `_call_tool` 固定 30 秒 |
| 解释器选择 | 按「工具本地 `.venv` → 项目 `venv` → 当前解释器」优先级选择，避免污染系统全局 Python；每级同时探测 Unix `bin/python` 与 Windows `Scripts/python.exe` |

### 2.6 LLM 生成时的强约束（写入 Prompt）

```
接口规范（必须遵守）：
1. 唯一入口：def execute(**kwargs) -> dict
2. 参数访问：kwargs.get("param_name", default)，禁止 kwargs["param_name"]
3. 返回值：{"status":"success"|"failed","output_format":"...","message":"...","data":{...}}
4. 错误处理：所有异常 try/except 包裹，返回 {"status":"failed","message":str(e)}
5. 无全局状态：每次调用独立
6. 文件路径：使用 _PROJECT_ROOT / "data" / ...
7. API调用：使用 _call_api("api-id", param=value)
8. 工具调用：使用 _call_tool("tool-id", param=value)
```

---

## 三、用户添加工具的完整流程

```
用户在左侧资源空间 → 工具空间 → [+] 按钮
    ↓
主面板切换到 → 工具编辑器视图
    ↓
┌─ Step 1: 描述需求 ──────────────────────────────────────────┐
│  自然语言描述工具功能                                          │
│  支持特殊标记：                                                │
│    【API名称】     → 引用系统 API                              │
│    【【工具名称】】 → 引用已注册工具                            │
│  调用 LLM → 生成标准化 MD 工具描述文档                          │
└──────────────────────────────────────────────────────────────┘
    ↓ 用户审阅编辑
┌─ Step 2: 审阅 MD 规范文档 ──────────────────────────────────┐
│  展示生成的 MD 文档，用户可编辑修改                             │
│  确认后 → 调用 LLM 生成完整工具代码                             │
└──────────────────────────────────────────────────────────────┘
    ↓
┌─ Step 3: 代码预览 + 沙箱测试 ───────────────────────────────┐
│  左: 生成的完整 Python 代码                                    │
│  右: 沙箱测试                                                 │
│    - 用户手动输入测试参数（弹窗）                               │
│    - 文件路径参数：提供上传按钮                                 │
│    - 点击"运行测试"执行                                        │
│    - 显示执行结果                                              │
│                                                              │
│  自动调试：                                                   │
│    - 点击"自动调试"启动                                        │
│    - 测试失败 → LLM 分析 → 重新生成代码 → 再测试               │
│    - 最多 10 轮（可在系统设置调整），可随时停止                 │
│    - 下方滚动显示调试日志                                      │
└──────────────────────────────────────────────────────────────┘
    ↓ 用户批准
┌─ Step 4: 注册发布 ─────────────────────────────────────────┐
│  ToolRegistry.register(tool)                                │
│  工具出现在资源空间 → 工具空间列表中                           │
│  可被 Agent 发现和调用                                        │
└──────────────────────────────────────────────────────────────┘
```

---

## 四、工具 MD 规范描述文档

### 4.1 标准模板

```markdown
---
id: {tool-id}
name: {工具名称}
version: 0.1.0
type: function           # function | script | api-wrapper
language: python          # python | shell | javascript
status: active
created: {日期}
---

# {工具名称}

## 1. 功能概述
{用自然语言描述工具做什么}

## 2. 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| param1 | string | 是 | — | 参数说明 |

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
- 文件不存在 → 返回错误信息
- 参数无效 → 返回验证错误
- 处理异常 → 捕获并返回详细错误

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | {日期} | 初始版本 |
```

### 4.2 NL → MD 转换 Prompt

```
你是一个工具规格文档生成器。根据用户描述生成标准化的 Tool MD 规范文档。

规则：
1. tool-id 使用小写字母+连字符
2. type 根据描述推断：function / script / api-wrapper
3. 合理推断输入参数和输出格式
4. 建议合适的依赖库和版本
5. 标注不确定的字段
6. 【重要】用户描述中的【xxx】标记表示系统API调用，【【xxx】】标记表示工具调用
7. 【严格禁止】除非用户描述中明确出现标记，否则不添加系统API或工具引用
```

---

## 五、工具代码生成

### 5.1 核心原则

**LLM 根据模板 + 提示，直接生成完整的可执行 Python 文件。不做任何后处理，完全依赖 LLM 生成符合规范的代码**（`ToolCodeBuilder.build()` 即「模板 + LLM 生成」，与 Agent 生成不同，工具代码没有机械拼接与 `_fix_common_errors` 环节）。

### 5.2 模板结构

`core/resource/builder/tool_builder.py` 中的 `TOOL_TEMPLATE`，为 LLM 提供以下预定义能力：

```python
# === SOTABand 工具标准模板 ===
import os, sys, json, time, requests
from pathlib import Path

# 项目根路径：优先读 TOOL_DIR 环境变量（执行器注入），否则按 __file__ 回溯
_PROJECT_ROOT = ...
_DATA_DIR = _PROJECT_ROOT / "data"

# API 调用辅助：from core.api import get_api; api.call(**params)
def _call_api(api_name: str, **params) -> dict: ...

# LLM 调用辅助：统一走系统配置的 LLM_PROVIDER/LLM_API_KEY/LLM_MODEL，
# 禁止 LLM 生成的代码硬编码服务商端点/模型名或调用 get-key 类 API
def _llm_chat(messages, **kwargs) -> str: ...

# 工具间调用：在「内置+私有」双资源根的 registry.json 中查 id/name
# （私有根优先），双根中定位 tool.py，用「工具 .venv（Unix bin/ 与
# Windows Scripts/ 均探测）→ 当前解释器」的子进程执行，固定 30s 超时
def _call_tool(tool_name: str, **params) -> dict: ...

# 路径辅助：展开 ~、反斜杠归一、消除 ./..、剥离空白与引号，
# 相对路径基于 _PROJECT_ROOT 解析（跨平台边界统一在此处理）
def _resolve_path(path: str) -> str: ...

# === 以下由 LLM 生成 ===
```

### 5.3 生成流程

1. 前端调用 `POST /api/tool/generate-code`，传入 MD 文档
2. 后端解析 MD 中的标记：
   - `【xxx】` → 查找系统 API，注入 `_call_api("api-id", ...)` 的调用示例与输入/输出参数表
   - `【【xxx】】` → 查找注册工具，注入 `_call_tool("tool-name", ...)` 调用示例
3. 构建 Prompt，包含：
   - 模板框架说明（可用函数和变量）
   - MD 规范文档全文
   - API 和工具的调用方式
   - 接口规范约束（含「LLM 调用必须用 `_llm_chat`，禁止硬编码服务商/模型名」）
4. LLM 流式生成完整 Python 文件（响应为 SSE：`reasoning` 思考过程 / `delta` 正文增量 / `done` 最终代码 / `error`）
5. `done` 前对累积文本做 `_clean_code` 清理：剥离 Markdown 代码块围栏（```python 等）与多余前后缀后返回

### 5.4 调用方式区分

| 标记 | 含义 | 代码调用方式 |
|------|------|------------|
| `【API名称】` | 系统 API | `_call_api("api-id", **params)` |
| `【【工具名称】】` | 注册工具 | `_call_tool("tool-id", **params)` |

两者统一使用类似函数调用的模式，简化调用复杂度。

---

## 六、沙箱测试

### 6.1 核心原则

**不做接口检查、不做执行前的准备性静态分析。直接用测试数据驱动工具执行，捕获结果——执行结果就是最好的验证器。**

唯一的例外是安全闸门（见 6.5）：执行前有一道确定性的 AST 静态安全审查，但它不判断代码「对不对」，只拦截破坏性/逃逸性调用。

### 6.2 测试数据获取

- 点击"沙箱测试"时，**弹窗让用户手动输入**测试参数
- 对于**文件路径类型**的参数，提供**上传按钮**（`POST /api/tool/upload-test-file`）：
  1. 用户点击上传 → 选择本地文件
  2. 上传到后端临时目录 `/tmp/sotaband-uploads/{filename}`
  3. 返回临时路径，自动填入测试输入框
- 用户确认输入后，点击"运行测试"

### 6.3 测试执行

`POST /api/tool/test`（SSE 流式）：

1. 代码为空时先调用 `builder.build()` 生成，随后**不落盘**，直接把代码字符串交给 `ToolExecutor.execute(code=...)`
2. `ToolExecutor` 把代码包进统一临时脚本，子进程执行 `execute(**params)`，单次执行附带超时（`DEBUG_EXEC_TIMEOUT`，默认 60 秒），死循环工具不会把测试无限挂起
3. 从子进程 stdout 中倒序找最后一行 JSON 作为 `execute()` 的返回值
4. SSE 推送 `{exit_code, stdout, stderr, success}`；客户端断开时取消执行任务

自动调试与对话界面执行走同一个 `ToolExecutor`，保证「自动调试能过的工具在对话界面中一定能过」。

### 6.4 依赖管理

依赖检测与安装是显式环节，不发生在普通执行路径上：

- `GET /api/tool/{id}/check-deps`：静态提取工具代码的第三方 import，用工具实际解释器逐个 `find_spec` 存在性探测（不执行模块代码，避免 torch 等大包超时误报），返回缺失清单及 pip 包名映射（`PIL→Pillow`、`cv2→opencv-python-headless` 等，统一维护在 `core/resource/deps.py`）；「添加工具到工作空间」时前端据此提示安装
- `POST /api/tool/{id}/install-deps`：安装缺失依赖到工具独立 `.venv`（uv 管理，缺失时自动引导创建，一次性装入工具全部第三方依赖，完成后 `uv pip freeze` 写工具目录 `requirements.txt` 精确 pin；多工具 venv 经 uv 全局缓存硬链接共享，近乎零磁盘增量，版本互不冲突）
- 自动调试循环中遇到 `ModuleNotFoundError` 时，由**系统自动**调用 `install_deps_stream` 安装缺失库（LLM 不参与装依赖）：失败输出命中源码编译特征（缺编译器/工具链，Windows 最常见）时先以 `--only-binary=:all:` 确定性回退预编译 wheel 重试（不消耗 LLM 轮次，事件流 `pip_fallback` 展示），回退仍失败才提示 LLM 换替代方案
- 执行解释器按「工具 `.venv` → 项目 `venv` → 当前解释器」优先级选择（见 2.5）

### 6.5 静态安全审查（执行前闸门）

沙箱执行入口（`sandbox_execute`）在起子进程之前先跑 `core/security/code_scan.py` 的 AST 静态扫描（todo 3.2）。基于 `ast.parse` 而非正则匹配（正则可被换行/别名绕过），两级结论：

- **阻断项（errors）**：几乎只用于破坏/逃逸的调用——shell 执行（`os.system`/`os.popen`/`subprocess.getoutput` 等，以及任何子进程调用显式 `shell=True`——命令串整个交给 /bin/sh，与 shell 执行同级）、动态执行（`eval`/`exec`/`compile`/`__import__`）、直接删除原语（`os.remove`/`shutil.rmtree` 等）、原始套接字与 FFI（`socket`/`ctypes`/`pty`/`resource`）、动态导入（`importlib.import_module`）。命中即不执行，返回 `exit_code=1` 的失败结果，`stderr` 携带中文错误清单——自动调试循环把它喂给 LLM，下一轮重写代码。
- **警告项（warnings）**：合法工具常见能力（subprocess 跑本地命令（列表参数、无 shell=True）、`open` 读写文件、requests 联网），以及需要人工确认的可疑信号——字符串字面量中的 `../` 上跳路径（拼接用户输入可能越权出 TOOL_DIR）、字面量 URL 指向本机/内网/链路本地地址、`while True` 体内找不到可达退出（LLM 忘写退出条件的典型形态）、≥10⁷ 量级的字面量分配、无守卫自递归（函数无条件调用自身且体内无 if/try/assert 出口，运行必触发 RecursionError；互递归静态不可判、接受漏报）、字符串字面量开头的单一平台硬编码路径（`/tmp/` 等 POSIX 绝对路径、`~/`、`C:\` 盘符——跨平台分发必然失效，建议 `_PROJECT_ROOT` / `TOOL_DIR` / `Path.home()` 解析）。工具在独立子进程运行，警告项属于平台信任模型允许的范围，记录展示但不阻断；error 级判定需要数据流分析（参数是否来自用户输入），静态层不做，宁可漏报不误杀（`shell=变量名` 的取值无法静态判定，按 warning 兜底）。

设计基线是「对全部内置工具零误杀」，直接运行 `python -m core.security.code_scan` 即回归复核（扫描内置工具 `tool.py`，应输出 0 errors）。闸门分布：**落盘前**——`POST /api/tool/register`、`POST /api/tool/{id}/save-code`、`POST /api/tool/import`（对包内全部 `.py` 成员审查，命中 400，被阻断的代码不落盘）；**执行前**——`ToolExecutor.execute/execute_sync` 入口统一复查（对话执行、沙箱测试、Agent 调用、自动调试共享这同一 choke point，存量工具也无法绕过），沙箱执行入口 `sandbox_execute` 额外前置一层以便自动调试循环拿到结构化错误重写代码。

---

## 七、自动调试

### 7.1 入口

- 沙箱测试按钮旁边提供**"自动调试"按钮**
- 点击后启动自动化调试循环

### 7.2 调试流程

```
┌─────────────────────────────────────────────────────┐
│  自动调试循环（默认最多 10 轮，可配置）              │
│                                                     │
│  1. 使用测试输入执行工具代码                           │
│  2. 检查执行结果：                                    │
│     ├── 成功 → 调试完成 ✅                            │
│     └── 失败 → 进入步骤 3                             │
│  3. 将失败上下文追加进修复对话，发给 LLM：             │
│     - 首轮：完整代码 + 测试输入 + 执行输出 + 约束规则  │
│     - 后续轮：仅最新执行输出/错误信息                  │
│       （上一轮代码与修复理由在对话历史中，LLM 能看到   │
│        自己此前试过什么，避免重复试错）                │
│  4. LLM 生成新代码 → 自动更新到界面代码展示区           │
│  5. 重复步骤 1                                        │
│                                                     │
│  用户可随时点击"停止调试"终止                          │
└─────────────────────────────────────────────────────┘
```

修复对话为**跨轮累积**的多轮 messages（todo 2.3）：首轮发全量上下文，之后每轮只补发最新执行结果，历史保留首条 Prompt + 最近 12 条防膨胀；LLM 能看到自己此前的修复尝试，减少重复试错与逐轮 token 重放。执行等待与 LLM token 消费均为事件驱动（`asyncio.wait` / `asyncio.Queue`），停止信号即时生效，不再有固定间隔的忙轮询。

### 7.3 调试界面

- 测试界面下方**滚动显示**调试日志
- 日志重点输出：
  - 每轮的**测试输出**（stdout / stderr）
  - LLM 的**分析过程**（流式逐 token 显示）
  - 失败原因摘要
- **不输出完整修改代码**，代码变化自动更新到界面代码展示区
- 日志格式示例：
  ```
  [第3轮] 测试执行...
  [第3轮] 输出: {"status":"failed","message":"NameError: name 'x' is not defined"}
  [第3轮] 🤔 LLM分析: 变量x未定义，需要在函数顶部初始化...
  [第3轮] ✅ 代码已更新
  ```

### 7.4 停止机制

- 用户点击"停止调试"按钮 → 前端调用 `POST /api/tool/auto-debug/stop`（按 tool_id 设置全局停止标志，即使 SSE 连接已断开也能可靠停止后台调试）
- 前端关闭 SSE 连接（`AbortController.abort()`）时后端同样设置停止标志并取消 producer
- 达到配置的最大轮数（默认 10 轮，`.env` 的 `DEBUG_MAX_ROUNDS` 或系统设置可调，上限 100）仍未通过 → 自动停止，写入调试日志并提示用户手动介入
- 每轮沙箱执行带超时（`.env` 的 `DEBUG_EXEC_TIMEOUT` 或系统设置可调，默认 60 秒）：工具代码陷入死循环时该轮直接按失败进入下一轮，不会永久挂起调试流

调试过程与结果持久化到项目 `logs/{tool_id}_{timestamp}.md`（写入前经密钥脱敏），每轮的执行结果、LLM 分析、依赖安装事件都记入其中。

---

## 八、工具注册

### 8.1 注册流程

注册（`POST /api/tool/register`）**不强制要求先通过沙箱测试**，用户核验即为落地门槛：

```
ToolRegistry.register(resource)
    │
    ├── 1. 写入条目到私有根 registry.json（读时合并内置根，同 id 私有覆盖内置）
    │      字段: id/name/version/type/language/status/spec_path/impl_path/
    │            input_schema/output_schema/param_meta/modes/tags/usage_count/
    │            created_at/owner/visibility=public
    ├── 2. MD 规范保存到私有根 definitions/{tool-id}.md
    └── 3. 代码保存到私有根 implementations/{tool-id}/tool.py，
           同时写 spec.md 副本与可选 tests/test_*.json 测试数据
```

条目中的 `param_meta`（从 MD 输入规范解析的参数元数据）与 `modes`（多模式工具的 `parse_modes` 结果，单模式工具为空）供前端渲染执行表单。`owner` 记录创建者，`visibility=public`——工具是共享代码资产，不做归属隔离。未传 `tags` 时注册后异步调 LLM 生成 3-5 个中文标签。

### 8.2 存储结构（双资源根）

```
resources/tools/                      # 内置根（随仓库分发，含预置工具）
├── registry.json                     # 内置工具注册表
├── definitions/{tool-id}.md          # MD 规范文档
└── implementations/{tool-id}/
    ├── tool.py                       # 工具实现
    └── spec.md                       # MD 规范文档副本

../resources-private/tools/           # 私有根（SOTABAND_PRIVATE_RESOURCES 可覆盖）
├── registry.json                     # 新注册工具一律写这里
└── implementations/{tool-id}/
    ├── .venv/                        # 工具独立虚拟环境（uv 管理，装依赖时自动创建）
    └── requirements.txt                # uv freeze 的精确依赖 pin（存在即可重建 .venv）
```

读取（发现、执行、代码查看）合并两个根，同 id 以私有为准；写入只进私有根。执行器与 `_call_tool` 都在双根中定位 `tool.py`，`_call_tool` 查 id 时同样双根都查、私有根优先。

### 8.3 导出与导入（zip 工具包）

`core/resource/tool_package.py` 定义跨机流转的归档格式（`{tool-id}.zip`）：

```
manifest.json                  # format=sotaband-tool / archive_version / tool_id / created_at
registry.json                  # 精选注册表字段（丢弃 usage_count/created_at/owner/status，导入时重新生成）
definitions/{tool-id}.md       # 必含；{tool-id}-demand.md / -reference.md 存在才带
impl/tool.py                   # 必含
impl/spec.md                   # MD 规范副本
impl/requirements.txt          # uv freeze 精确 pin（环境真相来源）
impl/tests/test_*.json         # 测试数据
impl/<assets>                  # 资产文件（如人脸检测的 cascade XML）
```

- **导出** `GET /api/tool/{id}/export`：内置与私有工具均可；`.venv`/`__pycache__` 永不进包（虚拟环境由 uv 按 `requirements.txt` 秒级重建，包体仅含源码、文档与资产）。
- **导入** `POST /api/tool/import`（multipart `file` + `overwrite`）：校验 manifest/registry/`tool.py` 必含、`tool_id` 白名单（`^[a-z0-9][a-z0-9_-]*$`）、manifest 与 registry id 一致、成员路径防 zip-slip、未压缩体积与成员数上限（zip 炸弹）。通过后复用 `register()` 落私有根——与重新注册语义一致；同名冲突未带 `overwrite` 返回 409，由 UI 确认后重试。导入内置同名 id 时私有副本遮蔽内置（响应 `overrode_builtin=true`，UI 警示；删除私有副本即恢复）。
- 依赖不在导入时安装，与注册一致，由 check-deps / install-deps 懒安装接管。导入时对包内全部 `.py` 成员做静态审查（危险调用阻断返回 400 不落盘），并校验非标准库导入是否在 `requirements.txt` 中声明——未声明项以 `import_warnings` 随 200 响应返回（依赖漂移提示，不阻断），UI 在导入成功提示条中透明展示。

---

## 九、工具发现

```python
class ToolDiscoverer(BaseDiscoverer):
    async def search(self, query=None, **filters) -> list[dict]:
        """关键词匹配 name/id/tags，支持 type / tags 过滤（当前为字符串匹配，非语义搜索）"""

    async def get_by_tags(self, tags) -> list[dict]: ...
    async def get_by_input_format(self, fmt) -> list[dict]:
        """按 input_schema.formats 过滤"""

    async def match_by_capability(self, description) -> list[dict]:
        """按能力描述打分匹配（当前实现为关键词命中计分，非 LLM 语义搜索）"""
```

需要真正的语义匹配时走 `POST /api/data/match-tools` 那样的 LLM 匹配路径。

---

## 十、工具调用

```python
# 方式 1: 后端代码统一入口（子进程执行，Agent/路由都走这里）
from core.executor.tool_executor import ToolExecutor
result = await ToolExecutor.execute(tool_id="eeg-bandpass-filter",
                                    params={"data_path": "/data/subj01.edf"})

# 方式 2: 通过 HTTP 调用
POST /api/tool/{tool_id}/execute
{"params": {"data_path":"/data/subj01.edf","channels":[0,1,2]}}

# 方式 3: 工具间调用（模板提供，子进程执行，固定 30s 超时）
_call_tool("eeg-bandpass-filter", data_path="...", channels=[0,1,2])

# 方式 4: Agent 代码中的机械辅助函数（agent_builder 按 MD 引用生成）
await self._call_tool_{tool_id}(**params)
```

`ToolRegistry.get()` 只返回注册表条目（dict），不携带执行能力；执行一律经 `ToolExecutor`。

---

## 十一、API 端点

`app/api/routes/tool_routes.py` 实际端点（节选核心项）：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/tool/generate-spec` | POST (SSE) | NL → MD 工具描述（流式，含思考过程） |
| `/api/tool/generate-code` | POST (SSE) | MD → 完整代码（流式，含思考过程） |
| `/api/tool/test` | POST (SSE) | 沙箱执行测试（单次执行附超时 `DEBUG_EXEC_TIMEOUT`） |
| `/api/tool/upload-test-file` | POST | 上传测试文件，返回临时路径 |
| `/api/tool/auto-debug` | POST (SSE) | 自动化调试（默认最多 10 轮，`DEBUG_MAX_ROUNDS` 可调） |
| `/api/tool/auto-debug/stop` | POST | 按 tool_id 可靠停止后台调试 |
| `/api/tool/register` | POST | 注册发布（不强制先过测试） |
| `/api/tool/workspace` | GET/POST | 工作区间工具列表 / 添加到工作区间 |
| `/api/tool/workspace/{tool_id}` | DELETE | 从工作区间移除 |
| `/api/tool/{id}/check-deps` | GET | 检测第三方依赖缺失（含 pip 包名映射） |
| `/api/tool/{id}/install-deps` | POST | 安装依赖到工具独立 `.venv`（uv 管理） |
| `/api/tool/list` `/repository` | GET | 工具列表 / 仓库视图（含标签统计） |
| `/api/tool/{id}` | GET/DELETE | 工具详情 / 删除工具 |
| `/api/tool/{id}/export` | GET | 导出 zip 工具包（附件下载，见 8.3） |
| `/api/tool/import` | POST | 导入 zip 工具包到私有根（冲突 409，见 8.3） |
| `/api/tool/{id}/execute` | POST | 调用工具（ToolExecutor，无超时） |
| `/api/tool/search/find` | GET | 搜索工具（`?q=&tags=`） |
| `/api/tool/{id}/modify-code` `/modify-and-debug` | POST | 对已注册工具改代码 / 改+调 |
| `/api/tool/{id}/spec-table` `/spec-outline` `/refine-spec-node` | POST | MD 规格的结构化编辑 |
| `/api/tool/{id}/save-code` `/save-spec` `/sync-spec-from-code` | POST | 保存代码/规格、从代码反向同步规格 |

---

## 十二、前端工具编辑器视图

```
┌──────────────────────────────────────────────────────────┐
│  🔧 工具编辑器                                     [× 关闭] │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Step 1: 描述需求 → [生成 MD 文档]                        │
│  ┌──────────────────────────────────────────────────────┐│
│  │ "我需要一个EEG带通滤波器..."                           ││
│  └──────────────────────────────────────────────────────┘│
│                                                          │
│  Step 2: MD 规范文档 (可编辑)          [→ 生成代码]       │
│  ┌──────────────────────────────────────────────────────┐│
│  │ ## 功能概述...                                       ││
│  └──────────────────────────────────────────────────────┘│
│                                                          │
│  Step 3: 代码 + 测试                                      │
│  ┌──────────────────┬───────────────────────────────────┐│
│  │  # 生成的代码      │  沙箱测试                         ││
│  │  def execute(...  │  [参数输入弹窗]                    ││
│  │      ...          │  [运行测试] [自动调试]             ││
│  │                   │  测试结果...                       ││
│  └──────────────────┴───────────────────────────────────┘│
│                                                          │
│  ┌── 🧠 自动调试日志（滚动） ──────────────────────────┐  │
│  │ [第1轮] 测试执行...                                  │  │
│  │ [第1轮] 输出: {"status":"failed","message":"..."}    │  │
│  │ [第1轮] 🤔 LLM分析: ...                              │  │
│  │ [第1轮] ✅ 代码已更新                                │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│                    [修改代码] [拒绝] [✅ 批准并注册发布]    │
└──────────────────────────────────────────────────────────┘
```

---

## 十三、实现文件清单

```
# 后端
core/resource/registry/tool_registry.py     # ToolRegistry
core/resource/discoverer/tool_discoverer.py # ToolDiscoverer
core/resource/builder/tool_builder.py       # ToolCodeBuilder（模板 + LLM 生成）
core/resource/builder/builder_base.py       # BaseBuilder
core/resource/tool_package.py               # zip 工具包导出/导入（归档格式 + 安全校验）
app/api/schemas/tool_schemas.py             # Tool Pydantic 模型
app/api/routes/tool_routes.py               # /api/tool/* 端点
resources/tools/registry.json              # 内置根：预置工具注册表
resources/tools/definitions/               # MD 规范文档
resources/tools/implementations/           # 工具代码（.venv / requirements.txt 在私有根按需创建）

# 前端
frontend/src/components/center-panel/ToolEditorView.tsx
frontend/src/components/center-panel/ToolDetailView.tsx
frontend/src/components/center-panel/RepositoryView.tsx
frontend/src/stores/tool-editor-store.ts
frontend/src/stores/workspace-tool-store.ts
frontend/src/services/api/tool.ts
```
