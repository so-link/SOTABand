# SOTABand 改进记录

> 记录时间：2026-08-29（首版，条目 1-22）／ 2026-08-30 增量（条目 23-26）
> 改动范围：27 个源码文件（后端 Python + 前端 TypeScript）
> 说明：本文只记录对 **SOTABand 平台本身**的改动；业务工具（如航拍图像质量评估器）的代码由平台生成，不在本记录范围内。

---

## 一、修复的 Bug

### 1. 流式对话崩溃：`IndexError: list index out of range`

**现象**：自动调试、交互式对话时终端报 `Task exception was never retrieved`，附带 `chunk.choices[0]` 越界堆栈。

**根因**：`core/llm/client.py` 的 `chat_stream()` 设了 `stream_options={"include_usage": True}`。该选项会让服务商在流的**最后一个 chunk 只返回 token 用量统计**，此时 `choices` 是空列表，代码直接取 `choices[0]` 就越界。

**修复**：加两处空值保护 —— `if not chunk.choices: continue` 跳过 usage-only chunk；`delta is not None` 防止 delta 为空的 role-only chunk。

**影响面**：所有流式对话功能（自动调试、交互式 Agent）。工具执行走非流式 `chat()`，不受此 bug 影响。

---

### 2. 数据删除会误删目录

**现象**：删除数据集接口存在误删风险。

**根因**：`shutil.rmtree(Path(data_path))` 未做路径解析。若 `data_path` 是相对路径（如 `datasets/xxx/`），会相对于**当前工作目录**解析，可能删错位置。

**修复**：统一改用 `resolve_data_path()` 解析为绝对路径；另加安全兜底，拒绝删除 `resources/data` 根目录自身。

---

### 3. 空 `data_path` 被误判为"本机有数据"

**现象**：某数据集 `data_path` 为空、`file_count=0`，却一直显示在数据空间里，点进去没有文件。

**根因**：`resolve_data_path("")` 把空串解析成 `resources/data` 根目录，而该目录恒非空，导致 `any(iterdir())` 为真 → 误判"本机有数据"。

**修复**：`is_present_locally()` 中加空路径短路，空值直接返回 `False`；并撤销了已被误认领的条目。

---

### 4. Agent 编辑器手工改代码后，点注册改动被静默丢弃

**现象**：在 Agent 编辑器 Step3 手工修改代码，点注册后提交的仍是旧代码。

**根因**：Step3 的 textarea 用**组件局部 state** 存储编辑内容，而 `registerAgent()` 读的是 store 里的 `generatedCode`，两者不通。

**修复**：改为直接读写 store 的 `generatedCode`，手工改动能正确提交。

---

### 5. 前端 `notifyEdit` 引用了错误的 store

**现象**：Agent 编辑器代码区改动不会触发保存状态更新。

**根因**：`AgentEditorView.tsx` 中误用了 `useToolEditorStore` 的 `notifyEdit`（应为 `useAgentEditorStore`）。

**修复**：改为引用正确的 store。

---

### 6. 新接口缺 `BaseModel` 导入

**现象**：`py_compile` 能通过，但运行时 `NameError: BaseModel is not defined`。

**根因**：新增 Pydantic 请求模型时忘了导入 `BaseModel`。`py_compile` 只检查语法、不检查运行时名称，因此逃过了首次验证。

**修复**：在 `tool_routes.py`、`agent_routes.py`、`data_routes.py` 补上导入。此后新增接口一律先确认导入再验证。

---

### 7. 生成代码的 Prompt 模板存在花括号转义错误（自引入）

**现象**：加了类型校验规则后，**工具生成功能完全不可用**，任何生成都抛 `ValueError: Invalid format specifier`。

**根因**：该 Prompt 模板后续会执行 `.format()`，而我在规则里写了含裸花括号的代码示例（`{"min": min(scores)}`），被当作 format 占位符解析。

**修复**：裸花括号全部转义为 `{{ }}`。

**教训**：在带 `.format()` 的模板中写代码示例，必须转义花括号。首次验证时模板会崩，因此这个 bug 在验证阶段就被发现并修复，未流入使用环节。

---

