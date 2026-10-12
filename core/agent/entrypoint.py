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
        spec.raw_md = spec_file.read_text(encoding='utf-8')

    return agent_cls(spec)


async def main():
    # Windows 的标准管道默认可能使用系统代码页（如 GBK）。
    # 父进程按 UTF-8 传输 JSON，必须显式统一 stdin/stdout 编码。
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

    def send_event(event: dict):
        """发送 JSON 行到主进程。

        Windows 的 ProactorEventLoop 不能可靠地把重定向后的标准输入输出
        包装成 asyncio pipe transport，会在 connect_write_pipe() 处触发
        WinError 6。Agent 输出很短，直接写入并 flush 更适合这个协议。
        """
        line = json.dumps(event, ensure_ascii=False) + "\n"
        sys.stdout.write(line)
        sys.stdout.flush()

    # 加载失败 → 通知主进程后退出
    if not agent_loaded:
        send_event({"event": "error", "data": {"message": load_error[:500]}})
        send_event({"event": "done", "data": {"messageId": "fatal"}})
        return

    while True:
        try:
            # 在工作线程中阻塞读取，避免 Windows asyncio 对标准 pipe 的
            # Proactor 兼容性问题，同时不阻塞 Agent 的事件循环。
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break  # stdin closed → 主进程要求退出

            task = json.loads(line.strip())

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
