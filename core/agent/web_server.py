"""Web 服务式 Agent 的 HTTP 宿主

为 web-service 类型的 Agent 提供一个轻量 HTTP 服务，监听 spec.server 指定的
端口，按 spec.apis 的路由定义接收外部请求，逐条调用 Agent 的 execute 完成
工具组合，并返回 JSON 响应。

设计要点（见 docs/agent-types-design.md）：
- 与 Agent 主进程同进程（Agent 本身已运行在独立子进程中），HTTP 服务作为
  该子进程内的一个 asyncio 任务运行。
- 端口为空时由引擎分配（此处支持传入 port=0 由系统随机分配）。
- 提供 /_health 健康检查端点。
"""

import asyncio
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent.base import BaseAgent, AgentContext


class WebServiceHost:
    """在 Agent 子进程内运行 HTTP 服务，把请求分派给 Agent.execute。"""

    def __init__(self, agent: "BaseAgent", host: str = "127.0.0.1", port: int = 0):
        self.agent = agent
        self.host = host
        self.port = port
        self._server = None
        self.actual_port = None

    async def start(self):
        """启动 HTTP 服务（asyncio 原生实现，避免额外依赖）。"""
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        # 记录实际监听端口（port=0 时由系统分配）
        try:
            self.actual_port = self._server.sockets[0].getsockname()[1]
        except Exception:
            self.actual_port = self.port
        return self.actual_port

    async def serve_forever(self):
        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """极简 HTTP/1.1 请求处理：解析请求行 / 头 / 体，按 API 路由分派。"""
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            parts = request_line.decode("utf-8", "replace").split()
            if len(parts) < 2:
                await self._respond(writer, 400, {"error": "bad request line"})
                return
            method, path = parts[0].upper(), parts[1]

            # 读取请求头
            headers = {}
            content_length = 0
            while True:
                line = await reader.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break
                key, _, val = line.decode("utf-8", "replace").partition(":")
                headers[key.strip().lower()] = val.strip()
            content_length = int(headers.get("content-length", "0") or "0")

            body = b""
            if content_length > 0:
                body = await reader.readexactly(content_length)

            # 健康检查
            if path == "/_health":
                await self._respond(writer, 200, {"status": "ok", "service": self.agent.agent_id})
                return

            # 匹配 API 路由（忽略 query string）
            clean_path = path.split("?", 1)[0]
            api = None
            for a in self.agent.spec.apis or []:
                if a.get("path") == clean_path and a.get("method", "POST").upper() == method:
                    api = a
                    break
            if api is None:
                await self._respond(writer, 404, {"error": f"no api for {method} {clean_path}"})
                return

            # 解析请求参数（JSON body 或 query string）
            params = {}
            if body:
                try:
                    params = json.loads(body.decode("utf-8"))
                except Exception:
                    params = {}
            if "?" in path:
                from urllib.parse import parse_qs

                for k, v in parse_qs(path.split("?", 1)[1]).items():
                    params.setdefault(k, v[0] if len(v) == 1 else v)

            # 调用 Agent 执行该 API 的流程
            from core.agent.base import AgentContext

            ctx = AgentContext(agent_id=self.agent.agent_id, session_id="http", user_id="http")
            response = None
            async for event in self.agent.execute(ctx, api_name=api.get("name"), params=params):
                if event.get("event") == "result":
                    response = event.get("data", {}).get("response")
                elif event.get("event") == "error":
                    await self._respond(writer, 500, {"error": event.get("data", {}).get("message", "error")})
                    return

            await self._respond(writer, 200, response if response is not None else {"status": "ok"})
        except Exception as e:
            try:
                await self._respond(writer, 500, {"error": str(e)})
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _respond(self, writer: asyncio.StreamWriter, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Internal Server Error"}.get(status, "OK")
        header = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        writer.write(header + body)
        await writer.drain()
