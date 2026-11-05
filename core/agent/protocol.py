"""协作协议 — Agent 间通信的帧格式定义（todo 4.5）

所有 Agent 运行在独立子进程中，子进程与主进程之间复用既有的
stdin/stdout JSON-line 通道。为区分「旧执行协议」与「通信帧」，
通信帧统一带版本字段：

    帧格式: {"v": 1, "dir": "o"|"i", "type": "<帧类型>", "req": "<请求id>", ...}
      dir="o"  子进程 → 主进程
      dir="i"  主进程 → 子进程

    普通执行事件仍是旧形式 {"event": "...", "data": {...}}（无 v 字段），
    旧调用方（自动调试循环、execute_stream）不受影响。

帧类型（type）：
    bus_subscribe          i  主→子：（仅登记用，实际订阅表在主进程）
    bus_publish            o  子→主：发布消息 {channel, message}
    bus_message            i  主→子：投递订阅消息 {channel, message, from}
    agent_call             o  子→主：RPC 请求 {to, method, params, timeout, user_id}
    agent_call             i  主→子：转发 RPC 请求 {from, method, params, user_id}
      （user_id = 服务端注入的调用者 uid，被调侧据此覆写执行身份；旧帧缺字段时回退默认）
    agent_reply            o  子→主：RPC 应答 {ok, result|error}
    agent_call_response    i  主→子：（发起方收到的 RPC 结果回执）
    platform_call          o  子→主：平台工具调用 {tool, args, session_id, user_id}
    platform_call_response i  主→子：平台调用回执（网关返回的结构化结果）
    workspace_audit        o  子→主：工作空间工具生命周期审计（单向，主进程落 chat_ops）

语义约定：
- Pub/Sub 为尽力而为：目标未启动/未订阅时消息丢弃，不排队重投。
- RPC 请求带 req id，应答以同一 req id 关联；超时/目标未运行/崩溃
  一律回结构化错误（ok=false），不抛异常。
- 订阅随进程生命周期：进程退出后主进程自动清理其订阅与悬挂请求。
"""

PROTOCOL_VERSION = 1

FRAME_BUS_SUBSCRIBE = "bus_subscribe"
FRAME_BUS_UNSUBSCRIBE = "bus_unsubscribe"
FRAME_BUS_PUBLISH = "bus_publish"
FRAME_BUS_MESSAGE = "bus_message"
FRAME_AGENT_CALL = "agent_call"
FRAME_AGENT_REPLY = "agent_reply"
FRAME_AGENT_CALL_RESPONSE = "agent_call_response"
FRAME_PLATFORM_CALL = "platform_call"
FRAME_PLATFORM_CALL_RESPONSE = "platform_call_response"
FRAME_WORKSPACE_AUDIT = "workspace_audit"

BUS_FRAME_TYPES = frozenset({
    FRAME_BUS_SUBSCRIBE, FRAME_BUS_UNSUBSCRIBE, FRAME_BUS_PUBLISH,
    FRAME_BUS_MESSAGE, FRAME_AGENT_CALL, FRAME_AGENT_REPLY,
    FRAME_AGENT_CALL_RESPONSE,
    FRAME_PLATFORM_CALL, FRAME_PLATFORM_CALL_RESPONSE,
    FRAME_WORKSPACE_AUDIT,
})


def make_frame(dir_: str, type_: str, req: str | None = None, **fields) -> dict:
    """构造通信帧。dir_: "o"=子进程→主进程, "i"=主进程→子进程。"""
    frame = {"v": PROTOCOL_VERSION, "dir": dir_, "type": type_}
    if req is not None:
        frame["req"] = req
    frame.update(fields)
    return frame


def is_frame(obj) -> bool:
    """是否为 v1 通信帧。"""
    return isinstance(obj, dict) and obj.get("v") == PROTOCOL_VERSION


def is_bus_frame(obj) -> bool:
    """是否为通信类帧（区别于执行事件流）。"""
    return is_frame(obj) and obj.get("type") in BUS_FRAME_TYPES