### 23. 工具空间全部显示"本地工具"（isUserGenerated 硬编码）

**现象**：工具空间里所有工具都被当作"本地工具"，两个用户生成的工具本应带 ⭐（用户本地工具）标记，却和 32 个内置示例无差别。

**根因**：三层叠加的硬编码——

1. `resource-store.ts` 初始加载时硬编码 `isUserGenerated: true`
2. 修复时只改了初始加载，**漏了同文件的 `fetchToolsFromApi()`** —— 两处同构映射代码，改一处漏一处
3. `ResourceBrowser.tsx` 工具空间列表从 localStorage 的 workspace 清单渲染，又一次硬编码 `isUserGenerated: true`

**修复**：

- 后端 `list_tools` 注入 `is_user_generated = bool(t.get("owner"))`（用户生成的工具注册时由 `user_context` 注入 owner，内置示例没有）
- 前端两处映射统一为 `Boolean(t.is_user_generated)`
- 工具空间渲染不再信 localStorage，改为查工具仓库（`toolResources`）取真实值

**教训**：同文件里的重复映射逻辑是漏改温床。实测确认方式：直接 `curl` 后端 API 看每个工具的 `owner` / `is_user_generated` 字段——数据对了前端还是错的，才能断定问题在前端。

---

### 24. 非法 DOM 嵌套：button 套 button（两轮修复）

**现象**：点击「资源空间」时 Console 报 `<button> cannot be a descendant of <button>`，会引发 hydration 异常。

**根因**：`ResourceBrowser` 有两类"整行可点 + 行内还有操作按钮"的结构：

| 位置 | 行内按钮 | 修复轮次 |
|---|---|---|
| section header（数据/工具/Agent 空间标题行） | 创建 +、工具仓库 | 第一轮 |
| 资源条目行（`paddingLeft:52px`） | 删除/移除 − | 第二轮（第一轮漏改） |

**修复**：两处统一改为 `div role="button" tabIndex={0}` + Enter/Space 键盘支持，保持可访问性等价。

**教训**：React 的报错栈会打印完整嵌套组件树——第二轮就是靠栈里的 `style={{paddingLeft:"52px"}}` 精确定位到条目行。修"同构重复结构"时必须全局搜一遍同类写法，不能只修报错的那一个。

---

## 二、安全加固

### 8. API Key 三条泄露路径（高危）

新增「工具可使用自己的 LLM 配置」能力后，排查出三条真实泄露路径：

| # | 路径 | 后果 | 严重性 |
|---|------|------|--------|
| 1 | **调试 Prompt** | key 随执行结果**发送给第三方模型服务商**，无法撤回 | 🔴 最高 |
| 2 | 调试日志 `logs/*.md` | key 明文落盘，长期留存 | 🟠 高 |
| 3 | 异常/返回信息 | key 随报错文本外传 | 🟡 中 |

路径 1 尤其隐蔽：原代码 `json.dumps(test_input)` 把工具输入**原样**拼进发给 LLM 的调试 Prompt，使用者填的 key 会直接出境。

**修复**：新建 `core/security/secrets.py`（项目原有的 `core/security/` 只有文档字符串，四个组件全是空壳），在三处统一脱敏：

- `scrub_text()` 文本级脱敏（覆盖 `sk-` / `sk-ant-` / `tp-` / `AIza` / `AKIA` / JWT / URL 查询参数 / 40+ 位随机串）
- `scrub_mapping()` 按参数名整值脱敏（`api_key` / `token` / `secret` / `password` 等）
- `redact_for_prompt()` 专用于发给 LLM 前的脱敏 + 长度限制

**验证**：三条路径均实测拦截，key 未泄露。

---

### 9. 生成代码的规则层面拦截

新增三条生成规则，从源头防止 LLM 产出带凭据的代码：

- **规则 16**：禁止硬编码任何 API key / token / password（含字面量、默认值、注释）
- **规则 17**：禁止把凭据打印到 stdout/stderr 或放入返回的 message/data
- **规则 18**：（原 16 顺延）

---

## 三、架构改进

### 10. 资源归属模型（核心改进）

