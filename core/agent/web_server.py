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
import hashlib
import hmac
import json
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent.base import AgentContext, BaseAgent


# ── 发起者身份头（可选，引擎代理使用）─────────────────────────────────
# 端口本身只认「引擎共享令牌」；身份头在此之上再声明「这次调用代表哪个用户」，
# 用 HMAC 签名防止拿到令牌之外的伪造（虽然令牌只有引擎知道，但纵深防御）。
OWNER_HEADER = "x-sotaband-owner"
OWNER_SIG_HEADER = "x-sotaband-owner-sig"

_OWNER_MAX_LEN = 128
_OWNER_RE = re.compile(r"^[A-Za-z0-9._@:-]+$")


def sign_owner(engine_token: str, owner: str) -> str:
    """计算发起者身份签名：``hex(hmac_sha256(engine_token, "sotaband-owner:"+owner))``。

    引擎代理（``/api/agent/{id}/test-api``）在转发时把 ``owner`` 与签名放进
    ``X-Sotaband-Owner`` / ``X-Sotaband-Owner-Sig``，Web 服务据此以该用户身份
    执行 Agent（工具落库、数据集归属都按人隔离），而不是永远用机器身份。
    """
    if not engine_token or not owner:
        return ""
    message = f"sotaband-owner:{owner}".encode("utf-8")
    return hmac.new(engine_token.encode("utf-8"), message, hashlib.sha256).hexdigest()


def owner_headers(engine_token: str, owner: str | None) -> dict[str, str]:
    """构造发起者身份请求头；``owner`` / ``engine_token`` 为空时返回空 dict。"""
    if not engine_token or not owner:
        return {}
    return {
        OWNER_HEADER: str(owner),
        OWNER_SIG_HEADER: sign_owner(engine_token, str(owner)),
    }


def verify_owner(engine_token: str, owner: str, signature: str) -> bool:
    """校验发起者身份头；token / owner / 签名任一缺失或不合法都返回 False。"""
    if not engine_token or not owner or not signature:
        return False
    owner = str(owner)
    if len(owner) > _OWNER_MAX_LEN or not _OWNER_RE.match(owner):
        return False
    return hmac.compare_digest(sign_owner(engine_token, owner), str(signature))


class WebServiceHost:
    """在 Agent 子进程内运行 HTTP 服务，把请求分派给 Agent.execute。

    ## 安全约束（2026-11-04 起）

    - **强制监听 127.0.0.1**：忽略 agent spec 里可被用户编辑的 ``server.host``，
      否则任何用户都能把 Agent 端口暴露到 0.0.0.0，绕过引擎的登录鉴权直接
      执行 Agent 流程（P1-13）。
    - **引擎共享令牌**：除 ``/_health`` 外，所有 API 请求必须带
      ``Authorization: Bearer <SOTABAND_AGENT_HTTP_TOKEN>``。令牌由引擎在 spawn
      时注入，后端代理（``/api/agent/{id}/test-api``）携带；外部无法得知。
    - 需要对外提供 API 的部署应另加网关（不在本期范围）。
    """

    def __init__(
        self,
        agent: "BaseAgent",
        host: str = "127.0.0.1",
        port: int = 0,
        token: str | None = None,
    ):
        self.agent = agent
        # 忽略外部传入的 host（防 0.0.0.0 暴露）；如需对外请走网关
        self.host = "127.0.0.1"
        self.port = port
        self.token = token or ""
        self._server = None
        self.actual_port = None
        # HTTP 请求之间串行执行：Agent 实例与其进程级 env 都是共享状态。
        # 与 stdin 任务的互斥由引擎侧 AgentFactory.execution_lock 保证
        # （/test-api 代理必须持锁调用本服务）。
        self._exec_lock = asyncio.Lock()

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

            # 健康检查（免令牌：仅用于引擎探测服务就绪，不执行 Agent 逻辑）
            if path == "/_health":
                await self._respond(writer, 200, {"status": "ok", "service": self.agent.agent_id})
                return

            # 引擎共享令牌校验（P1-13：端口是本机第二个入口，不能裸奔）
            # fail-closed：没有令牌的 Host 一律拒绝（只有引擎会带令牌启动它）
            if not self.token:
                await self._respond(writer, 401, {"error": "missing engine token"})
                return
            presented = headers.get("authorization", "")
            expected = f"Bearer {self.token}"
            if not hmac.compare_digest(presented, expected):
                await self._respond(writer, 401, {"error": "unauthorized"})
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

            # 发起者身份（可选）：令牌之外再带 HMAC 签名头，声明「这次调用代表
            # 哪个用户」。无头 / 校验失败 → 保持机器身份（旧行为），不因缺头
            # 而拒绝请求（令牌已经证明了调用方是引擎）。
            owner = ""
            presented_owner = headers.get(OWNER_HEADER, "")
            if presented_owner and verify_owner(
                self.token, presented_owner, headers.get(OWNER_SIG_HEADER, "")
            ):
                owner = presented_owner

            response = None
            error_message = ""
            # 串行化执行：Agent 实例与其**进程级 env**（SOTABAND_USER_ID）都是
            # 共享状态，并发 HTTP（以及并发的 stdin 任务）会互相覆盖身份，导致
            # 工具以别人的身份落库。与 stdin 任务的互斥由引擎侧
            # AgentFactory.execution_lock 保证（/test-api 代理必须持锁）。
            async with self._exec_lock:
                previous_owner = os.environ.get("SOTABAND_USER_ID")
                try:
                    if owner:
                        os.environ["SOTABAND_USER_ID"] = owner
                    else:
                        os.environ.pop("SOTABAND_USER_ID", None)
                    try:
                        from core.user_context import reset_cache

                        reset_cache()
                    except Exception:
                        pass

                    from core.agent.base import AgentContext
                    from core.user_context import use_owner

                    # 显式身份优先于 env：即使 env 被并发任务短暂覆盖，
                    # 进程内 current_owner_id() 仍然拿到本次请求的发起者。
                    ctx = AgentContext(
                        agent_id=self.agent.agent_id,
                        session_id="http",
                        user_id=owner or "http",
                    )
                    stream = None
                    try:
                        with use_owner(owner or None):
                            stream = self.agent.execute(
                                ctx, api_name=api.get("name"), params=params
                            )
                            async for event in stream:
                                if event.get("event") == "result":
                                    response = event.get("data", {}).get("response")
                                elif event.get("event") == "error":
                                    error_message = (
                                        event.get("data", {}).get("message") or "error"
                                    )
                                    break
                    finally:
                        # 显式关闭 Agent 执行流，避免提前 break 后残留生成器
                        close = getattr(stream, "aclose", None)
                        if close is not None:
                            try:
                                await close()
                            except Exception:
                                pass
                except Exception as e:
                    error_message = str(e)
                finally:
                    # 恢复执行前的 env：既不让本次身份留给后续请求，也不粗暴
                    # 清掉并发 stdin 任务已设置的身份（身份由锁 + 显式上下文保证）。
                    if previous_owner is None:
                        os.environ.pop("SOTABAND_USER_ID", None)
                    else:
                        os.environ["SOTABAND_USER_ID"] = previous_owner
                    try:
                        from core.user_context import reset_cache

                        reset_cache()
                    except Exception:
                        pass

            if error_message:
                await self._respond(writer, 500, {"error": error_message})
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
        reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 500: "Internal Server Error"}.get(status, "OK")
        header = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        writer.write(header + body)
        await writer.drain()
