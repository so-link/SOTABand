"""Web 服务式 Agent 的 HTTP 宿主

为 web-service 类型的 Agent 提供一个轻量 HTTP 服务，监听 spec.server 指定的
端口，按 spec.apis 的路由定义接收外部请求，逐条调用 Agent 的 execute 完成
工具组合，并返回 JSON 响应。

设计要点（见 docs/agent-types-design.md）：
- 与 Agent 主进程同进程（Agent 本身已运行在独立子进程中），HTTP 服务作为
  该子进程内的一个 asyncio 任务运行。
- 端口为空时由引擎分配（此处支持传入 port=0 由系统随机分配）。
- 路由支持 ``{name}`` 路径参数（如 ``/tasks/{task_id}``），捕获值会合入
  Agent 的 params，且优先于 body/query 中的同名字段。
- 提供 /_health 健康检查端点。
"""

import asyncio
import json
from typing import TYPE_CHECKING
from urllib.parse import unquote

if TYPE_CHECKING:
    from core.agent.base import BaseAgent, AgentContext


def match_api_path(template: str, path: str) -> dict | None:
    """按 ``/`` 分段匹配声明式路径模板，返回路径参数字典。

    - 无花括号的模板退化为精确匹配（``{}`` 表示匹配、``None`` 表示不匹配）；
    - ``{name}`` 段捕获一段非空路径并做 URL 解码，供 Agent 的 params 使用；
    - 段数不一致、空段或字面量段不同即不匹配。

    典型场景：``/api/v1/recognition/tasks/{task_id}`` 与
    ``/api/v1/recognition/tasks/task-0001``。
    """
    if template == path:
        return {}
    t_parts = template.split("/")
    p_parts = path.split("/")
    if len(t_parts) != len(p_parts):
        return None
    params: dict[str, str] = {}
    for t_part, p_part in zip(t_parts, p_parts):
        if len(t_part) > 2 and t_part.startswith("{") and t_part.endswith("}"):
            if not p_part:
                return None
            params[t_part[1:-1]] = unquote(p_part)
        elif t_part != p_part:
            return None
    return params


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

            # 匹配 API 路由（忽略 query string）：精确匹配优先，{name} 模板次之
            clean_path = path.split("?", 1)[0]
            api = None
            path_params: dict = {}
            apis = self.agent.spec.apis or []
            for a in apis:
                if a.get("path") == clean_path and a.get("method", "POST").upper() == method:
                    api = a
                    break
            if api is None:
                for a in apis:
                    if a.get("method", "POST").upper() != method:
                        continue
                    matched = match_api_path(str(a.get("path") or ""), clean_path)
                    if matched is not None:
                        api, path_params = a, matched
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
            # body 可能是数组/标量：统一归为 dict，避免后面的 setdefault/update 抛错
            if not isinstance(params, dict):
                params = {}
            if "?" in path:
                from urllib.parse import parse_qs

                for k, v in parse_qs(path.split("?", 1)[1]).items():
                    params.setdefault(k, v[0] if len(v) == 1 else v)
            # 路径参数优先级最高：body/query 中的同名字段不得覆盖路径值
            if path_params:
                params.update(path_params)

            # 调用 Agent 执行该 API 的流程。
            # 身份 = 服务所有者：HTTP 调用方可能没有平台身份，而工具产物按
            # 「服务侧归档」语义归属注册 owner（见 ToolRuntime 文档）。每个连接
            # 由独立 task 处理，contextvar 随连接结束销毁，不必 reset。
            from core.agent.base import AgentContext
            from core.user_paths import SYSTEM_UID, set_exec_uid

            # 宿主按 BaseAgent 约定工作，但允许测试桩/第三方实现只提供
            # agent_id/spec/execute：缺 owner_uid 时归系统用户。
            owner = getattr(self.agent, "owner_uid", "") or SYSTEM_UID
            set_exec_uid(owner)
            ctx = AgentContext(agent_id=self.agent.agent_id, session_id="http", user_id=owner)
            response = None
            # result 事件可选回传 HTTP 状态码（Agent 自己表达 400/404/409/500），
            # 未提供时保持 200，既有 Agent 行为不变。
            result_status = None
            async for event in self.agent.execute(ctx, api_name=api.get("name"), params=params):
                if event.get("event") == "result":
                    payload = event.get("data", {}) or {}
                    response = payload.get("response")
                    raw_status = payload.get("status")
                    if isinstance(raw_status, int) and 100 <= raw_status <= 599:
                        result_status = raw_status
                elif event.get("event") == "error":
                    await self._respond(writer, 500, {"error": event.get("data", {}).get("message", "error")})
                    return

            await self._respond(
                writer,
                result_status or 200,
                response if response is not None else {"status": "ok"},
            )
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
        reason = {
            200: "OK",
            400: "Bad Request",
            404: "Not Found",
            409: "Conflict",
            500: "Internal Server Error",
        }.get(status, "OK")
        header = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        writer.write(header + body)
        await writer.drain()
