"""Agent 工厂 — 异步子进程生命周期管理"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import AsyncGenerator

from core.proc_utils import no_window_kwargs

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class AgentFactory:
    """Agent 工厂 — 管理 Agent 子进程的完整生命周期"""

    def __init__(self):
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        # web-service Agent 的 HTTP 服务信息：{agent_id: {"host":..., "port":..., "apis":[...]}}
        self._services: dict[str, dict] = {}
        # 启动期由子进程上报的错误（如 spec/代码加载失败），便于 start 响应暴露原因
        self._startup_errors: dict[str, str] = {}

    @staticmethod
    def _resolve_impl_path(agent_id: str) -> str | None:
        """定位 Agent 实现目录（私有优先，相对 PROJECT_ROOT 的路径）。

        私有优先：编辑后的 agent.py 写入私有根，内置那份是随仓库分发的
        旧版本；否则会出现「详情页/编辑器是新的，实际运行的是旧代码」。
        均不存在时返回 None。
        """
        from core.resource import paths as _paths

        for root in _paths.read_roots():
            impl_dir = root / "agents" / "implementations" / agent_id
            if (impl_dir / "agent.py").exists():
                try:
                    return str(impl_dir.relative_to(PROJECT_ROOT))
                except ValueError:
                    # 私有根在项目之外时，返回绝对路径
                    return str(impl_dir)
        return None

    async def start(self, agent_id: str, impl_path: str = None) -> dict:
        """启动 Agent 子进程。若代码缺失则尝试从 spec 自动重新生成。"""
        if agent_id in self._processes and self._processes[agent_id].returncode is None:
            return {"agent_id": agent_id, "status": "already_running"}

        # 新的启动周期：清掉上一轮残留的启动错误
        self._startup_errors.pop(agent_id, None)

        if impl_path is None:
            # 在内置 + 私有资源根中检索实现目录
            impl_path = self._resolve_impl_path(agent_id) or f"resources/agents/implementations/{agent_id}"

        # 检查 agent.py 是否存在
        agent_file = PROJECT_ROOT / impl_path / "agent.py"

        if not agent_file.exists():
            # 尝试从 MD 规范文档重新生成代码
            rebuilt = await self._try_rebuild(agent_id)
            if not rebuilt:
                return {"agent_id": agent_id, "status": "failed",
                        "error": f"Agent 代码不存在且无法从 spec 重新生成: {agent_file}"}

        # 预检查：验证代码质量
        if agent_file.exists():
            code = agent_file.read_text(encoding='utf-8')
            issues = self._check_agent_code(code)
            if issues:
                # 尝试自动修复
                fixed = self._auto_fix_code(code)
                if fixed != code:
                    agent_file.write_text(fixed, encoding="utf-8")
                    # 重新检查
                    remaining = self._check_agent_code(fixed)
                    if remaining:
                        return {"agent_id": agent_id, "status": "failed",
                                "error": f"Agent 代码错误（已自动修复部分但仍存在）: {'; '.join(remaining)}"}
                else:
                    return {"agent_id": agent_id, "status": "failed",
                            "error": f"Agent 代码错误: {'; '.join(issues)}"}

        python = sys.executable
        entrypoint = str(PROJECT_ROOT / "core" / "agent" / "entrypoint.py")

        try:
            proc = await asyncio.create_subprocess_exec(
                python, entrypoint,
                "--agent-id", agent_id,
                "--impl-path", impl_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(PROJECT_ROOT),
                **no_window_kwargs(),
            )
            self._processes[agent_id] = proc

            # 等待子进程初始化
            await asyncio.sleep(1.0)

            if proc.returncode is not None:
                # 进程已退出 → 读取 stderr 获取错误信息
                try:
                    stderr_data = await proc.stdout.read(4096)  # 可能包含 error 事件
                    stderr_text = stderr_data.decode(errors="replace") if stderr_data else ""
                except Exception:
                    stderr_text = ""
                try:
                    err_data = await proc.stderr.read(4096)
                    err_text = err_data.decode(errors="replace") if err_data else ""
                except Exception:
                    err_text = ""
                detail = (stderr_text + err_text)[:300].strip() or "进程异常退出"
                self._processes.pop(agent_id, None)
                return {"agent_id": agent_id, "status": "failed", "running": False, "error": detail}

            # 仅对 web-service 类型尝试捕获 service_ready 事件。
            # 其他类型（interactive/task）不应在此读取 stdout——那会与后续
            # execute_stream 的读取争抢同一条流，并可能吞掉子进程输出，
            # 导致「每一步用户输入都要等很久才收到响应」。三类 Agent 共用
            # 同一套子进程启动流程，因此这里按角色分支而不是按"有没有关键字"。
            role = self._resolve_role(agent_id, impl_path)
            if role == "web-service":
                svc = await self._probe_service_ready(agent_id)
                if svc is None and proc.returncode is not None:
                    # 启动期就退出了（加载 spec/代码失败、端口被占等）
                    # → 明确回报，避免留下"状态 running 但服务不可用"的假象
                    err = self._startup_errors.pop(agent_id, "")
                    self._processes.pop(agent_id, None)
                    return {
                        "agent_id": agent_id,
                        "status": "failed",
                        "running": False,
                        "error": err or f"Agent 进程启动后立即退出（code={proc.returncode}）",
                    }

            result = {"agent_id": agent_id, "status": "started", "running": True, "role": role}
            if agent_id in self._services:
                result["service"] = self._services[agent_id]
            return result
        except Exception as e:
            return {"agent_id": agent_id, "status": "failed", "error": str(e)}

    def _resolve_role(self, agent_id: str, impl_path: str | None = None) -> str:
        """解析 Agent 角色：``interactive`` / ``task`` / ``web-service``。

        历史教训：这里曾用「agent.py 文本里有没有 `web-service` 关键字」来猜角色，
        结果判定实际取决于 **agent id 是否含该子串**（生成器会把 id 写进首行
        docstring）。于是 id 叫 `xxx-web-service` 的恰好"能用"，叫
        `action-description-agent` 的 web-service Agent 永远判定失败 →
        不抓 `service_ready` → 服务起在端口上了，界面却显示"未运行"。

        现在按权威程度依次解析（任一命中即返回）：
        1. registry 条目的 ``role``（唯一真相，注册/编辑时写入）
        2. 实现目录下的 ``spec.md``（子进程实际加载的那份）
        3. ``definitions/{id}.md``（内置 + 私有，私有优先）
        4. 兜底：agent.py 文本（兼容手工编写、显式写了 `AgentRole.WEB_SERVICE` 的实现）
        """
        # 1) registry（最权威）
        try:
            from core.resource.registry.agent_registry import AgentRegistry

            for e in AgentRegistry()._read():
                if str(e.get("id") or "") == agent_id:
                    role = self._normalize_role(e.get("role"))
                    if role:
                        return role
                    break
        except Exception:
            pass

        # 2) / 3) MD 规范文档（子进程的入口契约也来自它）
        try:
            from core.agent.entrypoint import _parse_role_from_md

            md = ""
            if impl_path:
                spec_md = PROJECT_ROOT / impl_path / "spec.md"
                if spec_md.exists():
                    md = spec_md.read_text(encoding="utf-8-sig")
            if not md:
                from core.resource import paths as _paths

                for root in _paths.read_roots():
                    cand = root / "agents" / "definitions" / f"{agent_id}.md"
                    if cand.exists():
                        md = cand.read_text(encoding="utf-8-sig")
                        break
            if md:
                return str(_parse_role_from_md(md).value)
        except Exception:
            pass

        # 4) 兼容兜底：手工实现里显式声明的角色
        try:
            if impl_path is None:
                impl_path = self._resolve_impl_path(agent_id) or f"resources/agents/implementations/{agent_id}"
            agent_file = PROJECT_ROOT / impl_path / "agent.py"
            if agent_file.exists():
                code = agent_file.read_text(encoding="utf-8")
                if "AgentRole.WEB_SERVICE" in code:
                    return "web-service"
                if "AgentRole.INTERACTIVE" in code:
                    return "interactive"
        except Exception:
            pass
        return "task"

    @staticmethod
    def _normalize_role(value) -> str:
        """把各种写法归一为三类之一；无法识别返回空串。"""
        v = str(value or "").strip().lower().replace("_", "-")
        if v in ("web-service", "webservice", "observer"):
            return "web-service"
        if v in ("interactive", "chat"):
            return "interactive"
        if v in ("task", "worker"):
            return "task"
        return ""

    def _is_web_service(self, agent_id: str, impl_path: str | None = None) -> bool:
        """判断该 Agent 是否为 web-service 类型。"""
        return self._resolve_role(agent_id, impl_path) == "web-service"

    async def _probe_service_ready(self, agent_id: str, timeout: float = 8.0) -> dict | None:
        """等待子进程上报 ``service_ready``，返回服务信息（host/port/apis）。

        仅用于 **web-service** 类型：交互/任务型绝不能读 stdout —— 那会与后续
        ``execute_stream`` 抢同一条流并吞掉子进程输出（表现为"每一步输入都要等很久"
        或消息丢失）。

        实现要点：
        - 逐行读、限时轮询。之前是「一次 read + 1.5s 超时」，Agent 依赖导入慢
          （torch / 大模型等）时必然读不到，服务明明起来了却报"未运行"。
        - 顺带捕获 ``error`` 事件：加载 spec/代码失败时子进程会先发 error 再退出，
          把这个原因留给 ``start()`` 回报，避免"点启动没反应"。

        Returns:
            服务信息 dict；未就绪返回 ``None``（不影响启动流程）。
        """
        proc = self._processes.get(agent_id)
        if proc is None or proc.stdout is None:
            return None
        if agent_id in self._services:
            return self._services[agent_id]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=min(remaining, 1.5))
            except asyncio.TimeoutError:
                if proc.returncode is not None:      # 进程已退出，再等也没意义
                    return None
                continue
            except Exception:
                return None

            if not line:                              # EOF：子进程关闭了 stdout
                return None

            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except Exception:
                continue                          # 非 JSON 行（日志/预热输出）忽略

            name = event.get("event")
            if name == "service_ready":
                data = event.get("data") or {}
                self._services[agent_id] = {
                    "host": data.get("host", "127.0.0.1"),
                    "port": data.get("port"),
                    "apis": data.get("apis", []),
                }
                return self._services[agent_id]
            if name in ("error", "done"):
                msg = str((event.get("data") or {}).get("message") or "").strip()
                if name == "error" and msg:
                    self._startup_errors[agent_id] = msg
                return None

    def get_service(self, agent_id: str) -> dict | None:
        """返回 web-service Agent 的 HTTP 服务信息（未启动或非 web-service 时为 None）。"""
        return self._services.get(agent_id)

    @staticmethod
    def _check_agent_code(code: str) -> list[str]:
        """启动前验证 agent 代码的常见错误"""
        issues = []
        # 错误的 import
        if re.search(r'from base_agent import|import base_agent', code):
            issues.append("错误的导入: 应使用 'from core.agent.base import BaseAgent, AgentContext'")
        # 缺失必需的 import
        if 'from pathlib import Path' not in code:
            issues.append("缺少 'from pathlib import Path'")
        if 'import json' not in code:
            issues.append("缺少 'import json'")
        if 'import sys' not in code:
            issues.append("缺少 'import sys'")
        # 错误的方法调用
        if 'ctx.get_input(' in code:
            issues.append("不存在的方法 ctx.get_input()，应用 kwargs.get()")
        # ctx.session / ctx.metadata_id 不存在
        if 'ctx.session[' in code or 'ctx.session.' in code:
            issues.append("不存在的属性 ctx.session，多轮状态应存储在 self._state 中")
        if 'ctx.metadata_id' in code:
            issues.append("不存在的属性 ctx.metadata_id，应为 ctx.session_id")
        # __import__ hack
        if "__import__" in code and 'importlib' not in code:
            issues.append("不应使用 __import__()，应使用 import 语句")
        # 语法错误
        try:
            ast.parse(code)
        except SyntaxError:
            issues.append("Python 语法错误")
        return issues

    @staticmethod
    def _auto_fix_code(code: str) -> str:
        """自动修复常见的 agent 代码问题"""
        fixed = code

        # 修复错误的 import
        fixed = re.sub(r'^from base_agent import.*$', 'from core.agent.base import BaseAgent, AgentContext', fixed, flags=re.MULTILINE)
        fixed = re.sub(r'^import base_agent.*$', 'from core.agent.base import BaseAgent, AgentContext', fixed, flags=re.MULTILINE)

        # 修复 ctx.get_input
        fixed = fixed.replace('ctx.get_input(', 'kwargs.get(')

        # 修复 ctx.session → self._state（应使用实例变量存储多轮状态）
        fixed = fixed.replace('ctx.session[', 'self._state[')
        fixed = fixed.replace('ctx.session.', 'self._state.')
        # 修复 ctx.metadata_id → ctx.session_id
        fixed = fixed.replace('ctx.metadata_id', 'ctx.session_id')

        # 修复 __import__ hack
        fixed = re.sub(r"__import__\('sys'\)\.executable", 'sys.executable', fixed)
        fixed = re.sub(r'__import__\("sys"\)\.executable', 'sys.executable', fixed)

        # 确保标准导入存在（在 docstring 之后插入）
        required_imports = [
            'import json',
            'import os',
            'import subprocess',
            'import sys',
            'import tempfile',
            'from pathlib import Path',
            'from typing import AsyncGenerator',
        ]
        for imp in required_imports:
            if imp not in fixed:
                # 在第一个 import 行之前插入
                lines = fixed.split('\n')
                inserted = False
                new_lines = []
                for i, line in enumerate(lines):
                    new_lines.append(line)
                    if not inserted and (line.startswith('import ') or line.startswith('from ')):
                        # 插入在最后一个现有导入之后
                        if i + 1 < len(lines) and not lines[i + 1].startswith('import ') and not lines[i + 1].startswith('from '):
                            new_lines.append(imp)
                            inserted = True
                if not inserted:
                    # 没有导入 → 在 docstring 后插入
                    new_lines = []
                    for line in lines:
                        new_lines.append(line)
                        if line.strip().startswith('"""') and not inserted:
                            new_lines.append('')
                            new_lines.append(imp)
                            inserted = True
                fixed = '\n'.join(new_lines)

        return fixed

    async def _try_rebuild(self, agent_id: str) -> bool:
        """尝试从 MD 规范文档重新生成 agent 代码，返回是否成功"""
        # 在内置 + 私有资源根中查找规范文档
        from core.resource import paths as _paths

        spec_file = None
        for root in _paths.read_roots():
            cand = root / "agents" / "definitions" / f"{agent_id}.md"
            if cand.exists():
                spec_file = cand
                break
        if spec_file is None:
            return False

        try:
            from core.resource.builder.agent_builder import AgentCodeBuilder
            builder = AgentCodeBuilder()
            spec = {"id": agent_id, "raw_md": spec_file.read_text(encoding='utf-8')}
            if not await builder.validate_spec(spec):
                return False

            code = await builder.build(spec)
            # 重建的代码写入「私有」实现目录（与新建资源策略一致）
            from core.resource import paths as _paths

            impl_dir = _paths.ensure_private_dirs("agents") / "implementations" / agent_id
            impl_dir.mkdir(parents=True, exist_ok=True)
            (impl_dir / "agent.py").write_text(code, encoding='utf-8')
            (impl_dir / "spec.md").write_text(spec_file.read_text(encoding='utf-8'), encoding='utf-8')
            return True
        except Exception:
            return False

    async def stop(self, agent_id: str) -> dict:
        """优雅停止 Agent 子进程。

        顺序：发送 stop 命令 → 关闭 stdin（触发 Agent 主循环退出并执行清理，
        如回收常驻工具进程 / 卸载模型）→ 等待其自行退出 → 超时才强制终止。

        注意：**不能**在发送 stop 后立刻 terminate()——那样 Agent 子进程
        来不及执行 on_stop() 清理，常驻工具进程与模型资源将无法被优雅释放。
        """
        proc = self._processes.pop(agent_id, None)
        # 清理 web-service 服务记录
        self._services.pop(agent_id, None)
        if proc is None:
            return {"agent_id": agent_id, "status": "not_running"}

        if proc.returncode is None:
            # 1) 发送 stop 命令
            try:
                task = json.dumps({"action": "stop"}) + "\n"
                proc.stdin.write(task.encode())
                await proc.stdin.drain()
            except Exception:
                pass
            # 2) 关闭 stdin → Agent 主循环读到 EOF 退出（会执行 on_stop 清理）
            try:
                proc.stdin.close()
            except Exception:
                pass
            # 3) 等待优雅退出（给清理留出时间；模型/显存随常驻进程退出释放）
            try:
                await asyncio.wait_for(proc.wait(), timeout=20)
            except asyncio.TimeoutError:
                # 4) 超时兜底：强制终止
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
        return {"agent_id": agent_id, "status": "stopped"}

    async def restart(self, agent_id: str) -> dict:
        """重启 Agent 子进程"""
        await self.stop(agent_id)
        await asyncio.sleep(0.5)

        # 在内置 + 私有资源根中重新定位实现目录后启动
        impl_path = self._resolve_impl_path(agent_id)
        return await self.start(agent_id, impl_path)

    async def execute(
        self, agent_id: str, content: str
    ) -> AsyncGenerator[dict, None]:
        """向 Agent 子进程发送任务，流式读取响应（总超时 120s）。

        兼容旧调用：仅传 content。
        """
        async for event in self.execute_stream(
            agent_id, params={"content": content}
        ):
            yield event

    async def execute_stream(
        self,
        agent_id: str,
        params: dict | None = None,
        context: dict | None = None,
        timeout: float = 120,
    ) -> AsyncGenerator[dict, None]:
        """向 Agent 子进程发送任务（支持任意参数），流式读取响应。

        Args:
            agent_id: Agent ID
            params: 传给 agent.execute(**params) 的参数（如 content / attachments / workspace_tool_ids）
            context: 执行上下文覆盖（session_id / user_id / metadata）
            timeout: 总超时（秒）
        """
        proc = self._processes.get(agent_id)
        if proc is None or proc.returncode is not None:
            yield {"event": "error", "data": {"message": f"Agent '{agent_id}' 未运行"}}
            return

        ctx = {"session_id": "default", "user_id": "default", "agent_id": agent_id}
        if context:
            ctx.update(context)

        task = {
            "action": "execute",
            "context": ctx,
            "params": dict(params or {}),
        }
        try:
            proc.stdin.write((json.dumps(task) + "\n").encode())
            await proc.stdin.drain()
        except Exception:
            yield {"event": "error", "data": {"message": "Agent 进程已断开"}}
            return

        # 流式读取 stdout：按行读取（JSON-line 协议），保证事件到达即被取出。
        # 注意：不要用 read(4096)——它会「攒满 4096 字节或 EOF」才返回，导致
        # 单条小事件被长时间挂起，表现为「每步输入都要等很久」。
        # readline() 在遇到换行符即返回，与子进程的 flush 配合可做到低延迟。
        #
        # 超时语义：
        #   - timeout=None  → 不设总超时（长耗时工具，如模型训练，必须支持）。
        #   - timeout>0     → 总时长上限，超出后向调用方报超时并终止子进程。
        # 空闲超时（idle）：单次 readline 长时间无新事件不代表任务卡死——
        # 训练类任务可能数分钟不产生中间输出。故给予较大的空闲容忍度，
        # 仅在「确实长时间完全无输出且进程仍存活」时才放弃等待。
        deadline = (time.time() + timeout) if timeout else None
        idle_timeout = max(600.0, float(timeout)) if timeout else 3600.0
        while True:
            if deadline is not None and time.time() > deadline:
                yield {"event": "error", "data": {"message": f"Agent 执行超时 ({int(timeout)}s)"}}
                try:
                    proc.terminate()
                except Exception:
                    pass
                return
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=idle_timeout)
                if not line:
                    yield {"event": "error",
                           "data": {"message": f"Agent '{agent_id}' 进程已退出"}}
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line.decode('utf-8'))
                    yield event
                    # done 事件到达即表示本轮执行结束，立即返回，
                    # 避免继续阻塞读取等待下一轮输入。
                    if isinstance(event, dict) and event.get("event") == "done":
                        return
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            except asyncio.TimeoutError:
                # 空闲超时：超过容忍时长仍无任何输出。
                yield {"event": "error",
                       "data": {"message": f"Agent 长时间无响应（{int(idle_timeout)}s 无输出）"}}
                break
            except Exception as e:
                yield {"event": "error", "data": {"message": f"读取 Agent 响应失败: {e}"}}
                return

    def is_running(self, agent_id: str) -> bool:
        proc = self._processes.get(agent_id)
        return proc is not None and proc.returncode is None

    def status(self, agent_id: str) -> dict:
        """返回 Agent 运行状态（供前端查询）。"""
        proc = self._processes.get(agent_id)
        if proc is None:
            return {"agent_id": agent_id, "running": False, "status": "stopped", "pid": None}
        running = proc.returncode is None
        return {
            "agent_id": agent_id,
            "running": running,
            "status": "running" if running else "stopped",
            "pid": proc.pid if running else None,
        }


# 全局单例
agent_factory = AgentFactory()