**问题**：数据空间显示了 32 个老师的数据集，但本地没有数据 —— 能选中、能传给工具，运行时报"路径不存在"。

**根因**：不是数据脏，而是**架构缺了归属维度**。项目有 `user_id` 概念但从未启用，全部硬编码 `"default"`。

**方案**：新增 `core/user_context.py`，区分两个正交概念：

```
owner      = 谁创建的  → 决定谁能改/删
visibility = private/public → 决定谁能看
```

**归属模型**：

| 资源 | 模型 | 理由 |
|---|---|---|
| **数据集** | 私有 | 数据在本机，他人访问不到 —— 是物理约束，不是权限偏好 |
| **工具** | 共享 | 代码资产，复制成本≈0 |
| **Agent** | 共享 | 同上 |

**关键推论**：共享资源访问私有数据时，必须在 **API 层**做归属过滤 —— 否则界面隔离形同虚设，任何共享工具调用 API 即可绕过。因此 `api_data.py` 强制按 owner 过滤，且**不接受调用方传入 owner**（防伪造）。

**实测**：

```
他人数据集 33 条 → 被拒绝 33/33 ✅ 共享工具无法绕过
自己的数据集 2 条 → 正常访问 2/2 ✅ 共享工具照常能用
```

**迁移兼容**：历史条目无 owner 时，用"本机是否有数据"的启发式判定，保证自己的数据不会平白消失；启动时自动认领并补全 `owner` / `visibility` / `storage` 字段。

---

### 11. 工具生成质量提升规则

新增三条规则，让平台生成的代码自带防御能力：

| 规则 | 内容 |
|---|---|
| **13** | 永不信任外部数据类型。聚合前必须 `isinstance` 校验 + `float()` 转换 + 过滤 NaN/Inf；明确指出"只按 error 标志过滤是不够的"，并给出 WRONG/RIGHT 对照代码 |
| **14** | LLM JSON 输出端到端防御：去围栏 → try/except 解析 → 数值字段强转，失败则判该条失败而非存原值 |
| **15** | 批处理必须逐项容错，仅当全部失败才返回 `failed` |
| **5（增强）** | 异常必须带 `traceback.format_exc()`，否则自动调试只能看到一句话，无法定位 |

**背景**：一个 `min(dim_scores)` 混入字符串导致崩溃的 bug，自动调试两轮都没修对 —— 它把变量改名、加注释"仅使用成功的数据"，但逻辑完全等价（幻觉式修复）。根因是平台没给这条规则，LLM 每次都从零猜。

**效果**：重新生成后代码自动包含 `float()` 强转 + try/except + NaN/Inf 过滤，用触发数据实测不再崩溃。

---

### 25. 工具空间清单：浏览器状态 → 引擎状态（架构）

**问题**：工具空间"已加载哪些工具"只存 localStorage（`sotaband_workspace_tools`）。换浏览器、开无痕窗口、清缓存后清单全空。无痕窗口实测：主窗口 1 个、无痕窗口 0 个 —— 同一台机器、同一个后端，工作空间却不一致。且发现主窗口旧清单只剩 1 个工具（`custom-model-caller` 早已丢失），localStorage 根本不可靠。

**方案**：状态源迁移到后端 `storage/workspace_tools.json`，与 `user.json` 同目录（版本控制之外）：

- 新增 `GET / POST / DELETE /api/tool/workspace` 三个端点（`WorkspaceToolItem` 校验 + 去重）
- 前端 `workspace-tool-store` 新增 `fetchFromApi()`：启动时以后端为准，localStorage 降级为离线缓存
- `addTool` / `removeTool` 双写（本地即时生效 + 后端同步）
- **一次性迁移**：后端清单为空且本地 localStorage 有旧数据时，自动上传对齐 —— 升级不丢已有清单

**验证**：主窗口与无痕窗口看到同一份清单（2 个用户工具，均带 ⭐）；清缓存、换浏览器不再丢工作空间。

---

### 26. 排障方法沉淀：「改了没生效」的服务端验证法

本次排查 23-25 时形成的一套可复用流程，绕开"浏览器到底加载了什么"的不确定性：

