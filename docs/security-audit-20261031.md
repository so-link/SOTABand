# SOTABand 平台安全审计报告

- 审计日期：2026-10-31
- 审计对象：本机运行的 SOTABand（后端 127.0.0.1:8201，前端 127.0.0.1:8200）
- 审计方式：静态代码审计 + 非破坏性动态验证（仅写入 /tmp 标记文件，已清理）
- 审计范围：FastAPI 路由、Agent/Tool 生成与执行链路、资源注册、文件与配置接口、认证与部署默认值
- 代码未做任何修改

---

## 一、结论摘要

**核心结论：SOTABand 可在其部署服务器上被任意执行代码，（在默认部署配置下）且无需登录。**

本系统把「LLM 生成的工具/Agent 代码」当作可执行代码运行，唯一的防线是一个基于 AST 的**黑名单静态扫描**。该扫描可被一行普通 Python 绕过；执行侧是**同用户、全文件系统、全网络、继承全部环境变量的普通子进程**，没有任何 OS 级隔离。再加上所有接口都没有鉴权，形成一个完整的未认证 RCE 链路。

同时确认了与你观察到的「自动调试多轮报错」直接相关的平台缺陷：

1. 自动调试把**基础设施写入异常**（PermissionError）误判为「Agent 代码错误」，同一错误连续 10 轮下发给 LLM，LLM 被误导反复删改正常代码；
2. 调试目录名固定、无并发隔离，多标签页/重复点击会互相覆盖；
3. **工具参数序列化用 JSON 字面量拼进 Python 源码**，布尔/None 参数必然触发 `NameError`（影响所有工具与新生成的 Agent 工具调用）；
4. **`BaseApi.call()` 在事件循环内调用会自锁**，任何新 Agent 调用系统 API 都会卡 60 秒后失败；
5. 工具执行接口默认无超时，可被无限循环工具拖死服务。

### 风险等级汇总

| 编号 | 发现 | 等级 | 状态 |
|---|---|---|---|
| C1 | 全部接口无鉴权，匿名请求回退为实例身份 | 严重 | 已动态确认 |
| C2 | 未认证的任意代码执行（`/api/tool/test` 等） | 严重 | 已动态确认（root 权限 `id`） |
| C3 | 静态安全扫描可被平凡绕过（黑名单） | 严重 | 已动态确认 |
| C4 | 「沙箱」并非沙箱：同用户子进程 + 继承密钥 + 无 FS/网络限制 | 严重 | 代码确认 |
| C5 | 任意文件读取 / 删除 / 目录列举（含 `.env`） | 严重 | 已动态确认 |
| C6 | 上传接口 `subdir` 路径穿越 → 任意文件写入 | 严重 | 已动态确认 |
| C7 | Agent/Tool/Dataset 的 id 未校验 → 路径穿越写实现文件 | 严重 | 代码确认 |
| C8 | 默认监听 `0.0.0.0` + 无鉴权 → 服务器部署可远程利用 | 严重 | 配置确认 |
| C9 | 浏览器 CSRF / DNS rebinding 可驱动本机接口 | 高 | 已动态确认（伪造 Origin 成功） |
| C10 | 任意依赖安装（`install-deps`）→ 供应链 RCE | 高 | 代码确认 |
| C11 | web-service Agent 可绑定 `0.0.0.0`，自身 API 无鉴权 | 高 | 代码确认 |
| C12 | 实例无用户时，任何人可注册成为 admin | 高 | 代码确认 |
| C13 | 配置接口可改 LLM Key/模型/调试参数 | 高 | 代码确认 |
| D1 | 自动调试 IO 异常误判为代码错误，误导 LLM 破坏性改写 | 高 | 日志确认 |
| D2 | 调试目录固定名、并发冲突、异常路径无 `[debug]` 归因 | 中 | 代码确认 |
| D3 | 工具参数 bool/null 序列化为 Python 非法字面量 | 高 | 已复现 |
| D4 | `BaseApi.call` 事件循环内自锁（60s 超时） | 高 | 已复现（模式验证） |
| D5 | 工具执行 / Agent 执行缺少超时与回收 | 中 | 代码确认 |
| D6 | Agent 内同步工具调用阻塞事件循环 | 中 | 代码确认 |
| D7 | 前端 SSE 中止产生 `net::ERR_ABORTED` 控制台噪声 | 低 | 历史观察，未复现本轮 |
| D8 | 调试日志把「语法错误」归类为「安全审查未通过」；轮数统计偏差 | 低 | 日志确认 |

---

## 二、安全发现（详细）

### C1 全部接口无鉴权，匿名即实例身份（严重）

