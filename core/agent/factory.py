"""Agent 工厂 — 异步子进程生命周期管理"""

import asyncio
import json
import sys
from pathlib import Path
from typing import AsyncGenerator

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class AgentFactory:
    """Agent 工厂 — 管理 Agent 子进程的完整生命周期"""

    def __init__(self):
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def start(self, agent_id: str, impl_path: str = None) -> dict:
        """启动 Agent 子进程"""
        if agent_id in self._processes and self._processes[agent_id].returncode is None:
            return {"agent_id": agent_id, "status": "already_running"}

        if impl_path is None:
            impl_path = f"resources/agents/implementations/{agent_id}"

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

            # 等一会儿检查子进程是否存活
            await asyncio.sleep(1.0)
            alive = proc.returncode is None
            return {
                "agent_id": agent_id,
                "status": "started" if alive else "failed",
                "running": alive,
            }
        except Exception as e:
            return {"agent_id": agent_id, "status": "failed", "error": str(e)}

    async def stop(self, agent_id: str) -> dict:
        """停止 Agent 子进程"""
        proc = self._processes.pop(agent_id, None)
        if proc is None:
            return {"agent_id": agent_id, "status": "not_running"}

        if proc.returncode is None:
            # 发送 stop 命令
            try:
                task = json.dumps({"action": "stop"}) + "\n"
                proc.stdin.write(task.encode())
                await proc.stdin.drain()
            except Exception:
                pass
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
        return {"agent_id": agent_id, "status": "stopped"}

    async def restart(self, agent_id: str) -> dict:
        """重启 Agent 子进程"""
        await self.stop(agent_id)
        await asyncio.sleep(0.5)

        from core.resource.registry.agent_registry import AgentRegistry
        registry = AgentRegistry()
        entry = await registry.get(agent_id)
        impl_path = entry.get("impl_path") if entry else f"resources/agents/implementations/{agent_id}"
        return await self.start(agent_id, impl_path)

    async def execute(
        self, agent_id: str, content: str
    ) -> AsyncGenerator[dict, None]:
        """向 Agent 子进程发送任务，流式读取响应"""
        proc = self._processes.get(agent_id)
        if proc is None or proc.returncode is not None:
            yield {"event": "error", "data": {"message": f"Agent '{agent_id}' 未运行"}}
            return

        task = {
            "action": "execute",
            "context": {"session_id": "default", "user_id": "default", "agent_id": agent_id},
            "params": {"content": content},
        }
        try:
            proc.stdin.write((json.dumps(task) + "\n").encode())
            await proc.stdin.drain()
        except Exception:
            yield {"event": "error", "data": {"message": "Agent 进程已断开"}}
            return

        # 流式读取 stdout
        buffer = b""
        while proc.returncode is None:
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=30)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        event = json.loads(line.decode())
                        yield event
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
            except asyncio.TimeoutError:
                break
            except Exception:
                break

    def is_running(self, agent_id: str) -> bool:
        proc = self._processes.get(agent_id)
        return proc is not None and proc.returncode is None


# 全局单例
agent_factory = AgentFactory()
