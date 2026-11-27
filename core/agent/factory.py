"""Agent 工厂 — 异步子进程生命周期管理"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import AsyncGenerator

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 子进程槽位被残留轮次占用时回的错误前缀（与 core/agent/entrypoint.py 一致，
# 用精确前缀匹配，避免把工具输出里恰好含「正忙」的普通错误误判成卡死）
BUSY_MESSAGE_PREFIX = "Agent 正忙（上一轮 execute 尚未结束）"


def _runtime_limit(name: str, default: int) -> int:
    """读取运行时限流配置（每次现读环境变量，改配置后无需重启即生效）。

    非法值回退默认值；返回 0 表示不限制。
    """
    try:
        return max(0, int(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


def _role_from_spec(impl_dir: Path | None, agent_id: str) -> str | None:
    """从规范文档读取 Agent 角色（与 entrypoint 同源），读不到返回 None。

    ## 为什么必须读 MD 而不是猜代码

    entrypoint 真正决定"要不要起 HTTP 服务"时，读的就是这份文档的 ``role:``
    字段（``core/agent/entrypoint.py:62``）。而 factory 原先按 ``agent.py`` 里的
    关键字（"web-service" / "AgentRole.WEB_SERVICE"）猜角色 —— 用户自建的
    Agent 通常把 role 写在 MD、代码里一个相关字面量都没有，于是：

        entrypoint: 起了 HTTP 服务，发了 service_ready
        factory   : 判定"不是 web-service"，从不去等这个事件
        ⇒ _services 永远为空，进程在但界面永远显示"启动中"

    文档检索顺序与 entrypoint 一致：``<impl>/spec.md`` → 各资源根的
    ``agents/definitions/<id>.md``（私有根优先）。
    """
    import re as _re

    candidates: list[Path] = []
    if impl_dir is not None:
        candidates.append(impl_dir / "spec.md")
        # 同根定义目录：<root>/agents/implementations/<id>/ → <root>/agents/definitions/<id>.md
        # 这比全局扫 resource_roots() 更可靠：多用户部署下私有根是按用户隔离的
        # （/root/SB-USER/<uid>），主进程当前用户未必是 Agent 的归属者。
        try:
            candidates.append(impl_dir.parent.parent / "definitions" / f"{agent_id}.md")
        except Exception:
            pass
    try:
        from core.resource import paths as _paths

        candidates.extend(
            r / "agents" / "definitions" / f"{agent_id}.md"
            for r in _paths.resource_roots()
        )
    except Exception:
        pass

    for f in candidates:
        try:
            if not f.is_file():
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        m = _re.search(r"^\s*role\s*:\s*([a-zA-Z_\-]+)", text, _re.MULTILINE)
        if not m:
            m = _re.search(r"[-*]\s*role\s*[:：]\s*([a-zA-Z_\-]+)", text)
        if m:
            return m.group(1).strip().lower().replace("_", "-")
    return None


def _role_from_code(agent_file: Path) -> str | None:
    """从 Agent 类构造里的 ``role=AgentRole.XXX`` 读取角色（内置 Agent 的权威来源）。

    内置 Agent（`interactive_agent`、`city-info-query-agent` 等）不依赖 MD：
    它们在 `__init__` 里自己构造 ``AgentSpec(role=AgentRole.INTERACTIVE)``，
    运行时 ``agent.role`` 就取自这里。

    **只匹配赋值形态**（`role=AgentRole.X` / `role = AgentRole.X`），
    不会把 ``if role == "web-service":`` 这类比较语句误判成声明 ——
    interactive_agent 代码里正好有这句，靠关键字匹配就会把它错认成 Web 服务式。
    """
    try:
        code = agent_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    m = re.search(r"role\s*=\s*AgentRole\.([A-Za-z_]+)", code)
    if not m:
        return None
    mapping = {
        "WEB_SERVICE": "web-service",
        "INTERACTIVE": "interactive",
        "TASK": "task",
    }
    return mapping.get(m.group(1).upper())


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
        # 启动期由子进程上报的错误（如 spec/代码加载失败、端口被占）：
        # {agent_id: message}。start() 把它带回响应，避免"点了启动没反应"。
        self._startup_errors: dict[str, str] = {}
        # 后台补探任务：{agent_id: asyncio.Task}。慢启动 Agent 的就绪事件晚于
        # start() 的短等待，由它继续等；stop/重启/进程退出时必须取消，否则悬挂。
        self._service_probe_tasks: dict[str, asyncio.Task] = {}
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
        # 帧身份登记表：{agent_id: 最近一次 execute 投递时的 user_id}。
        # platform_call / workspace_audit 帧的身份以此为准——帧经子进程
        # stdout 传回，子进程代码可任意伪造帧内 user_id，不可信。
        # 保留上一轮值不清理：重启窗口/常驻场景下仍是最新的服务端事实。
        self._exec_uid: dict[str, str] = {}
        # ── 防卡死：轮次中断状态 ──
        # 子进程对 cancel 的回执（由 _route_reader 拦截，不进事件队列，
        # 避免与并发的 execute_stream 争抢同一个事件）
        self._round_acks: dict[str, asyncio.Event] = {}
        self._round_cleared: dict[str, bool] = {}
        # 心跳探活：等待 pong 的信号（由 _route_reader 拦截 pong 后置位）
        self._pong_events: dict[str, asyncio.Event] = {}
        # 正在中断中的 Agent，防止停止/超时/断开多路并发重复重启
        self._interrupt_tasks: dict[str, asyncio.Task] = {}
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
                    elif obj.get("event") == "round_cancelled":
                        # cancel 回执单独留信号：中断流程在本协程等待，
                        # 不能塞进事件队列被其他 execute_stream 消费掉。
                        self._round_cleared[agent_id] = bool(
                            obj.get("data", {}).get("cleared"))
                        ev = self._round_acks.get(agent_id)
                        if ev is not None:
                            ev.set()
                    elif obj.get("event") == "pong":
                        ev = self._pong_events.get(agent_id)
                        if ev is not None:
                            ev.set()
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
        # 进程没了就不可能再回 cancel 回执：唤醒中断流程，立刻转强制路径
        ack = self._round_acks.pop(agent_id, None)
        if ack is not None:
            ack.set()
        pong = self._pong_events.pop(agent_id, None)
        if pong is not None:
            pong.set()

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
        elif t == "workspace_audit":
            # 子进程上报工作空间工具生命周期事件 → 主进程写 chat_ops。
            # 审计是短 SQL，直接同步落库；失败只打印，绝不影响事件路由。
            self._handle_workspace_audit(frame, src)

    def _handle_workspace_audit(self, frame: dict, src: str = ""):
        """把子进程的工作空间工具生命周期事件写入审计表（chat_ops）。

        审计唯一入口在主进程（core.chat_control.audit 模块注释）。审计归属
        以主进程登记表（execute 投递时写入的本轮 user_id）为准——帧内
        owner 字段经子进程可写的 stdout 通道传回，不可信，仅作为无登记
        记录时的回退。归属不符不丢事件：照常落库并在 error 标注伪造嫌疑，
        审计的可追溯性优先于拦截。
        """
        try:
            from core.chat_control import audit
            claimed = str(frame.get("owner") or "default")
            bound = self._exec_uid.get(src) if src else None
            owner = bound or claimed
            forged = ""
            if bound and claimed and claimed != bound:
                forged = f"[身份告警] 帧内 owner={claimed} 与执行身份 {bound} 不符（疑似伪造）"
                print(f"[agent_factory] workspace_audit {forged}")
            audit.record(
                op_id=str(frame.get("op_id") or ""),
                session_id=str(frame.get("session_id") or "default"),
                owner=owner,
                tool=str(frame.get("tool") or ""),
                args=frame.get("args") or {},
                risk=str(frame.get("risk") or "write"),
                status=str(frame.get("status") or ""),
                result=frame.get("result"),
                error="; ".join(x for x in (str(frame.get("error") or ""), forged) if x),
                duration_ms=frame.get("duration_ms"),
                uid=owner,
            )
        except Exception as e:
            print(f"[agent_factory] workspace_audit 落库失败: {e}")

    async def _handle_platform_call(self, src: str, frame: dict):
        """把子进程的 platform_call 帧交给 core.chat_control 网关并回执结果。

        身份以主进程登记表（execute 投递时写入的本轮 user_id）为准，由
        网关注入 contextvar，因此对话内的权限边界与手工操作一致。帧内
        user_id 仅作交叉校验：与登记不符即判定伪造并拒绝。异常一律转成
        结构化错误回给子进程，绝不让读循环侧的异常逃逸。
        """
        from core.agent.protocol import FRAME_PLATFORM_CALL_RESPONSE, make_frame
        req = frame.get("req")
        try:
            from core.chat_control import gateway
            claimed = str(frame.get("user_id") or "").strip()
            bound = self._exec_uid.get(src)
            if bound:
                if claimed and claimed not in gateway.PLACEHOLDER_IDENTITIES and claimed != bound:
                    print(f"[agent_factory] platform_call 身份伪造: {src} 自报 uid={claimed}，"
                          f"实际执行 uid={bound}，已拒绝")
                    result = {"status": "error", "tool": frame.get("tool"),
                              "error": "平台调用身份与当前执行轮次不符，已拒绝"}
                else:
                    result = await gateway.platform_call(
                        str(frame.get("tool", "")), frame.get("args") or {},
                        owner=bound,
                        session_id=str(frame.get("session_id") or "default"),
                    )
            else:
                # 无登记 = 该 Agent 从未在本主进程执行过任务，不存在
                # 「发起对话的用户」；回退帧内声明或实例身份（匿名单用户
                # 部署中即为超管，与 REST 路径同口径）。
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
            # 被调侧身份覆写取主进程登记表（调用方最近执行轮的 uid），
            # 不透传帧内自报的 user_id——stdout 通道子进程可任意伪造。
            fwd = make_frame("i", FRAME_AGENT_CALL, req=req,
                             frm=src, method=frame.get("method"),
                             params=frame.get("params") or {},
                             user_id=self._exec_uid.get(src) or frame.get("user_id"))
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
                         params: dict | None = None, timeout: float = 60,
                         caller_uid: str | None = None) -> dict:
        """主进程侧发起 RPC（供 API 端点与测试使用），语义同子进程侧。

        caller_uid 是服务端判定的调用者身份，注入帧的 user_id 字段供被调侧
        覆写执行身份——请求体自报的 from 只是会话标签，不参与身份。
        """
        from core.agent.protocol import FRAME_AGENT_CALL, make_frame
        if not self.is_running(to):
            return {"ok": False, "error": f"目标 Agent '{to}' 未运行"}
        req = self._next_req()
        fwd = make_frame("i", FRAME_AGENT_CALL, req=req, frm=from_agent,
                         method=method, params=dict(params or {}),
                         user_id=caller_uid)
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
                          src: str = "main",
                          can_deliver=None) -> dict:
        """主进程侧发布（API 端点/外部系统注入总线消息），返回投递统计。

        can_deliver: 可选异步谓词 `async (agent_id) -> bool`。API 端点传入
        按调用者可见性的过滤——他人私有 Agent 不收（delivered/failed 也不
        列出，避免泄露其存在）。不传则全量扇出（主进程内部/Agent 间路由）。
        """
        from core.agent.protocol import FRAME_BUS_MESSAGE, make_frame
        channel = str(channel)
        frame = make_frame("i", FRAME_BUS_MESSAGE, channel=channel,
                           message=message or {}, frm=src)
        delivered, failed = [], []
        for aid in list(self._subscribers.get(channel, ())):
            if can_deliver is not None and not await can_deliver(aid):
                continue
            (delivered if await self._deliver(aid, frame) else failed).append(aid)
        return {"channel": channel, "delivered": delivered, "failed": failed}

    def bus_status(self) -> dict:
        """订阅表快照（调试/可观测用）。"""
        return {"channels": {c: sorted(s) for c, s in self._subscribers.items()},
                "pending": len(self._bus_pending)}

    @staticmethod
    def _resolve_impl_path(agent_id: str) -> str | None:
        """在「私有 + 共享 + 内置」资源根中定位 Agent 实现目录（相对 PROJECT_ROOT 的路径）。

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

    @staticmethod
    def _agent_owner_uid(agent_id: str) -> str:
        """Agent 的产物归属用户：注册条目 owner，查不到归系统用户。

        Agent 子进程常驻后会被多个用户复用，启动时注入的 env 不能取「谁点的
        启动」——否则第一个启动者（可能是超管 0）会成为所有用户的产物目录。
        注册 owner 是稳定语义：公共 Agent 一律 100，用户私有 Agent 是其创建者。
        """
        from core.user_paths import SYSTEM_UID

        try:
            from core.resource.registry.agent_registry import AgentRegistry

            for entry in AgentRegistry()._read():
                if entry.get("id") == agent_id:
                    return str(entry.get("owner") or SYSTEM_UID)
        except Exception:
            pass
        return SYSTEM_UID

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

        # Agent 子进程环境：显式注入系统根与**归属用户**目录。
        # 模板里用 Path(__file__).parents[N] 猜项目根在私有根下会算错
        # （/root/resources/agents/logs 事故根因），统一改为信 env。
        # cwd 同样定为归属目录：Agent 代码里的相对路径不能落进 git 工作区。
        #
        # 关键：这里注入的是注册 owner，不是「点启动的人」。进程会常驻并被
        # 多个用户复用，用启动者身份会让所有回合的初始值都指向第一个用户。
        # 每轮真实身份由 entrypoint 用 contextvar 覆写（见 set_exec_uid）。
        from core.user_paths import runtime_env, user_home

        _owner = self._agent_owner_uid(agent_id)
        _home = user_home(_owner)
        _home.mkdir(parents=True, exist_ok=True)
        _proc_env = {**os.environ, **runtime_env({
            "SOTABAND_OWNER_UID": _owner,
            "SOTABAND_USER_HOME": str(_home),
            "SOTABAND_USER_ID": _owner,
        })}

        # 新一代启动：清掉上一代残留的启动错误与后台补探任务
        self._startup_errors.pop(agent_id, None)
        self._cancel_service_probe(agent_id)

        try:
            proc = await asyncio.create_subprocess_exec(
                python, entrypoint,
                "--agent-id", agent_id,
                "--impl-path", str(agent_file.parent),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(_home),
                env=_proc_env,
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

            # 仅对 web-service 类型等待 service_ready。
            # 快 Agent（1~2s 就绪）在短等待内直接回报；慢 Agent（import torch /
            # 加载 RKNN 模型）转后台继续等，启动接口不跟着卡十几秒，
            # 前端通过轮询 /service 自然转成"运行中"。
            if self._is_web_service(agent_id, impl_path):
                await self._wait_service_ready(agent_id, timeout=2.0)
                if agent_id not in self._services and self.is_process_alive(agent_id):
                    self._cancel_service_probe(agent_id)
                    self._service_probe_tasks[agent_id] = asyncio.create_task(
                        self._await_service_ready_bg(agent_id, timeout=20.0))

            result = {"agent_id": agent_id, "status": "started", "running": True}
            if agent_id in self._services:
                result["service"] = self._services[agent_id]
            startup_error = self._startup_errors.get(agent_id)
            if startup_error:
                result["startup_error"] = startup_error
            return result
        except Exception as e:
            return {"agent_id": agent_id, "status": "failed", "error": str(e)}

    def _is_web_service(self, agent_id: str, impl_path: str | None = None) -> bool:
        """判断该 Agent 是否为 web-service 类型。

        判定必须与 entrypoint **同源**：先读规范文档的 ``role:``（见
        :func:`_role_from_spec`），读不到才退回"代码里有没有关键字"的老办法。
        否则用户自建 Agent（role 只在 MD 里）会被判成非 web-service，factory
        就不去等 ``service_ready``，界面会永远停在"启动中"。
        """
        try:
            if impl_path is None:
                impl_path = self._resolve_impl_path(agent_id) or f"resources/agents/implementations/{agent_id}"
            impl_dir = PROJECT_ROOT / impl_path
            agent_file = impl_dir / "agent.py"

            # 1) 规范文档（用户自建 Agent 的权威来源，与 entrypoint 一致）
            role = _role_from_spec(impl_dir, agent_id)
            # 2) 构造函数里的 role=AgentRole.XXX（内置 Agent 的权威来源）
            if role is None:
                role = _role_from_code(agent_file)
            if role is not None:
                return role == "web-service"

            # 3) 兜底：既没有规范文档也没有显式 role 时，才按关键字猜
            if not agent_file.exists():
                return False
            code = agent_file.read_text(encoding="utf-8")
            return "AgentRole.WEB_SERVICE" in code or "web-service" in code or "web_service" in code
        except Exception:
            return False

    async def _wait_service_ready(self, agent_id: str, timeout: float = 15.0) -> dict | None:
        """限时轮询 ``service_ready``，返回服务信息；超时 / 进程退出返回 None。

        ## 为什么必须轮询而不是"读一次"

        web-service Agent 的 ``on_start`` 里常要 import torch、加载 RKNN/ONNX
        模型，``service_ready`` 往往 3~15s 才上报。旧实现只等 2s+1s 就判空，
        于是 ``_services`` 永远为空 —— 表现为「进程已经起来了，界面却一直显示
        未运行」，刷新页面也没用（后端从未记录过）。这段逻辑此前在
        ``f7ee390`` 上修过，但没合入 main，属于合并丢失。

        ## 契约（改这里前务必读）

        - **非 service_ready 事件按原顺序回队**：它们是 execute_stream 的输入，
          吞掉会表现为"每一步输入都要等很久"或消息丢失；
        - ``error`` 事件记入 ``_startup_errors``，由 ``start()`` 带回响应；
        - ``host`` 一律收敛为回环地址 —— 子进程自报的 host 不可信，否则
          ``/test-api`` 的代理会变成任意 host:port 的 SSRF；
        - 收到 ``None`` 哨兵（EOF）或进程已退出立即返回，不空等。
        """
        if agent_id in self._services:
            return self._services[agent_id]

        q = self._outputs.get(agent_id)
        if q is None:
            return None

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        buffered: list = []
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return None
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=min(remaining, 1.0))
                except asyncio.TimeoutError:
                    proc = self._processes.get(agent_id)
                    if proc is not None and proc.returncode is not None:
                        return None          # 进程已退出，再等也没有事件了
                    continue

                if ev is None:               # EOF 哨兵
                    return None

                name = ev.get("event")
                if name == "service_ready":
                    data = ev.get("data") or {}
                    svc = {
                        "host": "127.0.0.1",
                        "port": data.get("port"),
                        "apis": data.get("apis", []),
                    }
                    self._services[agent_id] = svc
                    return svc

                if name == "error":
                    message = str((ev.get("data") or {}).get("message") or "").strip()
                    if message:
                        self._startup_errors[agent_id] = message

                buffered.append(ev)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        finally:
            # 无论正常返回、超时还是取消，都要把读过的事件放回去
            for item in buffered:
                q.put_nowait(item)

    async def _await_service_ready_bg(self, agent_id: str, timeout: float = 20.0) -> None:
        """后台补探：start() 已返回后继续等服务就绪，前端轮询即可看到 running。

        慢 Agent 不该让"启动"按钮卡十几秒，所以短等待没拿到就转到这里。
        """
        try:
            await self._wait_service_ready(agent_id, timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            # 只清理"自己这一代"，避免重启后新任务被旧任务误删
            if self._service_probe_tasks.get(agent_id) is asyncio.current_task():
                self._service_probe_tasks.pop(agent_id, None)

    def _cancel_service_probe(self, agent_id: str) -> None:
        """取消该 Agent 的后台补探任务（stop / 重启 / 进程退出时调用）。"""
        task = self._service_probe_tasks.pop(agent_id, None)
        if task is not None and not task.done():
            task.cancel()

    def is_process_alive(self, agent_id: str) -> bool:
        """子进程是否仍在运行。

        用于区分「启动中」与「未运行」：web-service Agent 起来但还没上报
        service_ready 时，进程在、服务未就绪 —— 界面应显示"启动中"。
        """
        proc = self._processes.get(agent_id)
        return proc is not None and proc.returncode is None

    async def _drop_process(self, agent_id: str):
        """进程死亡后的收尾：取消 reader、清订阅/悬挂请求、唤醒等待者。"""
        self._processes.pop(agent_id, None)
        self._services.pop(agent_id, None)
        self._startup_errors.pop(agent_id, None)
        self._cancel_service_probe(agent_id)
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

    async def stop(self, agent_id: str, graceful_timeout: float = 20) -> dict:
        """优雅停止 Agent 子进程。

        顺序：发送 stop 命令 → 关闭 stdin（触发 Agent 主循环退出并执行清理，
        如回收常驻工具进程 / 卸载模型）→ 等待其自行退出 → 超时才强制终止。

        注意：**不能**在发送 stop 后立刻 terminate()——那样 Agent 子进程
        来不及执行 on_stop() 清理，常驻工具进程与模型资源将无法被优雅释放。

        graceful_timeout 可缩短：已探活确认事件循环卡死时（force 路径），
        它不可能再执行任何清理，等满 20 秒只会让用户多等 20 秒。
        """
        proc = self._processes.pop(agent_id, None)
        # 清理 web-service 服务记录 + 后台补探（否则任务会一直等一个已死的进程）
        self._services.pop(agent_id, None)
        self._startup_errors.pop(agent_id, None)
        self._cancel_service_probe(agent_id)
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
                await asyncio.wait_for(proc.wait(), timeout=graceful_timeout)
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

    async def restart(self, agent_id: str, graceful_timeout: float = 20) -> dict:
        """重启 Agent 子进程。

        优先复用 start() 记录的实现目录：按 id 重新搜索会在「私有根位于项目
        之外」「以自定义路径启动」时定位到别的目录（甚至搜不到），导致防卡死
        的强制重启把 Agent 拉起成另一个实现或直接失败。
        """
        await self.stop(agent_id, graceful_timeout=graceful_timeout)
        await asyncio.sleep(0.5)

        impl_path = self._impl_paths.get(agent_id) or self._resolve_impl_path(agent_id)
        result = await self.start(agent_id, impl_path)
        if result.get("status") == "failed":
            # 记录的目录已失效（目录被移动/删除）→ 退回按 id 重新定位
            fallback = self._resolve_impl_path(agent_id)
            if fallback and fallback != impl_path:
                result = await self.start(agent_id, fallback)
        return result

    def _ensure_interrupt_task(
        self, agent_id: str, reason: str, restart: bool, force: bool = False
    ) -> asyncio.Task | None:
        """返回该 Agent 的中断任务；已有进行中的则复用，避免多路并发重复重启。

        任务保存在字典里持有强引用：调用点常在生成器被取消（客户端断开）
        时的 finally 中，那里不能 await（会立刻被 CancelledError 打断），
        只能投递后立刻返回。

        force=True 跳过温和取消直接强制重启——用于探活已判定事件循环卡死、
        取消指令根本读不到的场景，省掉一个取消等待窗口。
        """
        running = self._interrupt_tasks.get(agent_id)
        if running is not None and not running.done():
            return running
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        task = loop.create_task(
            self._interrupt_round_impl(agent_id, reason, restart, force))
        self._interrupt_tasks[agent_id] = task
        return task

    def start_interrupt(
        self, agent_id: str, reason: str = "", restart: bool = True
    ) -> "asyncio.Task | None":
        """投递中断任务并立即返回该任务（不等待完成）。

        供 HTTP 接口使用：温和取消通常瞬时完成，但强制重启要等优雅退出，
        接口不该为此挂住，可自行 `wait_for(shield(task), 小超时)` 决定等多久。
        """
        return self._ensure_interrupt_task(agent_id, reason, restart)

    async def _probe_alive(self, agent_id: str, proc) -> bool:
        """心跳探活：子进程事件循环是否还能响应指令。

        区分两种「无输出」：
          - 循环活着、只是本轮在等长耗时工具 → 回 pong，可继续耐心等待；
          - 循环本身卡死（同步网络调用/阻塞线程占满循环，连指令都读不到）
            → 无 pong，此时温和取消也必然无效，只能立即强制重启。
        没有这一步，后者要等到空闲上限（默认 1 小时）才被发现。
        """
        ev: asyncio.Event = asyncio.Event()
        self._pong_events[agent_id] = ev
        try:
            proc.stdin.write((json.dumps({"action": "ping"}) + "\n").encode())
            await proc.stdin.drain()
        except Exception:
            self._pong_events.pop(agent_id, None)
            return False
        try:
            await asyncio.wait_for(ev.wait(), timeout=10)
            return True
        except asyncio.TimeoutError:
            return False
        except Exception:
            return False
        finally:
            self._pong_events.pop(agent_id, None)

    async def _await_interrupt_idle(self, agent_id: str, timeout: float = 20.0):
        """等待该 Agent 正在进行的后台中断收尾。

        中断未结束时子进程仍在往外吐事件（补发的 done、取消后的尾部输出），
        此时投递新轮次会让这些迟到的事件污染本轮事件流。
        """
        task = self._interrupt_tasks.get(agent_id)
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def interrupt_round(
        self, agent_id: str, reason: str = "", restart: bool = True
    ) -> dict:
        """中断该 Agent 正在进行的 execute 轮次（防卡死统一入口）。

        两级手段：
          1. 温和：发 ``{"action":"cancel"}`` 让子进程 cancel 当前任务——
             Agent 代码里的 ``await`` 点（LLM 流、子进程通信）会立刻抛
             CancelledError 释放，正常轮次几毫秒即回。
          2. 强制：cancel 回执超时未到，说明该轮卡在**不可取消**的位置
             （阻塞在 OS 调用、线程、无超时的第三方 SDK），温和手段无效，
             只能终止并重启子进程，否则它永远报「Agent 正忙」。

        返回 ``{"ok": bool, "mode": "cancel"|"restart"|...}``。
        """
        task = self._ensure_interrupt_task(agent_id, reason, restart)
        if task is None:
            return {"agent_id": agent_id, "ok": False, "mode": "no_loop"}
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return {"agent_id": agent_id, "ok": False, "mode": "error",
                    "reason": str(e)}

    async def _interrupt_round_impl(
        self, agent_id: str, reason: str, restart: bool, force: bool = False
    ) -> dict:
        try:
            proc = self._processes.get(agent_id)
            if proc is None or proc.returncode is not None:
                return {"agent_id": agent_id, "ok": True, "mode": "not_running"}

            ack: asyncio.Event = asyncio.Event()
            self._round_acks[agent_id] = ack
            self._round_cleared.pop(agent_id, None)
            cleared = None
            try:
                if not force:
                    try:
                        proc.stdin.write((json.dumps({"action": "cancel"}) + "\n").encode())
                        await proc.stdin.drain()
                    except Exception:
                        ack.set()  # 管道已断：直接走强制路径

                    # 温和取消的等待窗口。子进程回执 data.cleared 表明任务是否真的结束。
                    grace = max(1, _runtime_limit("AGENT_CANCEL_GRACE", 10))
                    try:
                        await asyncio.wait_for(ack.wait(), timeout=grace)
                        cleared = self._round_cleared.get(agent_id)
                    except asyncio.TimeoutError:
                        pass
            finally:
                self._round_acks.pop(agent_id, None)

            if cleared:
                # 子进程确认槽位已释放且仍能读 stdin，无需重启。
                return {"agent_id": agent_id, "ok": True,
                        "mode": "cancel", "reason": reason}

            print(f"[SOTABand] Agent {agent_id} 未能取消轮次（{reason or '未说明原因'}），"
                  f"强制重启子进程", flush=True)
            if not restart:
                return {"agent_id": agent_id, "ok": False, "mode": "stuck",
                        "reason": reason}
            await self.restart(agent_id, graceful_timeout=3 if force else 20)
            return {"agent_id": agent_id, "ok": True, "mode": "restart",
                    "reason": reason}
        finally:
            cur = self._interrupt_tasks.get(agent_id)
            if isinstance(cur, asyncio.Task) and asyncio.current_task() is cur:
                self._interrupt_tasks.pop(agent_id, None)

    async def execute(
        self, agent_id: str, content: str, context: dict | None = None
    ) -> AsyncGenerator[dict, None]:
        """向 Agent 子进程发送任务，流式读取响应（总超时 120s）。

        兼容旧调用：仅传 content。``context`` 用于指定执行身份
        （``user_id``），不传则落到 ``"default"``——调用方应显式传入
        当前登录用户，否则工具产物会写进默认目录而非用户自己的目录。
        """
        async for event in self.execute_stream(
            agent_id, params={"content": content}, context=context
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
            # 可能正处于防卡死的强制重启过程中（stop 与 start 之间的空档）。
            # 等它收尾再判定，否则紧随其后的一条消息会莫名吃到「未运行」。
            await self._await_interrupt_idle(agent_id)
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
            # 上一轮被中断时，子进程会先补发 done、再回 cancel 回执。必须等
            # 中断流程彻底结束再取事件，否则那条迟到的 done 会混进本轮，
            # 让本轮「秒结束」却什么都没有。
            await self._await_interrupt_idle(agent_id)
            # 等待期间中断可能已把子进程强制重启——必须重取句柄，
            # 否则会把 execute 写进已关闭的管道（表现为「进程已断开」）。
            proc = self._processes.get(agent_id)
            q = self._outputs.get(agent_id)
            if proc is None or proc.returncode is not None or q is None:
                yield {"event": "error", "data": {
                    "message": f"Agent '{agent_id}' 刚被重启，请重新发送"}}
                return

            ctx = {"session_id": "default", "user_id": "default", "agent_id": agent_id}
            if context:
                ctx.update(context)
            # 投递前登记本轮执行身份：后续 platform_call / workspace_audit /
            # agent_call 帧的身份一律以此为准（帧内自报字段不可信）。
            self._exec_uid[agent_id] = str(ctx.get("user_id") or "default")

            # 防卡死重试：子进程里若残留一个未结束的轮次（历史上的「Agent 正忙」
            # 死锁），先中断清理一次再重发，用户无需手工重启 Agent。
            # outcome["why"] 初值 interrupted：客户端断开时生成器在 yield 处收到
            # GeneratorExit，循环后的代码不会执行，收尾判断必须放在 finally 里。
            outcome: dict = {"why": "interrupted"}
            try:
                for attempt in (1, 2):
                    # 每轮投递前清空残留事件（迟到的 done / 半截输出）
                    while not q.empty():
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    if not await self._send_execute(proc, ctx, params):
                        outcome["why"] = "exit"
                        yield {"event": "error", "data": {"message": "Agent 进程已断开"}}
                        return

                    async for ev in self._read_round(agent_id, proc, q, timeout, outcome):
                        yield ev
                    if outcome["why"] != "busy" or attempt == 2:
                        if outcome["why"] == "busy":
                            # 清理后仍报正忙（极端情况）：显式报错，
                            # 不能让前端只看到「流结束、无内容」
                            outcome["why"] = "busy_final"
                            yield {"event": "error", "data": {
                                "message": "Agent 仍被上一轮任务占用且无法自动清理，"
                                           "请在 Agent 详情页手动重启"}}
                        break
                    yield {"event": "notice", "data": {
                        "message": "检测到上一轮任务残留，正在自动清理后重试"}}
                    task = self._ensure_interrupt_task(
                        agent_id, "残留轮次自动清理", True)
                    if task is not None:
                        await asyncio.shield(task)
                    proc = self._processes.get(agent_id)
                    q = self._outputs.get(agent_id)
                    if proc is None or proc.returncode is not None or q is None:
                        outcome["why"] = "exit"
                        yield {"event": "error",
                               "data": {"message": f"Agent '{agent_id}' 已重启，请重新发送"}}
                        return
            finally:
                # done / 进程已退出之外的所有结束方式（超时、空闲、客户端断开、
                # 异常）都必须回收子进程里的 execute 槽位——否则它会永久报
                # 「Agent 正忙」。用独立任务执行：此处可能正处在被取消的生成器里。
                if outcome["why"] not in ("done", "exit"):
                    self._schedule_interrupt(
                        agent_id, f"轮次结束于 {outcome['why']}",
                        force=outcome["why"] == "dead")

    async def _send_execute(self, proc, ctx: dict, params: dict | None) -> bool:
        """向子进程投递一轮 execute。管道已断返回 False。"""
        task = {"action": "execute", "context": ctx, "params": dict(params or {})}
        try:
            proc.stdin.write((json.dumps(task) + "\n").encode())
            await proc.stdin.drain()
            return True
        except Exception:
            return False

    async def _read_round(
        self, agent_id: str, proc, q: asyncio.Queue,
        timeout: float | None, outcome: dict,
    ) -> AsyncGenerator[dict, None]:
        """读取一轮 execute 的事件流，结束原因写入 ``outcome["why"]``。

        why 取值：
          done      正常收到 done
          busy      子进程回「Agent 正忙」（槽位被残留轮次占用）
          exit      子进程退出
          timeout   达到轮次硬超时
          dead      长时间无输出且心跳探活失败（事件循环卡死）

        超时语义（均可在系统设置/`.env` 调整，0 表示不限制）：
          - 调用方显式传 timeout → 总时长上限，空闲检查取同一量级；
          - timeout=None（对话链路）→ 用 AGENT_ROUND_TIMEOUT 兜底硬超时；
            每 AGENT_IDLE_TIMEOUT 无输出做一次心跳探活，只有探活也失败
            才判定为卡死并强制重启（长耗时工具不会被误杀）。
        """
        cap = _runtime_limit("AGENT_ROUND_TIMEOUT", 7200)
        total = float(timeout) if timeout else float(cap)
        deadline = (time.time() + total) if total > 0 else None
        # 空闲到点只做「心跳探活」，不直接判死：循环活着就继续等（长耗时工具
        # 数分钟无输出属正常），因此这里可以放心设小，让真正卡死的循环尽快暴露。
        idle_limit = _runtime_limit("AGENT_IDLE_TIMEOUT", 300)
        idle_cap = float(idle_limit) if idle_limit > 0 else 0.0
        if timeout:
            # 调用方限时（如 RPC 60s）时，空闲容忍与总时长同量级
            idle_cap = max(60.0, float(timeout))

        while proc.returncode is None:
            wait = idle_cap
            due = "idle"
            if deadline is not None:
                left = deadline - time.time()
                if left <= 0:
                    outcome["why"] = "timeout"
                    yield {"event": "error", "data": {
                        "message": f"Agent 单轮执行超过 {int(total)} 秒，已自动中断"}}
                    return
                # 单次等待不能越过总时限，否则硬超时要等空闲超时才生效
                if wait <= 0 or left <= wait:
                    wait, due = left, "timeout"
            try:
                event = await asyncio.wait_for(q.get(), timeout=wait or None)
            except asyncio.TimeoutError:
                if due == "timeout":
                    outcome["why"] = "timeout"
                    yield {"event": "error", "data": {
                        "message": f"Agent 单轮执行超过 {int(total)} 秒，已自动中断"}}
                    return
                # 空闲到点先探活，区分两种情况：
                #   循环活着 —— 本轮在等长耗时工具（模型训练等），继续等，
                #             总时限仍然兜底；
                #   循环卡死 —— 温和取消必然无效，立即强制重启，不再空等
                #             到空闲上限（默认 1 小时）。
                if await self._probe_alive(agent_id, proc):
                    continue
                outcome["why"] = "dead"
                yield {"event": "error", "data": {
                    "message": f"Agent 进程已无响应（{int(idle_cap)} 秒无输出且心跳超时），"
                               "正在强制重启"}}
                return
            except Exception:
                outcome["why"] = "exit"
                return
            if event is None:  # 进程已退出（reader 哨兵）
                outcome["why"] = "exit"
                return
            if isinstance(event, dict):
                et = event.get("event")
                if et == "done":
                    outcome["why"] = "done"
                    yield event
                    return
                msg = str((event.get("data") or {}).get("message", ""))
                if et == "error" and msg.startswith(BUSY_MESSAGE_PREFIX):
                    # 不当作最终错误抛给调用方——上层会自动清理残留轮次后重试
                    outcome["why"] = "busy"
                    return
            yield event

    def _schedule_interrupt(self, agent_id: str, reason: str, force: bool = False):
        """投递后台中断任务（不 await）。

        调用点在生成器被取消时的 finally 里——那里 await 会立刻被
        CancelledError 打断，中断动作根本没机会执行。
        """
        try:
            self._ensure_interrupt_task(agent_id, reason, True, force)
        except Exception:
            pass

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
