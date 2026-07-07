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
    """Agent 工厂 — 管理 Agent 子进程的完整生命周期"""

    def __init__(self):
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def start(self, agent_id: str, impl_path: str = None) -> dict:
        """启动 Agent 子进程。若代码缺失则尝试从 spec 自动重新生成。"""
        if agent_id in self._processes and self._processes[agent_id].returncode is None:
            return {"agent_id": agent_id, "status": "already_running"}

        if impl_path is None:
            impl_path = f"resources/agents/implementations/{agent_id}"

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
            code = agent_file.read_text()
            issues = self._check_agent_code(code)
            if issues:
                # 尝试自动修复
                fixed = self._auto_fix_code(code)
                if fixed != code:
                    agent_file.write_text(fixed)
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

            return {"agent_id": agent_id, "status": "started", "running": True}
        except Exception as e:
            return {"agent_id": agent_id, "status": "failed", "error": str(e)}

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
        spec_file = PROJECT_ROOT / "resources" / "agents" / "definitions" / f"{agent_id}.md"
        if not spec_file.exists():
            return False

        try:
            from core.resource.builder.agent_builder import AgentCodeBuilder
            builder = AgentCodeBuilder()
            spec = {"id": agent_id, "raw_md": spec_file.read_text()}
            if not await builder.validate_spec(spec):
                return False

            code = await builder.build(spec)
            impl_dir = PROJECT_ROOT / "resources" / "agents" / "implementations" / agent_id
            impl_dir.mkdir(parents=True, exist_ok=True)
            (impl_dir / "agent.py").write_text(code)
            (impl_dir / "spec.md").write_text(spec_file.read_text())
            return True
        except Exception:
            return False

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

        # registry 中的 impl_path 相对于 resources/agents/，但 start() 需要相对于 PROJECT_ROOT
        impl_path = f"resources/agents/implementations/{agent_id}"
        return await self.start(agent_id, impl_path)

    async def execute(
        self, agent_id: str, content: str
    ) -> AsyncGenerator[dict, None]:
        """向 Agent 子进程发送任务，流式读取响应（总超时 120s）"""
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

        # 流式读取 stdout（总超时 120s，单次读取超时 30s）
        buffer = b""
        deadline = time.time() + 120
        while proc.returncode is None:
            if time.time() > deadline:
                yield {"event": "error", "data": {"message": "Agent 执行超时 (120s)"}}
                try:
                    proc.terminate()
                except Exception:
                    pass
                return
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
