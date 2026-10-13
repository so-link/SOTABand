"""Agent 子进程入口 — 通过 stdin/stdout JSON-line 协议与主进程通信

用法: python -m core.agent.entrypoint --agent-id xxx --impl-path core/agent/implementations/xxx
"""

import argparse
import asyncio
import importlib.util
import json
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
        for _d in reversed(_def_dirs):
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

    # 通过 stdin/stdout JSON-line 协议通信
    # 普通 Windows stdin/stdout 管道不支持 connect_read/write_pipe。
    # 阻塞 I/O 放在线程中，保留异步执行和逐行 UTF-8 协议。
    write_lock = asyncio.Lock()

    async def send_event(event: dict):
        """发送 JSON 行到主进程。

        每个事件立即 flush，避免用户等待缓冲区满或进程退出才收到响应。
        """
        line = json.dumps(event, ensure_ascii=False) + "\n"
        def write_line():
            sys.stdout.buffer.write(line.encode("utf-8"))
            sys.stdout.buffer.flush()

        async with write_lock:
            await asyncio.to_thread(write_line)

    # 加载失败 → 通知主进程后退出
    if not agent_loaded:
        await send_event({"event": "error", "data": {"message": load_error[:500]}})
        await send_event({"event": "done", "data": {"messageId": "fatal"}})
        return

    await send_event({"event": "agent_ready", "data": {
        "agent_id": args.agent_id, "role": agent.role.value,
    }})

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

    try:
        while True:
            try:
                line = await asyncio.to_thread(sys.stdin.buffer.readline)
                if not line:
                    break  # stdin closed → 主进程要求退出

                task = json.loads(line.decode('utf-8').strip())

                if task.get("action") == "stop":
                    break

                if task.get("action") == "execute":
                    ctx_data = task.get("context", {})
                    from core.agent.base import AgentContext

                    ctx = AgentContext(
                        agent_id=args.agent_id,
                        session_id=ctx_data.get("session_id", "default"),
                        user_id=ctx_data.get("user_id", "default"),
                        metadata=ctx_data.get("metadata", {}) or {},
                    )
                    params = task.get("params", {})

                    try:
                        async for event in agent.execute(ctx, **params):
                            await send_event(event)
                    except Exception as e:
                        await send_event({"event": "error", "data": {"message": str(e)}})

                elif task.get("action") == "ping":
                    # 健康探测
                    await send_event({"event": "pong", "data": {}})

            except json.JSONDecodeError:
                continue
            except Exception:
                break
    finally:
        # 无论正常退出还是异常崩溃，都执行清理：
        # 回收常驻工具进程、释放其占用的模型/显存。
        try:
            await agent.on_stop()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
