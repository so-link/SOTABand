"""Agent 子进程入口 — 通过 stdin/stdout JSON-line 协议与主进程通信

用法: python -m core.agent.entrypoint --agent-id xxx --impl-path core/agent/implementations/xxx
"""

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.base import AgentRole  # noqa: E402


def load_agent(impl_path: Path, agent_id: str):
    """从 implementations/{agent-id}/agent.py 加载 Agent 实例"""
    agent_file = impl_path / "agent.py"
    if not agent_file.exists():
        raise FileNotFoundError(f"Agent 代码不存在: {agent_file}")

    spec = importlib.util.spec_from_file_location(f"agent_{agent_id}", agent_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from core.agent.base import BaseAgent, AgentSpec

    # 查找 BaseAgent 子类
    agent_cls = None
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and issubclass(obj, BaseAgent) and obj is not BaseAgent:
            agent_cls = obj
            break

    if agent_cls is None:
        raise ValueError(f"Agent 代码中未找到继承 BaseAgent 的类: {agent_file}")

    # 读取 spec（优先 spec.md；交互 Agent 等内置类型可能没有 spec.md，
    # 此时回退到 definitions/{id}.md，并从 MD 中解析 role）
    # 规范文档检索范围：内置 + 私有资源根。
    spec_file = impl_path / "spec.md"
    raw_md = ""
    if spec_file.exists():
        raw_md = spec_file.read_text(encoding='utf-8')
    else:
        try:
            from core.resource import paths as _paths
            _def_dirs = [r / "agents" / "definitions" for r in _paths.resource_roots()]
        except Exception:
            _def_dirs = [PROJECT_ROOT / "resources" / "agents" / "definitions"]
        for _d in _def_dirs:
            _f = _d / f"{agent_id}.md"
            if _f.exists():
                raw_md = _f.read_text(encoding='utf-8')
                break

    role = _parse_role_from_md(raw_md) if raw_md else AgentRole.TASK
    spec = AgentSpec(id=agent_id, name=agent_id, role=role)
    spec.raw_md = raw_md
    # 解析 task 的 flow / web-service 的 server+apis（失败时保持空值）
    spec.parse_structured_fields()

    # 按构造函数签名实例化：
    # - 部分 Agent（如内置交互 Agent）的 __init__ 只接受 llm_client 等自定义参数，
    #   不接受 spec；若盲目传 spec 会被当作 llm_client，导致 self.spec 错乱。
    # - 因此仅当构造函数显式接受 spec 参数时才传入。
    import inspect

    try:
        params = inspect.signature(agent_cls.__init__).parameters
    except (TypeError, ValueError):
        params = {}

    if "spec" in params:
        return agent_cls(spec)
    return agent_cls()


def _parse_role_from_md(raw_md: str) -> AgentRole:
    """从 MD 规范文档头部的 front-matter / 字段中解析 role。"""
    import re as _re

    m = _re.search(r"^\s*role\s*:\s*([a-zA-Z_\-]+)", raw_md, _re.MULTILINE)
    if not m:
        m = _re.search(r"[-*]\s*role\s*[:：]\s*([a-zA-Z_\-]+)", raw_md)
    if m:
        return AgentRole(m.group(1).strip())
    return AgentRole.TASK


async def main():
    # Windows Proactor 事件循环不支持 connect_read_pipe/write_pipe 挂标准流；
    # 统一强制 UTF-8 行协议，读写都走线程（见下方 send_event / 主读循环）。
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--impl-path", required=True)
    args = parser.parse_args()

    impl_path = Path(args.impl_path)
    if not impl_path.is_absolute():
        impl_path = PROJECT_ROOT / impl_path

    # 加载 Agent — 失败时仍建立协议通道以报告错误
    try:
        agent = load_agent(impl_path, args.agent_id)
        await agent.on_start()
        agent_loaded = True
    except Exception as e:
        import traceback
        load_error = f"Agent 加载失败: {e}\n{traceback.format_exc()}"
        agent = None
        agent_loaded = False

    # 通过 stdin/stdout JSON-line 协议通信。不用 loop 的 pipe transport：
    # Windows Proactor 无法把标准流管道挂进事件循环（Linux 上则是
    # 该实现异常时静默丢事件、Agent 空回复）。写侧包装成 StreamWriter
    # 兼容对象（write 入队 / drain 线程内 flush），bus 与 send_event 共用，
    # 锁保证后台任务与主循环同时发帧时不交错。
    write_lock = asyncio.Lock()
    _pending: list[bytes] = []

    async def _flush_pending():
        # 切分在事件循环内完成（与 write 追加无竞态），字节写出走线程。
        if not _pending:
            return
        data = b"".join(_pending)
        _pending.clear()
        async with write_lock:
            await asyncio.to_thread(_write_now, data)

    def _write_now(data: bytes):
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    class _LineWriter:
        def write(self, data: bytes) -> None:
            _pending.append(data)

        async def drain(self) -> None:
            await _flush_pending()

    writer = _LineWriter()

    async def send_event(event: dict):
        """发送 JSON 行到主进程（立即 flush，不等缓冲区攒满）。"""
        line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            writer.write(line)
            await writer.drain()
        except Exception:
            pass

    # 加载失败 → 通知主进程后退出
    if not agent_loaded:
        await send_event({"event": "error", "data": {"message": load_error[:500]}})
        await send_event({"event": "done", "data": {"messageId": "fatal"}})
        return

    # ── Agent 间通信（todo 4.5）：绑定总线客户端 ──
    # Agent 代码在 execute/on_start 期间可经 self._bus 使用
    # publish / subscribe / call_agent。帧经本通道与主进程交换。
    from core.agent.bus import AgentBusClient
    from core.agent.protocol import is_frame

    bus = AgentBusClient(args.agent_id)
    bus.bind(writer)
    agent._bus = bus

    async def _handle_rpc(from_agent: str, method: str, params: dict,
                          caller_uid: str | None = None) -> dict:
        """被调侧：以 execute 语义执行 RPC，把事件流聚合为一次性响应。

        聚合规则：error 事件 → 失败；result 事件 → 取其 data；
        否则拼接 content 事件文本。RPC 事件不外发，避免混入本 Agent
        正常执行轮次的事件流。

        身份：与 execute 轮同语义——「以发起本轮的用户身份跑」。uid 取
        主进程注入的受信帧字段 user_id（帧内 from 是会话标签不是 uid，
        绝不可信）；缺字段（旧帧）或为空时与 execute 轮一致回退 "0"。
        必须覆写 SOTABAND_USER_ID：共享进程里若不覆写，RPC 触发的工具
        会沿用上一个 execute 轮残留的用户身份落盘——跨用户串号。
        """
        from core.agent.base import AgentContext

        uid = str(caller_uid or "").strip() or "0"
        os.environ["SOTABAND_USER_ID"] = uid
        ctx = AgentContext(agent_id=args.agent_id,
                           session_id=f"rpc:{from_agent}", user_id=uid)
        result = None
        contents: list = []
        err: str | None = None
        # 把调用参数平铺进 kwargs，被调方（如 task 型 Agent）可直接按
        # 自己声明的入参名取用（content 等）；同时保留 bus_method/bus_params
        # 供只需要自定义方法名的 Agent 使用，两者互不影响。
        invoke_kwargs = dict(params or {})
        invoke_kwargs["bus_method"] = method
        invoke_kwargs["bus_params"] = params or {}
        card_summary: str | None = None
        card_payload: dict | None = None
        try:
            async for event in agent.execute(ctx, **invoke_kwargs):
                et = event.get("event")
                if et == "error":
                    err = event.get("data", {}).get("message", "error")
                elif et == "result":
                    result = event.get("data")
                elif et == "content":
                    contents.append(event.get("data", {}).get("text", ""))
                elif et == "delta":
                    # 流式增量同样属于回复正文：Agent 里只要用了 delta（如
                    # InteractiveAgentBase 的对话/整理结果路径），这里不收集
                    # 就会把整段回复丢掉，调用方只剩「未返回文本结果」。
                    contents.append(event.get("data", {}).get("text", ""))
                elif et == "card":
                    # 结果卡是最终答复的载体：InteractiveAgentBase 的工具结果
                    # 只放在 result-summary 卡里，正文留给 LLM 小结。只收集
                    # 正文会让「某次小结没提到结论」的轮次整体丢失答案。
                    d = event.get("data") or {}
                    if d.get("type") == "result-summary":
                        if d.get("summary"):
                            card_summary = str(d["summary"])
                        # 表格/图片/文件结果只存在于卡片的 data.result 里，
                        # 调用方（交互 Agent 派发）要用它渲染结果卡；
                        # 只带 summary 会让对话里只剩一句「成功处理 N 只」。
                        if isinstance(d.get("data"), dict):
                            card_payload = d["data"]
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if err is not None:
            return {"ok": False, "error": err}
        if result is not None:
            return {"ok": True, "result": result}
        text = "".join(contents).strip()
        # 正文为空时才用卡片摘要兜底；有正文（如 Agent 的汇总结论）时再拼摘要，
        # 前端正文与卡片会重复显示同一句「成功处理 N 只」。
        if not text and card_summary:
            text = card_summary
        out: dict = {"text": text}
        if card_payload:
            out["card"] = {"type": "result-summary",
                           "title": "",
                           "summary": card_summary or "",
                           "data": card_payload}
        return {"ok": True, "result": out}

    bus.add_handler("__call__", _handle_rpc)

    # ── Web 服务式：加载成功后自动启动 HTTP 服务 ──
    _web_host = None
    if agent.role == AgentRole.WEB_SERVICE:
        try:
            from core.agent.web_server import WebServiceHost

            srv_cfg = agent.spec.server or {}
            host = srv_cfg.get("host", "127.0.0.1")
            port = int(srv_cfg.get("port") or 0)
            _web_host = WebServiceHost(agent, host=host, port=port)
            actual_port = await _web_host.start()
            await send_event({
                "event": "service_ready",
                "data": {"host": host, "port": actual_port, "apis": [a.get("name") for a in (agent.spec.apis or [])]},
            })

            # 常驻化本 Agent 依赖的工具（一次性预热：import 重型库 + 加载模型），
            # 之后每次 API 调用只需付出推理时间，避免重复的启动开销。
            # 预热在后台进行，不阻塞服务就绪；未就绪期间的调用会自动回退到
            # ToolExecutor 的一次性执行。
            async def _warmup():
                try:
                    st = await agent.warmup_resident_tools()
                    if st:
                        await send_event({"event": "tools_warmed", "data": st})
                except Exception as e:
                    await send_event({"event": "error",
                                      "data": {"message": f"工具常驻预热失败: {e}"}})

            asyncio.ensure_future(_warmup())
            asyncio.ensure_future(_web_host.serve_forever())
        except Exception as e:
            await send_event({"event": "error", "data": {"message": f"Web 服务启动失败: {e}"}})

    async def _run_execute(task: dict, exec_state: dict):
        """在独立任务中驱动一个 execute 轮次。

        必须作为任务运行：Agent 在 execute 期间可能 await
        self._bus.call_agent(...)，需要本读循环继续读取 stdin 收取
        agent_call_response / bus_message 帧——若同步 await 将自锁死。
        """
        ctx_data = task.get("context", {})
        from core.agent.base import AgentContext

        uid = ctx_data.get("user_id") or "0"
        # 关键：工具执行/产物落盘都按"发起本轮的用户"走。进程启动时固化的是
        # 超管 uid（启动进程无请求身份），若不在此覆盖，任何用户的工具都会
        # 写到 /root/SB-USER/0/（超管目录）——跨用户数据串位。
        os.environ["SOTABAND_USER_ID"] = str(uid)

        ctx = AgentContext(
            agent_id=args.agent_id,
            session_id=ctx_data.get("session_id", "default"),
            user_id=uid,
            metadata=ctx_data.get("metadata", {}) or {},
        )
        params = task.get("params", {})
        try:
            async for event in agent.execute(ctx, **params):
                await send_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await send_event({"event": "error", "data": {"message": str(e)}})
        finally:
            exec_state["task"] = None

    exec_state: dict = {"task": None}

    try:
        while True:
            try:
                line = await asyncio.to_thread(sys.stdin.buffer.readline)
                if not line:
                    break  # stdin closed → 主进程要求退出

                msg = json.loads(line.decode('utf-8').strip())

                # ── 通信帧（todo 4.5）：响应 / 投递 ──
                if is_frame(msg):
                    t = msg.get("type")
                    if t == "agent_call_response":
                        bus.resolve(msg)
                    elif t == "platform_call_response":
                        bus.resolve_platform(msg)
                    elif t in ("bus_message", "agent_call"):
                        # 作为任务投递：长 RPC 不阻塞读循环
                        asyncio.ensure_future(bus.deliver(msg))
                    continue

                action = msg.get("action")
                if action == "stop":
                    cur = exec_state.get("task")
                    if cur is not None and not cur.done():
                        cur.cancel()
                    break

                if action == "cancel":
                    # 主进程要求中断当前轮次（用户停止 / 超时 / 前端断开）。
                    # 回执必须反映**任务是否真的结束了**：卡在不可取消位置
                    # （同步 OS 调用、线程、无超时 SDK）时 cancel 无效，
                    # cleared=False 让主进程据此强制重启，否则槽位永不释放。
                    cur = exec_state.get("task")
                    if cur is not None and not cur.done():
                        cur.cancel()
                        # 不用 wait_for：任务被取消时它会抛 CancelledError，
                        # 打断本读循环；wait 只返回 done/pending。
                        await asyncio.wait([cur], timeout=3.0)
                        cleared = cur.done()
                    else:
                        cleared = True
                    await send_event({
                        "event": "round_cancelled",
                        "data": {"cleared": cleared},
                    })
                    continue

                if action == "execute":
                    cur = exec_state.get("task")
                    if cur is not None and not cur.done():
                        await send_event({"event": "error", "data": {
                            "message": "Agent 正忙（上一轮 execute 尚未结束）"}})
                    else:
                        exec_state["task"] = asyncio.ensure_future(
                            _run_execute(msg, exec_state))

                elif action == "ping":
                    # 健康探测
                    await send_event({"event": "pong", "data": {}})

            except json.JSONDecodeError:
                continue
            except Exception:
                break
    finally:
        # 取消未完成的 execute 轮次，再执行清理：
        # 回收常驻工具进程、释放其占用的模型/显存。
        cur = exec_state.get("task")
        if cur is not None and not cur.done():
            cur.cancel()
        try:
            await agent.on_stop()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