```
1. 验证源码     → read_file 确认修复在源文件里
2. 验证编译产物 → curl http://localhost:5173/src/<模块路径>
                  Vite dev 按需编译，返回的就是浏览器将执行的代码
3. 验证后端数据 → curl <API 端点> 看真实返回字段
4. 验证运行时   → 无痕窗口（独立缓存 + 无 localStorage）
```

每一步排除一个环节，哪步断了问题就在哪。本次靠它把问题精确定位到「Safari 缓存回放旧 JS 模块」+「localStorage 架构缺陷」两个独立原因——Safari 对 localhost 开发服务器的缓存回放是知名顽疾，普通刷新不等于重新验证。

---

## 四、体验优化

### 12. 编辑器：从"审阅"步进入，不再推倒重来

**问题**：点已发布工具的「编辑」，会回到第 1 步并清空现有 MD 与代码。

**修复**：新增 `prefillFromTool()` / `prefillFromAgent()`，带齐现有成果，直接从 **Step 2（审阅）** 进入。

### 13. 编辑器：代码可手工微调

**问题**：生成的代码框是只读预览，想微调无处下手。

**修复**：Step3 代码区支持「手工微调」切换为可编辑 textarea，改动直接写回 store。

### 14. 编辑器：手工保存 + Ctrl/Cmd+S

**问题**：无保存入口；曾考虑自动保存，但改动会直接影响已发布工具，风险高。

**修复**：改为**手动保存**（保存按钮 + `Ctrl/Cmd+S`），配状态指示器：`无改动` / `未保存` / `保存中` / `已保存` / `保存失败+重试`。关闭时若有未保存改动会弹确认。

*注：曾短暂实现自动保存，经讨论后按"改动应显式确认"的原则改为手动。*

### 15. 编辑器：关闭按钮回到"来处"

**问题**：从工具详情点「编辑」进去，关闭后跳到对话，上下文丢失。

**修复**：按来处回退 —— 编辑已有资源 → 回该资源详情页；新建 → 回对话。注册完成后同理（且仅当选中项正是刚注册的资源时才跳详情页，避免跳错）。

### 16. 代码编辑：Tab 缩进

**问题**：原生 textarea 中 Tab 会跳走焦点，打不出缩进。

**修复**：新建 `use-tab-indent.ts`，对齐 VSCode 行为：光标处插入 / 多选整块缩进 / Shift+Tab 反缩进。统一用空格（Python 混用 tab/space 会 `TabError`）。

**关键修正**：初版用「直接改 value + 派发 input 事件」实现，会**清空浏览器 undo 栈，导致 Ctrl+Z 失效**。改用 `document.execCommand('insertText')` 保留原生撤销能力，并保留降级路径。

### 17. 左侧资源树：未保存标记

新增 `use-unsaved-marks.ts`，在资源树上打**橙色圆点**，即使切到别的视图也能看到"这里还有改动没保存"（等同 VSCode 标签页圆点）。另有**红色圆点**标记数据不可用的数据集。

### 18. 后端接口补齐

| 接口 | 用途 |
|---|---|
| `POST /api/tool/{id}/save-code` | 保存工具代码（原有） |
| `POST /api/tool/{id}/save-spec` | **新增**：保存工具 MD 文档 |
| `POST /api/tool/{id}/sync-spec-from-code` | **新增**：代码 → 文档反向同步 |
| `POST /api/agent/{id}/save-code` | **新增**：保存 Agent 代码 |
| `POST /api/agent/{id}/save-spec` | **新增**：保存 Agent MD 文档 |
| `POST /api/agent/{id}/sync-spec-from-code` | **新增**：Agent 代码 → 文档反向同步 |
| `GET /api/data/list?available_only=` | **增强**：可用性过滤 + 归属隔离 |
| `POST /api/data/claim-local` | **新增**：认领本机历史数据集 |

---

## 五、LLM 能力增强

### 19. 工具可使用自己的 LLM 配置

**场景**：项目 `.env` 用 `deepseek-v4-flash`（纯文本），某个工具需要多模态能力，必须换模型 —— 但改全局 `.env` 会影响所有工具。

**方案**：新增 `api-llm-chat-with-config`，单次调用时覆盖服务商/模型/Key，不影响全局。