- 位置：[app/main.py](../app/main.py#L69-L93)
- 事实：中间件只处理 `Authorization: Bearer` 的有效性；**没有令牌时直接 `call_next`，不拒绝**。所有路由都没有 `Depends(...)` 鉴权依赖。
- 影响：本机任何进程、任何网页（见 C9）都可读写全部资源、调用代码执行接口。
- 证据：不带任何令牌访问 `/api/agent/list` 返回 200；`/api/tool/test` 成功执行代码。
- 相关：无用户时 `/api/auth/register` 允许匿名注册且首个用户为 admin（[auth_routes.py](../app/api/routes/auth_routes.py#L40-L50)），当前实例 `resources-private/users/` 不存在，即**当前即可被任何人注册为管理员**。

### C2 未认证的任意代码执行（严重，已动态验证）

- 入口（至少）：
  - `POST /api/tool/test`：`code` 表单字段直接交给执行器（[tool_routes.py](../app/api/routes/tool_routes.py#L255-L327)）
  - `POST /api/tool/auto-debug`：`code` → `ToolExecutor`（[tool_routes.py](../app/api/routes/tool_routes.py#L418)）
  - `POST /api/agent/auto-debug`：`code` 表单字段 → entrypoint 子进程执行（[agent_routes.py](../app/api/routes/agent_routes.py#L386-L454)，执行见 [agent_builder.py](../core/resource/builder/agent_builder.py#L742-L793)）
  - `POST /api/tool/{tool_id}/execute`：执行已注册工具（[tool_routes.py](../app/api/routes/tool_routes.py#L1962-L1973)）
  - `POST /api/tool/register`、`/api/agent/register`：落盘后可随时执行
- 实测（非破坏性）：以 `from os import system` 实现的工具代码，静态扫描放行，POST `/api/tool/test` 后服务器执行命令，输出 `uid=0(root) gid=0(root)`。**即部署用户为 root 时直接是 root RCE。**
- 根因：见 C3/C4。

### C3 静态安全扫描是黑名单，可被平凡绕过（严重，已动态验证）

- 位置：[core/security/code_scan.py](../core/security/code_scan.py#L28-L52)、[检查逻辑](../core/security/code_scan.py#L201-L241)
- 现规则只拦：`os.system/os.popen/...`、`shutil.rmtree`、`subprocess.getoutput`、`eval/exec/compile/__import__`、`import ctypes/socket/pty/resource`。
- 已确认绕过样例（全部通过扫描）：
  - `from os import system` → `system("...")`（`system` 是单名调用，不在黑名单）
  - `from shutil import rmtree` → `rmtree("...")`（单名，只拦 `rmdir/remove/unlink/import_module`）
  - `import subprocess` → `subprocess.run(["sh","-c","..."])`（未使用 `shell=True`，仅 warning）
  - `getattr(os, "system")(...)`、`builtins.exec(...)`
  - `open("/root/.ssh/authorized_keys","w")`（`open` 仅 warning）
  - `os.environ` 读取 + `requests` 外发（仅 warning）
- 设计注释明确承认：`subprocess/open/network` 属于「平台信任模型允许」——这与「不可信代码」是相互矛盾的。

### C4 所谓「沙箱」不是沙箱（严重）

- 工具执行：`asyncio.create_subprocess_exec(python_exe, tmp_path)`，**未传 `env=`**，完整继承后端环境（[tool_executor.py](../core/executor/tool_executor.py#L200-L205)）。
- Agent 试运行：同样 `create_subprocess_exec(..., cwd=PROJECT_ROOT)` 无 `env=`（[agent_builder.py](../core/resource/builder/agent_builder.py#L784-L793)）。
- 后果：
  - 继承 `LLM_API_KEY`、`SOTABAND_AUTH_SECRET` 等全部密钥，恶意代码可直接外发；
  - 与后端同 UID，可读写整个项目、私有资源、用户主目录、`/root/.ssh`、系统服务；
  - 可任意联网（无 egress 限制），可做内网横向/SSRF；
  - 无 CPU/内存/进程数限制，可 DoS；
  - 反过来，`/api/file/download` 可读取 `.env`（见 C5），无需代码执行即可窃取主 Key。
- 结论：UI/日志中的「沙箱试运行」「静态安全审查」会给人虚假的安全感。

### C5 任意文件读取 / 删除 / 目录列举（严重，已动态验证）

| 接口 | 位置 | 实测 |
|---|---|---|
| `GET /api/file/download?path=` | [file_routes.py](../app/api/routes/file_routes.py#L200-L226) | 200 读取 `/etc/hostname`；**200 读取 `/root/SOTABand/.env`（7206 字节，含 LLM_API_KEY 等 15 处敏感项）** |
| `POST /api/file/delete` | [file_routes.py](../app/api/routes/file_routes.py#L159-L184) | 删除 `/tmp` 下自建文件成功；可删项目文件、`.env`、其他用户文件 |
| `GET /api/file/scan-directory` | [file_routes.py](../app/api/routes/file_routes.py#L266-L324) | `/etc` 全树列举成功 |
| `GET /api/file/image`、`/audio`、`/browse-directory` | [file_routes.py](../app/api/routes/file_routes.py#L187-L197) | 同类任意路径读取 |

`/api/data/scan-directory`（[data_routes.py](../app/api/routes/data_routes.py#L111-L117)）与数据集注册（[data_routes.py](../app/api/routes/data_routes.py#L208-L249)）也接受任意绝对路径。

### C6 上传路径穿越 → 任意文件写入（严重，已动态验证）

- 位置：[file_routes.py](../app/api/routes/file_routes.py#L119-L156)
- 问题：只清洗文件名（`Path(name).name`），`subdir` 直接拼接：`dest_dir = UPLOAD_ROOT / dir_name`（第 132-134 行）。
- 实测：`subdir=../../../../tmp/sotaband-trav` 成功把文件写到 `/tmp/sotaband-trav/trav.txt`，返回值中也回显了穿越后的路径。
- 升级利用：可写 `.env`（改 LLM 网关/Key）、写 `resources/agents/implementations/*/agent.py`（持久化后门）、写用户 `~/.bashrc`、写 cron/systemd 等。

### C7 资源 ID 未校验 → 路径穿越写入实现文件（严重）

- Agent 注册：`agent_id` 直接取自 MD 的 `id:` 行，未做格式校验（[agent_routes.py](../app/api/routes/agent_routes.py#L518-L524)），随后拼成路径写 `agent.py/spec.md/config.yaml`（[agent_routes.py](../app/api/routes/agent_routes.py#L546-L555)）；注册表本身也不校验（[agent_registry.py](../core/resource/registry/agent_registry.py#L99-L108)）。
- Tool 注册：同样未校验（[tool_routes.py](../app/api/routes/tool_routes.py#L500-L544)，[tool_registry.py](../core/resource/registry/tool_registry.py#L135-L145)）。`TOOL_ID_RE` 只在安装包导入路径使用（[tool_package.py](../core/resource/tool_package.py#L35)）。
- 自动调试：`debug_id = f"_debug-{agent_id}"` 直接把表单值拼进目录（[agent_builder.py](../core/resource/builder/agent_builder.py#L742-L759)）；日志文件名也直接拼 `agent_id`。
- 影响：`id: ../../../../<目标目录>` 即可把攻击者代码写到任意目录的固定文件名（`agent.py`/`spec.md`/`config.yaml`），随后通过 `/start` 启动，形成**持久化执行**。目录穿越 + 固定文件名足够覆盖内置 Agent 实现。

### C8 默认 0.0.0.0 + 无鉴权（严重）

- [settings.py](../config/settings.py#L402) 默认 `api_host=0.0.0.0`；[serve.py](../scripts/serve.py#L258) 同样默认 `0.0.0.0:8201`。
- 当前实例 `.env` 改成了 `127.0.0.1`，所以本机监听是回环；但按代码默认值/文档部署到服务器，即等于把 C2/C5/C6 暴露给整个网络。
- 无 TLS、无鉴权、无速率限制。

### C9 CSRF / 浏览器驱动攻击（高，已动态确认）

- CORS 默认允许来源为空（[settings.py](../config/settings.py#L409-L411)），但 **CORS 只限制「读取响应」，不阻止请求送达**。
- `multipart/form-data`、`application/x-www-form-urlencoded` 属于「简单请求」，不触发预检。任意网页都可向 `http://127.0.0.1:8201/api/tool/test` 或 `/api/agent/auto-debug` 发 POST 触达执行接口。
- 实测伪造 `Origin: https://evil.example.com` 的 POST 仍成功执行。
- 结合 DNS rebinding，攻击者还能读取响应（任意文件读取/密钥窃取）。当前 127.0.0.1 绑定并不能防住「浏览器内」攻击。

### C10 任意依赖安装（高）

- `POST /api/tool/{tool_id}/install-deps`（[tool_routes.py](../app/api/routes/tool_routes.py#L406)）与自动调试里的依赖安装会对 LLM/请求给出的包名执行 `uv pip install`（[tool_builder.py](../core/resource/builder/tool_builder.py#L781-L909)）。
- 攻击者先注册一个工具（可绕过扫描），再安装任意包（含自建恶意包/依赖混淆），即可在安装期执行任意代码。安装发生在工具独立 venv，但仍是同一用户、同一主机。

### C11 web-service Agent 可绑定 0.0.0.0 且无鉴权（高）

- `server` 配置块的 `host` 原样使用（[entrypoint.py](../core/agent/entrypoint.py#L192-L203)、[web_server.py](../core/agent/web_server.py#L25-L40)）。
- 生成的 HTTP 服务没有任何鉴权/限流/大小限制（[web_server.py](../core/agent/web_server.py#L54-L95)），且 `content-length` 无上限。若 Agent 的 MD 写 `host: 0.0.0.0`，即把「LLM 生成的服务端」直接暴露到网络。

### C12 首个注册用户即 admin（高）

- [auth_routes.py](../app/api/routes/auth_routes.py#L40-L50)：实例无用户时任何人（含匿名）可注册，且 `register_user` 首用户为 admin。当前实例无用户，等于 admin 可被匿名抢占（虽然大部分接口本来就不鉴权）。

### C13 配置接口可被匿名篡改（高）

- `POST /api/config/llm`（[config_routes.py](../app/api/routes/config_routes.py#L173-L217)）可改 `LLM_PROVIDER/LLM_MODEL/LLM_API_KEY/LLM_TIMEOUT/DEBUG_EXEC_TIMEOUT/DEBUG_MAX_ROUNDS` 并写回 `.env`。
- 结合 C6，可把 `LLM_BASE_URL` 改成攻击者网关，使所有对话/生成内容（含脱敏后的报错与输入）流向攻击者；也可把调试轮数/超时调到极端值造成资源耗尽。

### 其他（中/低）

- **密钥与令牌**：`core/auth` 的签名密钥位于私有根 `users/secret.key`，可被 C5 读取；`SOTABAND_AUTH_SECRET` 也在子进程环境里（C4）。
- **SSRF**：工具联网属于 warning；`api/implementations` 中动态 `importlib.import_module(module_path)`（[core/api/base.py](../core/api/base.py#L19-L38)）允许通过改写 `core/api/registry.json`（C6 可写）加载任意模块。
- **敏感信息进日志**：自动调试会把 stdout/stderr、测试输入写入 `logs/*.md` 并发给第三方 LLM，现有 `redact_for_prompt` 只覆盖已知模式。

---

## 三、功能缺陷（与「调试多轮报错 / 控制台错误」直接相关）

### D1 自动调试把基础设施错误误判为代码错误（高）

- 位置：[agent_builder.py](../core/resource/builder/agent_builder.py#L756-L759)（写文件在 `try` 之外）、[异常捕获](../core/resource/builder/agent_builder.py#L1088-L1093)
- 现象（本次日志 `logs/agent_etf-quote-insight-agent_20261031_223420.md`）：
  - 10 轮全部失败，每轮报 `试运行内部异常: PermissionError: ... '/root/resources-private/.../_debug-etf-quote-insight-agent'`
  - 该异常发生在「建目录 → 写 agent.py/spec.md」阶段（未进入 try），错误路径不走 `_fail()`，因此**没有 `[debug]` 前缀**，也没有归类为「环境错误」；
  - LLM 把它当成代码缺陷，反复删掉 `_log_step` 等正常代码，10 轮全部无效，还造成代码退化。
- 本次根因说明：审计期间后端进程继承了 Trae 的沙箱文件策略，私有目录写入被拒；这是**本次运行环境**问题，不是 Agent 代码问题。**但平台的处理方式（不区分环境错误/代码错误、不做同错短路）是必须修的平台缺陷**，任何用户遇到目录只读、磁盘满、权限不足都会复现同样的 10 轮空转。

### D2 调试目录固定名、并发与清理问题（中）

- `_debug-{agent_id}` 固定目录（[agent_builder.py](../core/resource/builder/agent_builder.py#L742)），同一 Agent 的两个调试会话共享目录；`finally` 中 `shutil.rmtree(impl_dir)`（第 939 行）可能删掉另一个会话正在使用的文件。
- 若建目录在写文件前失败，`finally` 仍会尝试删除；如果中途异常，没有区分「本次会话独占」与「他人目录」。
- 建议：`_debug-{agent_id}-{uuid}`，并校验 agent_id 合法性（同时修复 C7）。

### D3 工具参数序列化非法（高，已复现）

- 位置：[tool_executor.py](../core/executor/tool_executor.py#L129)（`execute(**{json.dumps(params)})`），Agent 工具调用 helper 同样：[agent_builder.py](../core/resource/builder/agent_builder.py#L102)
- JSON 的 `true/false/null` 不是 Python 字面量。实测：
  - 参数 `{"flag": true, "n": null}` → `NameError: name 'true' is not defined`
- 影响：**所有带布尔/空值参数的工具与新生成 Agent 的工具调用必然失败**；这类参数在 LLM 生成工具里很常见。内置工具的「工具调用」自带 helper 用的是 `repr()`，所以行为不一致，问题被掩盖。
- 建议：用 `repr(params)`（或 `ast.literal_eval` 反向解析），不要用 `json.dumps` 拼源码。

### D4 `BaseApi.call` 在事件循环内自锁（高，已复现模式）

- 位置：[core/api/base.py](../core/api/base.py#L40-L54)
- 在运行中的事件循环里调用时：`asyncio.run_coroutine_threadsafe(...)` 把协程排到当前循环，随后 `future.result(timeout=60)` **阻塞了同一个循环线程** → 协程永远得不到执行，60 秒后超时。
- 实测最小复现：同一循环内 `run_coroutine_threadsafe` + `future.result(timeout=3)` → 必然超时。
- 影响：任何异步 Agent 的 `execute` 中同步调用 `_call_api_*()`（模板生成的 helper 就是同步）都会卡 60s 后失败。当前内置 Agent 恰好不使用 API（所以此前未暴露），但新建 Agent 的 MD 只要引用【API名】就会踩中。
- 建议：agent 侧改为 `await` 异步调用，或 `await asyncio.to_thread(api.call, **kwargs)`；`BaseApi.call` 在检测到当前循环时直接抛出明确错误，禁止同步自调用。

### D5 执行缺少超时与进程回收（中）

- `POST /api/tool/{tool_id}/execute` 使用 `timeout=None`（[tool_routes.py](../app/api/routes/tool_routes.py#L1968-L1972)）；`ToolExecutor.sandbox_execute` 也可无超时（[tool_builder.py](../core/resource/builder/tool_builder.py#L668-L703)）。
- Agent `/{id}/execute` SSE 循环无总超时；客户端断开只取消读循环，是否终止子进程/任务取决于具体实现。
- 影响：一个 `while True` 工具即可长期占用 CPU/内存并堆积进程（DoS），配合未认证接口可远程触发。

### D6 Agent 内同步工具调用阻塞事件循环（中）

- 模板生成的 `_call_tool_*` 是同步方法，内部 `subprocess.run(...)`（[agent_builder.py](../core/resource/builder/agent_builder.py#L86-L120)）。
- 在 async `execute` 中直接调用会**阻塞整个 Agent 事件循环**，导致流式事件停顿、心跳超时、多个请求串行化。
- 建议：`await asyncio.to_thread(self._call_tool_xxx, **params)`，或提供异步 helper。

### D7 前端 SSE `net::ERR_ABORTED`（低）

- 观察：上一轮浏览器控制台在 `POST /api/agent/generate-code` 完成后出现 `net::ERR_ABORTED`。
- 代码侧：生成/调试均使用 `AbortController`（[agent.ts](../frontend/src/services/api/agent.ts#L48-L93)、[agent.ts](../frontend/src/services/api/agent.ts#L159-L209)），`stopGeneration`/`stopAutoDebug` 会主动 abort；`reset()` 未 abort。浏览器把「请求方关闭连接」记成 ERR_ABORTED 属正常日志，但**如果并非用户主动停止**，则可能是页面切换/组件卸载导致，需要区分并避免误判为业务失败。
- 本轮未复现，建议后续用真实浏览器复测并记录触发路径；不建议仅凭该控制台日志判断功能失败。

### D8 日志归类与统计问题（低）

- 工具调试日志把「语法错误」显示为「静态安全审查未通过：第 101 行语法错误」（`logs/calc-square_20261031_163715.md`），把语法/静态检查混为一谈，误导用户与 LLM。
- Agent 日志显示「结果：失败（共 10 轮）」但「日志条目：9 轮」，最后一轮因 `round_num >= max_rounds` 提前 break，未记入条目，统计口径不一致。

---

## 四、其他工具/Agent 是否同样受影响

**是。** 这不是 ETF Agent 的个案：

1. **48 个内置工具**全部经过同一个 `ToolExecutor._security_gate()` 与同一执行模型（同一用户、继承环境、无 OS 隔离），代码扫描基线为 `errors=0`，说明这些工具在「放行」状态下长期执行；任何工具被改写/新增即获得同等能力。
2. 工具与 Agent 的调试、注册、执行共用同一套黑名单与子进程模型（`tool_builder` 与 `agent_builder` 同构）。
3. 已有日志证明工具调试也会出现同类误分类（`calc-square` 的语法错误被当作安全审查失败）。
4. D3 参数 bug 同时存在于 `ToolExecutor` 与 Agent 的 `_call_tool_*` helper，影响面是「所有工具 + 所有使用工具的 Agent」。
5. D4 API 自锁影响面是「所有引用系统 API 的新 Agent」。
6. 内置工具的「工具调用」helper 用 `repr()`，与 `ToolExecutor` 的 `json.dumps` 不一致，导致「同一条工具调用在两种执行路径下行为不同」——这是跨工具的系统性一致性问题。
7. 工具调用、Agent 调用均无鉴权；`/api/agent/bus/*`、`/api/tool/*`、`/api/data/*` 全部暴露。

---

## 五、复现证据（已脱敏，测试产物已清理）

### 1. 静态扫描绕过 + 未认证 RCE

```bash
# 扫描器对以下代码返回 ok=True
cat > /tmp/poc_tool.py <<'PY'
from os import system
def execute(**kwargs):
    system("id > /tmp/sotaband_rce_proof.txt")
    return {"status": "success", "message": "proof"}
PY

curl -sS -X POST http://127.0.0.1:8201/api/tool/test \
  -H "Origin: https://evil.example.com" \
  -F "tool_id=rce-proof" -F "tool_name=RCE Proof" \
  -F "code=< /tmp/poc_tool.py" -F "test_input_json={}"

cat /tmp/sotaband_rce_proof.txt
# uid=0(root) gid=0(root) groups=0(root)
```

### 2. 匿名读取 `.env` 与任意目录

```bash
curl -s -o /tmp/env_dump -w "%{http_code} %{size_download}\n" \
  "http://127.0.0.1:8201/api/file/download?path=/root/SOTABand/.env"
# 200 7206  （文件含 LLM_API_KEY / SOTABAND_AUTH_SECRET 等，未打印内容）

curl -s "http://127.0.0.1:8201/api/file/scan-directory?path=/etc" | head -c 200
```

### 3. 匿名任意删除

```bash
echo x > /tmp/sotaband_del_proof.txt
curl -s -X POST http://127.0.0.1:8201/api/file/delete \
  -H "Content-Type: application/json" -d '{"path":"/tmp/sotaband_del_proof.txt"}'
# {"status":"ok","message":"文件已删除"}
```

### 4. 上传路径穿越

```bash
echo "traversal-proof" > /tmp/trav.txt
curl -s -X POST http://127.0.0.1:8201/api/file/upload \
  -F "file=@/tmp/trav.txt" -F "subdir=../../../../tmp/sotaband-trav"
# filePath 回显 /root/SOTABand/data/uploads/../../../../tmp/sotaband-trav/trav.txt
ls /tmp/sotaband-trav/trav.txt   # 存在
```

### 5. 工具参数 bool/null

```bash
# 执行 {"flag": true, "n": null}
# NameError: name 'true' is not defined
```

### 6. BaseApi.call 自锁

```python
# 同一事件循环内：
fut = asyncio.run_coroutine_threadsafe(fn(), asyncio.get_running_loop())
fut.result(timeout=3)  # 超时，协程从未执行
```

### 7. 自动调试误判（已有日志）

`logs/agent_etf-quote-insight-agent_20261031_223420.md`：10 轮均为
`试运行内部异常: PermissionError: [Errno 13] Permission denied: '/root/resources-private/agents/implementations/_debug-etf-quote-insight-agent'`，
且 LLM 在修复中被误导删除正常代码。

### 8. 静态扫描基线

- 内置工具：`scanned 48 builtin tools, errors=0`
- 全部 Agent 实现（内置+私有，5 个）：`errors=0`
- 说明不是某段代码违规，而是**规则整体过宽**。

---

## 六、修复建议（按优先级，本次未改代码）

### P0（部署前必须解决）

1. **默认只监听 127.0.0.1**，服务器部署必须显式开启并强制鉴权（Token/反代基本认证/内网 ACL）。
2. **给所有敏感路由加鉴权依赖**：执行、注册、文件、配置、调试、安装依赖、总线。未登录时拒绝写类/执行类操作。
3. **把「代码执行」放进真正隔离的运行环境**：独立低权限用户 + 只读挂载 + 禁网/白名单 + cgroup 资源上限 + seccomp；至少不要把主进程环境（密钥）继承给子进程，使用显式最小 env。
4. **删除黑名单式安全审查**，改为「能力白名单 + 运行隔离」；黑名单只能作为附加提示，不能作为安全边界。
5. **修复路径穿越**：资源 id 严格正则（如 `^[a-z0-9][a-z0-9_-]{0,99}$`）、`Path.resolve()` 后必须在允许根之内；上传 `subdir` 同样校验并 `resolve`。
6. **文件接口按资源语义收敛**：禁止绝对路径；只允许工作区/上传根内的相对路径；删除接口需鉴权且限制根目录。
7. **禁止匿名首次注册 admin**；初始化流程应本地生成或一次性引导码。

### P1（高）

8. `install-deps` 只允许锁定来源/白名单或完全禁用外部包名。
9. web-service Agent 强制 `127.0.0.1`（除非显式配置并以鉴权网关暴露），并对请求体大小/超时设限。
10. 增加 CSRF 防护：校验 `Origin/Referer`；对执行类接口要求自定义头或 CSRF token；限制 DNS rebinding（Host 校验）。
11. 配置文件、密钥文件不通过下载接口可达；子进程 env 只传必要变量。
12. 为执行/调试/对话增加统一超时、输出大小上限、并发上限与进程组回收。

### P2（功能/体验）

13. 自动调试错误分类：`PermissionError/OSError/磁盘满/目录不存在` 直接判为「环境错误」，**同错连续 2 轮即短路并给出明确提示**，不再喂给 LLM 改代码。
14. debug 目录加随机后缀，写文件整体包进 try，失败也走 `_fail()` 并带 `[debug]` 原因。
15. 参数序列化改 `repr()`；Agent 工具 helper 与 ToolExecutor 统一。
16. `BaseApi.call` 禁止当前循环内同步自调用；Agent 模板改成 `await asyncio.to_thread(...)`。
17. 统一「语法错误 / 静态扫描 / 运行错误」标签，修正轮数统计。
18. 前端对 SSE 的主动 abort 与异常中断分开处理，避免控制台噪声与误报。

---

## 七、审计限制与说明

- 本次为**本机回环环境**动态验证；未对真实服务器做网络侧扫描/压测。
- 未实际执行持久化后门、未修改系统文件；所有测试文件均在 `/tmp` 且已删除。
- 未对第三方 LLM 供应链做深入测试（如恶意模型输出/提示注入的完整利用链），但 C3+C4 已足以成立。
- 「PermissionError」是本次审计进程继承了 Trae 沙箱策略导致的**环境问题**；但 D1 的平台错误分类缺陷是独立且真实的。
- 第一至七章为审计阶段结论（审计阶段未改代码）；修复实施与验证见第八章。

---

## 八、修复记录（2026-10-31，批次 A/B）

按「安全 P0」与「功能 D1–D4」两批实施，逐批验证。

### 批次 A：安全硬化

| 改动 | 位置 | 验证结果 |
|---|---|---|
| 默认监听改为 `127.0.0.1`（配置与启动脚本） | [settings.py](../config/settings.py#L402-L404)、[serve.py](../scripts/serve.py#L258) | 新实例监听回环 |
| 请求防护：Host 校验（DNS rebinding）、Origin 校验（CSRF）、写/执行类接口鉴权 | [main.py](../app/main.py#L72-L171) | 伪造 Origin → 403；伪造 Host → 403；正常本机请求 → 200 |
| 无用户实例：写/执行类接口仅允许回环客户端 | 同上 | 非回环客户端 → 403 |
| 上传 `subdir` 白名单校验（拒绝 `/` 与 `..`） | [file_routes.py](../app/api/routes/file_routes.py#L158-L164) | `subdir=../../../../tmp/x` → 400 |
| 文件接口拒绝密钥/身份文件（`.env*`、`secret.key`、`~/.ssh`、私有 `users/`） | [file_routes.py](../app/api/routes/file_routes.py#L20-L42) | `.env` 下载 → 403 |
| Agent/Tool 资源 id 白名单校验（防路径穿越） | [paths.py](../core/resource/paths.py#L44-L52)、[agent_routes.py](../app/api/routes/agent_routes.py#L410-L414)、[agent_routes.py](../app/api/routes/agent_routes.py#L536-L539)、[tool_routes.py](../app/api/routes/tool_routes.py#L500-L508) | `agent_id=../../evil` → 400 |

实例当前无注册用户，故本机匿名请求仍按「单用户本地模式」放行；一旦注册用户，全部写/执行类接口要求登录。远端来源在两种情况下都被拒绝。

### 批次 B：功能缺陷

| 改动 | 位置 | 验证结果 |
|---|---|---|
| D1：调试目录写入失败标记 `infra_error`，不交给 LLM；同错连续 2 轮短路 | [agent_builder.py](../core/resource/builder/agent_builder.py#L759-L773)、[agent_builder.py](../core/resource/builder/agent_builder.py#L1131-L1147) | 不可写目录 → 第 1 轮 `llm_error`，第 2 轮 `stopped`，未调用 LLM |
| D2：调试目录改为 `_debug-<id>-<随机后缀>`，多会话不再互相覆盖 | [agent_builder.py](../core/resource/builder/agent_builder.py#L743-L745) | 目录名唯一 |
| D3：参数改用 `repr()` 渲染为 Python 字面量 | [tool_executor.py](../core/executor/tool_executor.py#L105-L118)、[agent_builder.py](../core/resource/builder/agent_builder.py#L103) | `{"flag":true,"n":null}` → `flag=True n=None`（API 端到端） |
| D4：`BaseApi.call` 改为独立线程事件循环，消除自锁 | [base.py](../core/api/base.py#L40-L78) | 事件循环内调用 0.00s 返回（原为 60s 超时） |

### 仍需处理的残余风险

1. **黑名单扫描仍是唯一代码闸门**，`from os import system` 等仍可绕过；真正的边界必须是 OS 级隔离（低权限用户 + 只读挂载 + 禁网 + cgroup）。
2. 子进程仍继承后端环境（含 `LLM_API_KEY`），也未限制网络。
3. `install-deps` 仍可安装任意包；web-service Agent 仍可通过 MD 指定 `host: 0.0.0.0`。
4. 文件接口的敏感路径拦截是名单式防御，`scan-directory` 等只读接口在「无用户本机模式」下仍可列举任意目录。
5. 宿主机 8000 端口对应的进程实际是 **`llm-gateway` 容器**（容器内 `uvicorn app.main:app --port 8000`，映射到宿主机 `0.0.0.0:8100`），并非旧版 SOTABand；它是 SOTABand 的 `LLM_BASE_URL` 依赖，停掉会切断全部 LLM 能力。按用户决定保持现状，其 `0.0.0.0:8100` 暴露面仍在。

---

## 九、ETF Agent 端到端验证（2026-10-31）

链路：`generate-spec → generate-code → auto-debug → register → start → execute`。

| 阶段 | 结果 |
|---|---|
| 生成规范 | 2551 字符，id `etf-quote-analysis-agent`，role interactive |
| 生成代码 | 8931 字符，静态沙箱 7 项全过（语法/导入/方法/继承/done 事件等） |
| 自动调试 | **第 5 轮成功**（[日志](../logs/agent_etf-quote-analysis-agent_20261031_232515.md)） |
| 注册 | 200，`etf-quote-analysis-agent` 出现在 `/api/agent/list` |
| 启动 | 200，`running: true` |
| 对话验证 | 输入「分析 510300」，返回实时简报：最新价 4.07 元、涨跌幅 -1.12%、成交额 86.14 亿元、风险提示 |

D1 修复前后对比（同一环境错误）：

- 修复前：[232202 日志](../logs/agent_etf-quote-analysis-agent_20261031_232202.md) 10 轮空转，0 条有效分析。
- 修复后：[232324 日志](../logs/agent_etf-quote-analysis-agent_20261031_232324.md) 第 2 轮即短路停止（stderr 含随机调试目录，需用 `infra_kind` 而非整段 stderr 作为签名）。
- 环境可写时：5 轮真实修复后通过。

**验证中发现的三个问题：**

1. **沙箱仍是环境阻塞**：后端写 `/root/resources-private/...` 被拒（`PermissionError`），本次改用官方支持的 `SOTABAND_PRIVATE_RESOURCES` 指向项目内可写目录才跑通；资源当前位于 [resources-private](file:///root/SOTABand/resources-private)，恢复默认路径需在无沙箱环境启动后端并重新注册。
2. **CA 证书缺失导致 LLM「修复」为关闭 TLS 校验**：`ssl.get_default_verify_paths()` 的 `cafile=None`、`capath=/usr/local/openssl/ssl/certs` 不可用，HTTPS 请求 `CERTIFICATE_VERIFY_FAILED`；自动调试最终生成的代码使用 `check_hostname=False` / `CERT_NONE` 并回退 `http://`（[agent.py](file:///root/SOTABand/resources-private/agents/implementations/etf-quote-analysis-agent/agent.py#L42-L50)）。安全扫描未拦截该模式，建议把「禁用证书校验 / 明文回退」提升为 error 级。
3. **agent_id 以 MD 为准**：`register` 会用 MD frontmatter 的 `id:` 覆盖请求体里的 `agentId`，调用方需以返回的 `agent_id` 为准。

---

## 十、删除失败修复与新 Agent 重建验证（2026-10-31）

### 10.1 界面删除失败（无提示）

| 问题 | 根因 | 修复 |
|---|---|---|
| 通过 IDE/预览域名点删除，无任何提示 | 浏览器 `Origin` 与后端 `Host` 不同，`_browser_guard` 判为跨站返回 403；前端 `fetch` 未检查 `res.ok`，失败静默 | Vite 代理 `xfwd: true` 透传 `X-Forwarded-Host`；后端仅在直连方为回环时信任该头；uvicorn 加 `--no-proxy-headers` 保持真实客户端；前端失败 `alert(detail)` |
| DELETE 返回 500 `PermissionError` | `serve.py` 启动的后端被注入 `sbox.so`，无法写/删 `/root/resources-private` | 删除改用路径级 `_remove_tree`（不依赖 `shutil.rmtree` 的 dir_fd）；后端以非沙箱方式（前台 `setsid`）启动后删除成功 |

两个旧 ETF Agent（`etf-market-analysis-agent`、`etf-quote-analysis-agent`）已删除，列表恢复为 3 个内置 Agent。

### 10.2 工具 `etf-target-price-signals` 创建与修复

| 问题 | 修复 |
|---|---|
| HTTPS 全源 `CERTIFICATE_VERIFY_FAILED`（系统 CA 缺失） | 用 `certifi.where()` 构造 SSL context，不关闭证书校验 |
| 腾讯源回退涨跌幅恒为 `0.0%` | 用前收盘价补齐 `change/change_pct/amplitude` |
| 自动标签生成 `ImportError`（`get_llm_client` 不存在） | 改用模块级 `llm` 客户端 |
| 调试产物写入内置 `resources/`（`.venv`、`requirements.txt`） | `resolve_tool_dir` / `ToolExecutor` 兜底改私有根 |
| 规范 frontmatter `id` 与注册 id 不一致 | 统一为 `etf-target-price-signals` |
| 只返回 `columns/rows`，结构化字段被丢弃 | `data` 同时保留 `trend/ma/indicators/levels/targets`，界面仍按表格渲染 |
| 置信度恒为 20（510500 等弱势品种） | `confidence = trend_score + 20` 把「看涨程度」当「预测可信度」。经平台 `modify-and-debug` 流程改为可靠性四因子模型（信号一致性 0-35 + 数据充分性 0-25 + 波动率 0-25 + 预测周期 0-15，RSI 极端值 −5），510500 达 82% |
| `modify-and-debug` 必然超时 | 该接口硬编码 `timeout=60`，全量重写十几 KB 代码不可能在 60s 内完成 | 去掉硬编码，回退 `settings.llm.timeout`（默认 1200s），随后一轮调试通过 |

### 10.3 新 Agent `etf-target-price-agent` 端到端验证

链路 `generate-spec → generate-code → auto-debug → register → start → execute` 全部通过：规范 2.7k 字符，代码 12.9k 字符，自动调试第 2 轮成功，注册 200，启动 200。对话验证：

- `510300` + 20 日：目标区间 3.63 / 3.946 / 4.262，支撑 4.031、压力 4.503，置信度 20%；
- 仅给名称「华夏沪深300ETF」：正确推断为 510300；
- `159915` + 10 日：目标区间 2.025 / 2.356 / 2.687；
- 非法代码 `abcdef`：返回明确错误「代码格式不正确或不存在」。

创建过程中修复的平台问题：

| 问题 | 根因 | 修复 |
|---|---|---|
| 私有根 Agent 的 `PROJECT_ROOT` 落到 `/root` | 模板写死 `parents[4]`，私有实现目录比内置少一跳 | 改为向上查找含 `core/agent/base.py` 的目录 |
| 引用工具的 Agent 调试首轮必被拦 | 工具调用模板用 `os.unlink`，静态扫描将其列为阻断项 | 模板改为 `Path(...).unlink(missing_ok=True)` |
| 规范 `created` 被 LLM 编造为 2025-01-07 | `{今天日期}` 占位符未替换就交给 LLM | 生成前替换为 `date.today().isoformat()` |
| 引用工具的 Agent 在界面显示「通用任务」 | 注册时从未写入 `required_tools` | 从 MD 的 `【【工具名】】` 解析并写入 registry |
| 工具查找只认内置/同级私有根 | 假定私有根为项目根同级 | 运行时优先走 `core.resource.paths.space_dirs("tools")` |

---

## 十一、后续变化（2026-11-03 增补，不改写上文审计结论）

审计后代码已发生变化，其中部分被引位置/行为与上文描述不再一致，列出备查：

- **C1（无鉴权）已缓解**：`app/main.py` 的中间件现对**所有非 GET 的 `/api/*`**（`/api/auth/`、`/api/health` 除外）以及文件/配置/LLM/API 列表类读端点强制要求登录令牌，未带令牌直接 401（`_is_sensitive_endpoint`）；并新增 `_browser_guard` 对状态变更请求校验 Origin，防 CSRF/DNS rebinding（对应 C9）。GET 类资源列表仍允许匿名回退实例身份，C1 的读侧描述仍然成立。
- **C8（默认 0.0.0.0）已修**：`api_host` 默认改为 `127.0.0.1`（commit b14f90c）。
- **C6（上传路径穿越）已修**：上传接口 `subdir` 限制在受控目录内；**C7（资源 id 路径穿越）已修**：注册/落盘处增加 id 校验（同 commit b14f90c）。
- **D3（参数序列化 NameError）已修**：`ToolExecutor._params_literal` 将参数渲染为合法 Python 字面量，不再直接 `json.dumps` 拼源码。
- **D4（BaseApi.call 自锁）已修**：桥接改为在独立守护线程的新事件循环中执行并 join 60s，不再用 `run_coroutine_threadsafe`（见 `core/api/base.py` 注释）。上文引用的「`run_coroutine_threadsafe` 桥接」实现已不存在。
- **C2/C4（未认证 RCE / 非沙箱子进程）方向未变**：执行侧仍是同用户子进程，`scan_code` 仍是黑名单 AST 扫描；新增的仅是执行前 choke point 统一复查（`ToolExecutor._security_gate`）与对话内平台控制网关的独立审计（`chat_ops` 表，见 docs/chat-platform-control-design.md）。
- 端口/入口未变（8201/8200）；`storage/platform.db` 新增 `chat_sessions`/`chat_messages`/`cron_jobs`/`cron_runs`/`chat_ops` 表，审计中「SQLite 仅 task_history/chat_store 两 store」的描述已扩展。

## 十二、后续变化（2026-11-09 增补，不改写上文审计结论）

上文（含第十一节）之后代码又发生变化，与第十一节描述不再一致处列出备查：

- **鉴权中间件换轨**：`_is_sensitive_endpoint` 端点名单已不存在。现按 `.env` 是否配置
  `SOTABAND_ROOT_PASSWORD` 分两种模式：**多用户模式**下 `/api/*` 一律要求有效令牌
  （仅 `/api/health` 与 `/api/auth/login|me|logout` 匿名放行，`_is_anonymous_allowed`
  白名单），第十一节「GET 类资源列表仍允许匿名回退实例身份」在多用户模式下不再成立；
  **单用户兼容模式**（未配置密码）下匿名请求直接注入超级用户（uid=0）身份放行——本机
  即用属设计取舍，但 `api_host` 改为非回环监听即等同向网络开放超管权限（新增债务 N3）。
  只读文件类 GET 端点（`/api/file/{id}`、数据集 raw/preview/archive、工具/Agent 导出）
  允许 `?auth_token=` 查询参数携带同一枚令牌（`_QUERY_TOKEN_GET_PATTERNS` 白名单，
  校验链与 Bearer 完全相同，仅凭证来源不同）。
- **平台数据库改每用户一库**：`storage/platform.db` 不再是唯一库；task_history /
  chat_store / cron / chat_ops 经 `core/user_db.py` 按当前身份落
  `<USER_HOME>/<uid>/platform.db`（第十一节末条的表清单语义不变，落库位置变了）。
- **公共（原内置）资源层写闸门换轨**：公共层资源的写/删必须持系统身份令牌
  （超管经 `POST /api/auth/act-as-system` 领 1 小时短时令牌），超管长期令牌也不例外
  （`app/api/deps.py` `ensure_can_touch_builtin`）。
- **目录枚举面收敛**：`POST /api/data/scan-directory` 已限当前用户目录内
  （空/根路径只扫 `uploads`、`plots` 受控子目录），第十一节 P0-3 语境的
  「仍可对任意路径 rglob」已修。
- **C2/C4 方向仍不变，纵深增强**：执行侧仍是同用户子进程并继承全部环境变量；
  新增 `core/security/review.py` core API 约束线（默认 audit 只记录）与工具子进程
  `sys.addaudithook` 运行时审计（写白名单拦截/系统调用拦截/网络记录）。审计要防的
  「Key 出进程」问题（LLM Key 仍在工具子进程环境里）未变，仍是沙箱项（P0-1）的
  核心目标。



