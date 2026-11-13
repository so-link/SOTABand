#!/usr/bin/env python3
"""OpenAI 兼容的确定性假 LLM —— 让 E2E 完全离线、可重复。

支持：
- ``POST /v1/chat/completions``（stream=True 走 SSE chunked，stream=False 走 JSON）
- ``GET /v1/models``、``GET /health``

响应脚本按「提示词包含关键字」匹配（见 ``frontend/e2e/fixtures/llm-script.json``）；
未命中时返回带 run nonce 的固定文本，用于证明对话内容确实被前端渲染。

所有请求写入 JSONL 日志（Authorization / Cookie 脱敏），便于失败排查。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if sys.platform == "win32":
    for _stream in ("stdout", "stderr"):
        try:
            getattr(sys, _stream).reconfigure(encoding="utf-8")
        except Exception:
            pass

_LOG_LOCK = threading.Lock()
_RULES: list[dict] = []
_DEFAULT_RESPONSE = "E2E_REPLY_{{nonce}}"
_NONCE = "local"
_LOG_PATH: Path | None = None
_MODEL = "e2e-model"


def _now() -> int:
    return int(time.time())


def _redact(value: str | None) -> str:
    if not value:
        return ""
    return "<redacted>"


def _log(record: dict) -> None:
    if _LOG_PATH is None:
        return
    line = json.dumps(record, ensure_ascii=False)
    with _LOG_LOCK:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _load_script(path: Path | None) -> None:
    global _RULES, _DEFAULT_RESPONSE
    if path is None or not path.exists():
        _RULES = []
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = data.get("rules") if isinstance(data, dict) else data
    if isinstance(rules, list):
        _RULES = [rule for rule in rules if isinstance(rule, dict) and rule.get("match")]
    if isinstance(data, dict) and data.get("default"):
        _DEFAULT_RESPONSE = str(data["default"])
    for rule in _RULES:
        if rule.get("match") == "__default__" and rule.get("response"):
            _DEFAULT_RESPONSE = str(rule["response"])


def _message_text(messages: list) -> str:
    chunks: list[str] = []
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
    return "\n".join(chunks)


def _select_response(messages: list) -> tuple[str, str | None]:
    text = _message_text(messages)
    for rule in _RULES:
        match = str(rule.get("match") or "")
        if match == "__default__":
            continue
        if match and match in text:
            response = str(rule.get("response") or "").replace("{{nonce}}", _NONCE)
            repeat = int(rule.get("repeat") or 1)
            return response * max(1, repeat), match
    response = _DEFAULT_RESPONSE.replace("{{nonce}}", _NONCE)
    return response, None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SOTABandFakeLLM/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/_health"):
            self._send_json(200, {"status": "ok"})
            return
        if path in ("/v1/models", "/models"):
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": _MODEL,
                            "object": "model",
                            "created": _now(),
                            "owned_by": "sotaband-e2e",
                        }
                    ],
                },
            )
            return
        self._send_json(404, {"error": {"message": f"unknown path {path}"}})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in ("/v1/chat/completions", "/chat/completions"):
            self._send_json(404, {"error": {"message": f"unknown path {path}"}})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": {"message": f"invalid json: {exc}"}})
            return

        messages = payload.get("messages") or []
        response_text, matched = _select_response(messages)
        stream = bool(payload.get("stream"))
        model = str(payload.get("model") or _MODEL)

        _log(
            {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "path": path,
                "stream": stream,
                "model": model,
                "matched": matched,
                "prompt_tail": _message_text(messages)[-800:],
                "response": response_text,
                "authorization": _redact(self.headers.get("Authorization")),
                "cookie": _redact(self.headers.get("Cookie")),
            }
        )

        if not stream:
            self._send_json(
                200,
                {
                    "id": f"chatcmpl-e2e-{int(time.time() * 1000)}",
                    "object": "chat.completion",
                    "created": _now(),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": response_text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": len(response_text),
                        "total_tokens": len(response_text) + 1,
                    },
                },
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def emit(obj: dict) -> None:
            data = f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")
            self._send_chunk(data)

        chunk_id = f"chatcmpl-e2e-{int(time.time() * 1000)}"
        base = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": _now(),
            "model": model,
        }
        emit({**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})

        # 分块模拟流式；分块大小固定，保证同一响应在重复运行中表现一致
        step = 6
        for offset in range(0, len(response_text), step):
            emit(
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": response_text[offset : offset + step]},
                            "finish_reason": None,
                        }
                    ],
                }
            )
            time.sleep(0.01)

        emit({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        self._send_chunk(b"data: [DONE]\n\n")
        self._send_chunk(b"")
        try:
            self.wfile.flush()
        except Exception:
            pass


def main() -> int:
    global _NONCE, _LOG_PATH, _MODEL

    parser = argparse.ArgumentParser(description="SOTABand E2E 假 LLM 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8111)
    parser.add_argument("--script", type=Path, default=None)
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--nonce", default=os.environ.get("E2E_RUN_ID", "local"))
    parser.add_argument("--model", default="e2e-model")
    args = parser.parse_args()

    _NONCE = args.nonce
    _LOG_PATH = args.log
    _MODEL = args.model
    _load_script(args.script)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(
        f"[fake-llm] listening on http://{args.host}:{args.port}/v1 "
        f"(rules={len(_RULES)}, nonce={_NONCE})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