### 20. 服务商目录 + base_url 自动解析

**问题**：让使用者填 `base_url` 不合理 —— 那是技术细节，多数人不知道各家端点该填什么。

**方案**：新建 `core/llm/providers.py`，维护 9 家服务商的端点/能力/模型/文档链接。解析顺序：

```
1. 显式 base_url → 自定义模式
2. provider 查目录 → 自动解析
3. 按 model 名推断（gpt-4o → openai）
4. 都失败 → 报错并列出全部 9 家
```

**使用者只需填三项：供应商 + 模型 + Key。** 附带效果：形成天然白名单，避免请求发往未知地址。

### 21. 模型列表动态拉取

**问题**：静态目录会过时（厂商上下线模型频繁）。

**实测佐证**：用真实 Key 拉取 MiMo 得到 6 个模型，其中 **4 个（`asr` / `tts` / `tts-voiceclone` / `tts-voicedesign`）不在静态目录中**。

**方案**：职责拆分 ——

| 信息 | 变化 | 来源 |
|---|---|---|
| **模型列表** | 快 | **实时拉取 `/v1/models`** |
| 端点 / 能力标注 / 文档链接 | 慢 | 静态目录 |

静态目录中的模型名降级为"推荐示例"，不做权威列表。

### 22. 一键配置验证

新增 `api-llm-test-config`：填完配置后一次查完连通性、Key 有效性、模型是否存在。

**模型名填错会纠错**（编辑距离匹配）：

```
输入 "mimo-v2.5-typo"
→ model_valid = False
→ suggestion  = mimo-v2.5-pro
```

**自动提醒配置陷阱**：识别为推理模型时提示"max_tokens 建议 >= 1500"（这是实测踩过的坑）。

新增 API 一览：`api-llm-chat-with-config`、`api-llm-test-config`、`api-llm-list-providers`。

---

## 六、当前状态

```
数据集：私有，35 条注册 → 我的 2 条 ✅
工具：  共享，32 个
Agent： 共享，6 个
系统 API：17 个
服务商目录：9 家（6 家支持多模态）
```

验证：全项目编译通过、前端 `tsc --noEmit` 零错误、ESLint 0 错误。

---

## 七、待办：必须修复

### P0-1. 自动调试无法识别偶发 bug

**现象**：某 bug 依赖随机条件触发（如 VLM 偶发返回非数值）。自动调试重跑时若恰好未触发，就判定"修复成功" —— 实际上代码一行没改。

**已部分缓解**：规则 13-15 让生成的代码更健壮；规则 5 让异常带 traceback。

**仍需**：
- 自动调试对"代码未变更但测试通过"的情况应给出警告，而非报成功
- 关键路径可考虑重复执行 2~3 次，或显式提示"若失败由随机性导致，请检查边界处理"

### P0-2. 归属隔离未覆盖部门资源

当前仅**数据集**做了归属隔离。工具与 Agent 是共享设计（有意为之），但：

- 二者均**未记录 owner**，无法追溯创建者
- 未来若需"私有工具/私有 Agent"或"公共池自选"，需要 `visibility` 开关与界面入口

> 字段已预留（`owner` + `visibility` + 判定函数 `is_visible()`），接上界面即可用。

### P0-3. 数据集列表每次都要校验 35 条路径

`check_availability()` 对每个条目做 `rglob` 统计。当前 35 条无感，数据量增长后会有 IO 开销。

**建议**：给"本机可用"结果加缓存（按路径 + 修改时间失效）。

---

## 八、待办：可优化

### P1-1. 数据集编辑器 / API 编辑器未对齐

工具与 Agent 编辑器已具备：手工微调、Ctrl+S、关闭回退、Tab 缩进。

**数据集编辑器、API 编辑器大概率仍是"只能看不能改"**，建议统一扫一遍并补齐。

### P1-2. 关闭确认用浏览器原生弹窗

当前 `window.confirm` 样式割裂，且只有两选项。建议换成项目内 Modal，提供三选一：**保存并关闭 / 不保存关闭 / 取消**。

### P1-3. 缺少版本历史

