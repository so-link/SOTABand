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


def load_agent(impl_path: Path, agent_id: str):
    """从 implementations/{agent-id}/agent.py 加载 Agent 实例"""
    agent_file = impl_path / "agent.py"
    if not agent_file.exists():
        raise FileNotFoundError(f"Agent 代码不存在: {agent_file}")

    spec = importlib.util.spec_from_file_location(f"agent_{agent_id}", agent_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from core.agent.base import BaseAgent, AgentSpec, AgentRole

    # 查找 BaseAgent 子类
    agent_cls = None
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and issubclass(obj, BaseAgent) and obj is not BaseAgent:
            agent_cls = obj
            break

    if agent_cls is None:
        raise ValueError(f"Agent 代码中未找到继承 BaseAgent 的类: {agent_file}")

    # 读取 spec
    spec_file = impl_path / "spec.md"
    spec = AgentSpec(id=agent_id, name=agent_id, role=AgentRole.TASK)
    if spec_file.exists():
        spec.raw_md = spec_file.read_text()

    return agent_cls(spec)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--impl-path", required=True)
    args = parser.parse_args()

    impl_path = Path(args.impl_path)
    if not impl_path.is_absolute():
        impl_path = PROJECT_ROOT / impl_path

    # 加载 Agent
    agent = load_agent(impl_path, args.agent_id)
    await agent.on_start()

    # 通过 stdin/stdout JSON-line 协议通信
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)
    w_transport, _ = await asyncio.get_event_loop().connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(w_transport, protocol, reader, asyncio.get_event_loop())

    def send_event(event: dict):
        """发送 JSON 行到主进程"""
        line = json.dumps(event, ensure_ascii=False) + "\n"
        writer.write(line.encode())

    while True:
        try:
            line = await reader.readline()
            if not line:
                break  # stdin closed → 主进程要求退出

            task = json.loads(line.decode().strip())

            if task.get("action") == "stop":
                break

            if task.get("action") == "execute":
                ctx_data = task.get("context", {})
                from core.agent.base import AgentContext

                ctx = AgentContext(
                    agent_id=args.agent_id,
                    session_id=ctx_data.get("session_id", "default"),
                    user_id=ctx_data.get("user_id", "default"),
                )
                params = task.get("params", {})

                try:
                    async for event in agent.execute(ctx, **params):
                        send_event(event)
                except Exception as e:
                    send_event({"event": "error", "data": {"message": str(e)}})

        except json.JSONDecodeError:
            continue
        except Exception:
            break

    await agent.on_stop()


if __name__ == "__main__":
    asyncio.run(main())
