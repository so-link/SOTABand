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

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class AgentFactory:
    """Agent 工厂 — 管理 Agent 子进程的完整生命周期

    同时是 Agent 间通信（todo 4.5）的**主进程路由中枢**：
    每个子进程绑定一个后台 reader，逐行解析 stdout——普通事件进入该 Agent
    的输出队列（execute_stream 消费），通信帧（core/agent/protocol.py）
    在此路由：订阅表维护、bus_publish 扇出、agent_call 转发与超时兜底。
    """

    def __init__(self):
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        # web-service Agent 的 HTTP 服务信息：{agent_id: {"host":..., "port":..., "apis":[...]}}
        self._services: dict[str, dict] = {}
        # ── 通信总线路由状态（todo 4.5）──
        # 订阅表：{channel: {agent_id}}；随进程退出清理
        self._subscribers: dict[str, set[str]] = {}
        # 悬挂的 RPC 请求：{req_id: (from_agent, loop, asyncio.Future)}
        self._bus_pending: dict[str, tuple] = {}
        # execute 事件输出队列 / 后台 reader 任务 / 实现目录（restart 复用）
        self._outputs: dict[str, asyncio.Queue] = {}
        self._readers: dict[str, asyncio.Task] = {}
        self._impl_paths: dict[str, str] = {}
        # 同一 Agent 的 execute 轮次串行化（事件流不互相穿插）
        self._exec_locks: dict[str, asyncio.Lock] = {}
        self._req_seq = 0

    # ══════════════════════════════════════════════════════════
    # 通信总线路由（todo 4.5 本地通信）
    # ══════════════════════════════════════════════════════════

    def _next_req(self) -> str:
        self._req_seq += 1
        return f"main#{self._req_seq}"

    def _bind_reader(self, agent_id: str, proc: asyncio.subprocess.Process):
        """为子进程绑定后台 stdout reader：事件入队，通信帧走路由。"""
        q: asyncio.Queue = asyncio.Queue()
        self._outputs[agent_id] = q
        self._readers[agent_id] = asyncio.ensure_future(
            self._route_reader(agent_id, proc, q))

    async def _route_reader(self, agent_id: str, proc, q: asyncio.Queue):
        """后台逐行读子进程 stdout，分发普通事件与通信帧。

        EOF 时：放入 None 哨兵唤醒等待中的 execute_stream；若该进程仍是
        当前代（崩溃路径，无人调用 stop），顺带清理路由状态。restart 时
        旧 reader 的清理被代际检查跳过——此时 stop() 已清理过，且
        self._outputs/_readers 可能已指向新一代，不能误删。
        """
        from core.agent.protocol import is_bus_frame, is_frame

        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8", "replace"))
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                try:
                    if is_bus_frame(obj) and obj.get("dir") == "o":
                        await self._route_frame(agent_id, obj)
                    elif is_frame(obj) and obj.get("dir") == "o":
                        pass  # 未知 v1 帧：丢弃，避免混入事件流
                    else:
                        await q.put(obj)
                except Exception:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            q.put_nowait(None)
            if self._processes.get(agent_id) is proc:
                self._cleanup_agent_routes(agent_id)

    def _cleanup_agent_routes(self, agent_id: str):
        """清理订阅表、悬挂 RPC、输出队列与 reader 任务（按代际安全弹出）。"""
        for chans in self._subscribers.values():
            chans.discard(agent_id)
        self._subscribers = {c: s for c, s in self._subscribers.items() if s}
        for req, (from_agent, loop, fut, to_agent) in list(self._bus_pending.items()):
            if (from_agent == agent_id or to_agent == agent_id) and not fut.done():
                fut.set_result({"ok": False, "error": "通信通道已关闭"})
            if from_agent == agent_id or to_agent == agent_id:
                self._bus_pending.pop(req, None)
        self._outputs.pop(agent_id, None)
        task = self._readers.pop(agent_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _route_frame(self, src: str, frame: dict):
        """处理子进程发来的通信帧。"""
        t = frame.get("type")
        if t == "bus_subscribe":
            for ch in frame.get("channels") or []:
                self._subscribers.setdefault(str(ch), set()).add(src)
        elif t == "bus_unsubscribe":
            for ch in frame.get("channels") or []:
                s = self._subscribers.get(str(ch))
                if s:
                    s.discard(src)
        elif t == "bus_publish":
            await self._fanout(frame)
        elif t == "agent_call":
            asyncio.ensure_future(self._proxy_call(src, frame))
        elif t == "agent_reply":
            entry = self._bus_pending.pop(frame.get("req"), None)
            if entry is not None:
                _from, loop, fut, _to = entry
                if not fut.done():
                    fut.set_result({
                        "ok": bool(frame.get("ok")),
                        "result": frame.get("result"),
                        "error": frame.get("error"),
                    })
        elif t == "platform_call":
            # 子进程请求平台操作：交给网关（引擎进程内、以会话用户身份执行）。
            # 作为任务运行——网关可能耗时较长（生成类操作），不能阻塞读循环。
            asyncio.ensure_future(self._handle_platform_call(src, frame))

    async def _handle_platform_call(self, src: str, frame: dict):
        """把子进程的 platform_call 帧交给 core.chat_control 网关并回执结果。

        身份取帧内 user_id（= 发起对话的用户），由网关注入 contextvar，
        因此对话内的权限边界与手工操作一致。异常一律转成结构化错误回给
        子进程，绝不让读循环侧的异常逃逸。
        """
        from core.agent.protocol import FRAME_PLATFORM_CALL_RESPONSE, make_frame
        req = frame.get("req")
        try:
            from core.chat_control import gateway
            result = await gateway.platform_call(
                str(frame.get("tool", "")), frame.get("args") or {},
                owner=gateway.resolve_owner(frame.get("user_id")),
                session_id=str(frame.get("session_id") or "default"),
            )
        except Exception as e:
            result = {"status": "error", "tool": frame.get("tool"),
                      "error": f"网关异常: {type(e).__name__}: {e}"}
        await self._deliver(src, make_frame(
            "i", FRAME_PLATFORM_CALL_RESPONSE, req=req, result=result))

    async def _fanout(self, frame: dict):
        from core.agent.protocol import FRAME_BUS_MESSAGE, make_frame
        channel = str(frame.get("channel", ""))
        msg_frame = make_frame("i", FRAME_BUS_MESSAGE,
                               channel=channel,
                               message=frame.get("message") or {},
                               frm=frame.get("src"))
        for aid in list(self._subscribers.get(channel, ())):
            if aid == frame.get("src"):
                continue
            await self._deliver(aid, msg_frame)

    async def _deliver(self, agent_id: str, frame: dict) -> bool:
        """把输入帧写入目标子进程 stdin。"""
        proc = self._processes.get(agent_id)
        if proc is None or proc.returncode is not None or proc.stdin is None:
            return False
        try:
            proc.stdin.write((json.dumps(frame, ensure_ascii=False) + "\n").encode())
            await proc.stdin.drain()
            return True
        except Exception:
            return False

    async def _proxy_call(self, src: str, frame: dict):
        """转发 RPC 请求到目标子进程；失败/超时回结构化错误帧。"""
        from core.agent.protocol import FRAME_AGENT_CALL, FRAME_AGENT_REPLY, make_frame
        req = frame.get("req")
        to = str(frame.get("to", ""))
        timeout = float(frame.get("timeout") or 60)
        timeout = min(max(timeout, 1.0), 300.0)
        if to == src:
            result = {"ok": False, "error": "不能调用自身"}
        elif not self.is_running(to):
            result = {"ok": False, "error": f"目标 Agent '{to}' 未运行"}
        else:
            fwd = make_frame("i", FRAME_AGENT_CALL, req=req,
                             frm=src, method=frame.get("method"),
                             params=frame.get("params") or {})
            # 先登记再投递：应答帧可能在投递后立即回到本循环，
            # 若彼时 pending 尚未登记，应答会被当作无主帧丢弃、误报超时。
            loop = asyncio.get_event_loop()
            fut = loop.create_future()
            self._bus_pending[req] = (src, loop, fut, to)
            if not await self._deliver(to, fwd):
                self._bus_pending.pop(req, None)
                result = {"ok": False, "error": f"目标 Agent '{to}' 通道不可用"}
            else:
                try:
                    result = await asyncio.wait_for(fut, timeout=timeout)
                except asyncio.TimeoutError:
                    result = {"ok": False, "error": f"调用 {to} 超时（{timeout:g}s）"}
                except Exception as e:
                    result = {"ok": False, "error": str(e)}
                finally:
                    self._bus_pending.pop(req, None)
        await self._deliver(src, make_frame(
            "i", "agent_call_response", req=req, **result))

    async def call_agent(self, from_agent: str, to: str, method: str,
                         params: dict | None = None, timeout: float = 60) -> dict:
        """主进程侧发起 RPC（供 API 端点与测试使用），语义同子进程侧。"""
        from core.agent.protocol import FRAME_AGENT_CALL, make_frame
        if not self.is_running(to):
            return {"ok": False, "error": f"目标 Agent '{to}' 未运行"}
        req = self._next_req()
        fwd = make_frame("i", FRAME_AGENT_CALL, req=req, frm=from_agent,
                         method=method, params=dict(params or {}))
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._bus_pending[req] = (from_agent, loop, fut, to)
        if not await self._deliver(to, fwd):
            self._bus_pending.pop(req, None)
            return {"ok": False, "error": f"目标 Agent '{to}' 通道不可用"}
        try:
            return await asyncio.wait_for(fut, timeout=min(max(timeout, 1.0), 300.0))
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"调用 {to} 超时"}
        finally:
            self._bus_pending.pop(req, None)

    async def bus_publish(self, channel: str, message: dict | None = None,
                          src: str = "main") -> dict:
        """主进程侧发布（API 端点/外部系统注入总线消息），返回投递统计。"""
        from core.agent.protocol import FRAME_BUS_MESSAGE, make_frame
        channel = str(channel)
        frame = make_frame("i", FRAME_BUS_MESSAGE, channel=channel,
                           message=message or {}, frm=src)
        delivered, failed = [], []
        for aid in list(self._subscribers.get(channel, ())):
            (delivered if await self._deliver(aid, frame) else failed).append(aid)
        return {"channel": channel, "delivered": delivered, "failed": failed}

    def bus_status(self) -> dict:
        """订阅表快照（调试/可观测用）。"""
        return {"channels": {c: sorted(s) for c, s in self._subscribers.items()},
                "pending": len(self._bus_pending)}

    @staticmethod
    def _resolve_impl_path(agent_id: str) -> str | None:
        """在「内置 + 私有」资源根中定位 Agent 实现目录（相对 PROJECT_ROOT 的路径）。

        返回第一个含 agent.py 的实现目录；均不存在时返回 None。
        """
        from core.resource import paths as _paths

        for root in _paths.resource_roots():
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
            )
            self._processes[agent_id] = proc
            # 绑定后台 reader：普通事件入输出队列，通信帧走总线路由。
            # 此后**不允许**再直接读 proc.stdout——会与 reader 争抢字节流。
            self._impl_paths[agent_id] = impl_path
            self._bind_reader(agent_id, proc)

            # 等待子进程初始化
            await asyncio.sleep(1.0)

            if proc.returncode is not None:
                # 进程已退出 → 读取 reader 已收事件 + stderr 获取错误信息
                stderr_text = ""
                try:
                    err_data = await proc.stderr.read(4096)
                    stderr_text = err_data.decode(errors="replace") if err_data else ""
                except Exception:
                    stderr_text = ""
                stdout_text = ""
                try:
                    q = self._outputs.get(agent_id)
                    if q is not None:
                        try:
                            ev = await asyncio.wait_for(q.get(), timeout=1.0)
                            if ev:
                                stdout_text = json.dumps(ev, ensure_ascii=False)
                        except asyncio.TimeoutError:
                            pass
                except Exception:
                    pass
                detail = (stdout_text + "\n" + stderr_text)[:300].strip() or "进程异常退出"
                await self._drop_process(agent_id)
                return {"agent_id": agent_id, "status": "failed", "running": False, "error": detail}

            # 仅对 web-service 类型尝试捕获 service_ready 事件（从输出队列取，
            # 取不到不阻塞——事件留在队列里，后续 execute_stream 照常读到）。
            if self._is_web_service(agent_id, impl_path):
                await self._capture_service_ready(agent_id)

            result = {"agent_id": agent_id, "status": "started", "running": True}
            if agent_id in self._services:
                result["service"] = self._services[agent_id]
            return result
        except Exception as e:
            return {"agent_id": agent_id, "status": "failed", "error": str(e)}

    def _is_web_service(self, agent_id: str, impl_path: str | None = None) -> bool:
        """判断该 Agent 是否为 web-service 类型（读取其实现代码中的 role 声明）。"""
        try:
            if impl_path is None:
                impl_path = self._resolve_impl_path(agent_id) or f"resources/agents/implementations/{agent_id}"
            agent_file = PROJECT_ROOT / impl_path / "agent.py"
            if not agent_file.exists():
                return False
            code = agent_file.read_text(encoding="utf-8")
            return "AgentRole.WEB_SERVICE" in code or "web-service" in code or "web_service" in code
        except Exception:
            return False

    async def _capture_service_ready(self, agent_id: str):
        """从输出队列捕获 web-service 的 service_ready 事件。

        短超时；非 service_ready 事件按原顺序放回队列（后续 execute_stream
        照常读到），失败不影响启动流程。
        """
        if agent_id in self._services:
            return
        q = self._outputs.get(agent_id)
        if q is None:
            return
        others: list = []
        try:
            ev = await asyncio.wait_for(q.get(), timeout=2.0)
            if ev and ev.get("event") != "service_ready":
                others.append(ev)
                try:
                    ev2 = await asyncio.wait_for(q.get(), timeout=1.0)
                    if ev2 and ev2.get("event") == "service_ready":
                        ev = ev2
                    elif ev2:
                        others.append(ev2)
                except asyncio.TimeoutError:
                    pass
            if ev and ev.get("event") == "service_ready":
                self._services[agent_id] = {
                    "host": ev["data"].get("host", "127.0.0.1"),
                    "port": ev["data"].get("port"),
                    "apis": ev["data"].get("apis", []),
                }
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass
        for o in others:
            q.put_nowait(o)

    async def _drop_process(self, agent_id: str):
        """进程死亡后的收尾：取消 reader、清订阅/悬挂请求、唤醒等待者。"""
        self._processes.pop(agent_id, None)
        self._services.pop(agent_id, None)
        self._cleanup_agent_routes(agent_id)

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
        for root in _paths.resource_roots():
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

        # 无条件放入哨兵：优雅退出路径下 reader 的 finally 可能晚于 proc.wait()
        # 执行，若无此哨兵，execute_stream 会空等到空闲超时。新 start 会替换队列，
        # 残留哨兵不会泄漏到下一代。
        q = self._outputs.get(agent_id)
        if q is not None:
            q.put_nowait(None)

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
        # 路由清理：reader 的 finally 在优雅退出路径下会因代际检查跳过清理
        # （proc 已被 pop），必须由 stop 收尾，否则订阅表/队列/任务泄漏。
        self._cleanup_agent_routes(agent_id)
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
        q = self._outputs.get(agent_id)
        if proc is None or proc.returncode is not None or q is None:
            yield {"event": "error", "data": {"message": f"Agent '{agent_id}' 未运行"}}
            return

        # 同一 Agent 的子进程一次只跑一个 execute 轮次：并发请求排队执行，
        # 避免两路调用的事件流互相穿插（与旧 readline 独占读的行为等价）。
        lock = self._exec_locks.setdefault(agent_id, asyncio.Lock())
        if lock.locked():
            yield {"event": "notice",
                   "data": {"message": "该 Agent 正在执行其他任务，已排队等待"}}
        async with lock:
            proc = self._processes.get(agent_id)
            q = self._outputs.get(agent_id)
            if proc is None or proc.returncode is not None or q is None:
                yield {"event": "error", "data": {"message": f"Agent '{agent_id}' 未运行"}}
                return
            # 丢弃上一轮超时后残留的过期事件，保证本轮从干净状态开始
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break

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

            # 从后台 reader 的输出队列取事件（reader 已把通信帧分流给总线路由，
            # 队列里只有普通事件与 None 哨兵——协议帧不会混入 SSE 流）。
            #
            # 超时语义：
            #   - timeout=None  → 不设总超时（长耗时工具，如模型训练，必须支持）。
            #   - timeout>0     → 总时长上限，超出后向调用方报超时并终止子进程。
            # 空闲超时：训练类任务可能数分钟不产生中间输出，给予较大容忍度。
            deadline = (time.time() + timeout) if timeout else None
            idle_timeout = max(600.0, float(timeout)) if timeout else 3600.0
            while proc.returncode is None:
                if deadline is not None and time.time() > deadline:
                    yield {"event": "error", "data": {"message": f"Agent 执行超时 ({int(timeout)}s)"}}
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    return
                try:
                    event = await asyncio.wait_for(q.get(), timeout=idle_timeout)
                except asyncio.TimeoutError:
                    yield {"event": "error",
                           "data": {"message": f"Agent 长时间无响应（{int(idle_timeout)}s 无输出）"}}
                    break
                except Exception:
                    break
                if event is None:  # 进程已退出（reader 哨兵）
                    break
                yield event
                # done 事件到达即表示本轮执行结束，立即返回，
                # 避免继续阻塞读取等待下一轮输入。
                if isinstance(event, dict) and event.get("event") == "done":
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