手动保存已落地，但没有历史版本。改崩了无法回退（跨会话后 Ctrl+Z 无效）。

**方案**：每次保存留一份快照到 `_history/`，保留最近 20 版，支持预览与一键恢复。

### P1-4. 自定义 base_url 无白名单

`api-llm-chat-with-config` 允许填任意 `base_url`，工具可把数据发到任何地址。

当前单机使用无风险；多人共用后端时建议加白名单（只允许已登记的服务商地址）。

### P1-5. 静态服务商目录会过时

模型列表已改为动态拉取（P0 已解）。但**端点、文档链接、能力标注**仍是硬编码，厂商改域名或调整能力后需要人工更新。

**建议**：
- 端点失效时给出明确提示（而非通用网络错误）
- 定期检查目录中的 `docs_url` 可达性

### P1-6. 密钥脱敏存在误判

`scrub_text()` 对 40+ 位无空格随机串一律脱敏，可能误伤正常长文本（如 base64 图片数据、长哈希）。

当前"宁可多脱敏"，安全性优先；但会削弱日志可读性。建议后续对已知安全字段（如 base64 图片）加白名单豁免。

### P1-7. 工具编辑器无「已修改」的全局提示

资源树圆点已实现（P2 项 17），但编辑器内切换 Step 时，`dirty` 状态依赖 store 单一字段，尚未做"MD 脏 / 代码脏"的分别标记与分别保存。

---

## 九、附：本次改动文件清单（27 个）

**后端（13）**
```
app/api/routes/agent_routes.py       + save-spec / sync-spec-from-code
app/api/routes/data_routes.py        + 归属隔离 / save-spec / 删除安全
app/api/routes/tool_routes.py        + save-code / save-spec / sync-spec-from-code
core/api/implementations/api_data.py + 归属过滤重写 / 防绕过
core/api/implementations/api_llm.py  + 3 个新 API
core/llm/client.py                   + aclose() / chat_stream 空值保护
core/llm/providers.py                【新建】服务商目录 + 动态拉取
core/resource/builder/tool_builder.py + 生成规则 / 日志脱敏 / traceback
core/resource/registry/agent_registry.py + 归属字段
core/resource/registry/data_registry.py  + 归属核心逻辑
core/resource/registry/tool_registry.py  + 归属字段
core/security/secrets.py             【新建】密钥脱敏
core/user_context.py                 【新建】用户上下文 + 归属模型
```

**前端（14）**
```
components/center-panel/AgentDetailView.tsx   编辑入口 / 代码微调 / 保存
components/center-panel/AgentEditorView.tsx   编辑模式 / 保存 / 关闭回退
components/center-panel/ToolDetailView.tsx    编辑改道 / 代码微调
components/center-panel/ToolEditorView.tsx    编辑模式 / 保存 / Ctrl+S / Tab
components/left-panel/ResourceBrowser.tsx     未保存圆点 / 数据不可用标记
hooks/use-save-shortcut.ts                    【新建】Ctrl/Cmd+S
hooks/use-tab-indent.ts                       【新建】Tab 缩进（保留 undo）
hooks/use-unsaved-marks.ts                    【新建】未保存状态汇总
services/api/agent.ts                         + get/saveSpec/saveCode/sync
services/api/data.ts                          + 可用性过滤
services/api/tool.ts                          + saveSpec/saveCode/sync
stores/agent-editor-store.ts                  + 编辑模式 / 保存状态
stores/resource-store.ts                      + 数据集字段映射
stores/tool-editor-store.ts                   + 编辑模式 / 保存状态
types/resources.ts                            + DataResource 可用性字段
```

---

## 十、附：2026-08-30 增量改动文件清单（3 个）

```
app/api/routes/tool_routes.py                + workspace 3 端点 / list 注入 is_user_generated
frontend/src/stores/workspace-tool-store.ts  + fetchFromApi / 后端为准 / 一次性迁移
frontend/src/components/left-panel/ResourceBrowser.tsx + 条目行 DOM 嵌套修复 / isUserGenerated 查仓库
```

新增数据文件：`storage/workspace_tools.json`（工具空间清单，版本控制之外）
