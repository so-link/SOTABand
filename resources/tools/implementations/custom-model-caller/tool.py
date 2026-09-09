"""自定义模型调用器

用使用者临时指定的服务商与模型调用大模型，不影响项目全局配置。

场景：项目 .env 用某个纯文本模型，但本次任务需要多模态（看图）、
长上下文或想试用其他厂商的模型 —— 无需修改全局配置。

密钥策略（api_key 可选）：优先用调用方显式传入的 key；未传时由
【LLM自定义配置对话API】自动降级读取 .env 中该服务商的
<PROVIDER>_API_KEY（或主 key LLM_API_KEY）。推荐只传 provider
让 key 走 .env——密钥不进聊天记录、不进工具参数。
"""

import base64
import io
import os
import time
import traceback
from pathlib import Path


def _resolve_image_path(raw: str):
    """解析图片路径：相对路径需相对项目根目录解析

    工具执行时的工作目录可能与项目根不同，因此相对路径必须显式
    相对项目根目录解析，否则会找不到文件。
    """
    if not raw or not str(raw).strip():
        return None
    p = Path(str(raw).strip())
    if p.is_absolute():
        return p if p.exists() else None
    # 相对路径：依次尝试项目根、当前目录、常见数据目录
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    candidates = [
        project_root / p,
        Path.cwd() / p,
        project_root / "data" / p,
        project_root / "uploaded_images" / p,
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return None


def _encode_image(path: Path, max_side: int = 1024) -> str:
    """读取图片并编码为 base64（等比缩放以控制体积）"""
    from PIL import Image

    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _mask(key: str) -> str:
    """脱敏：只保留前 4 后 4，避免密钥出现在输出或日志中"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def execute(**kwargs):
    t0 = time.time()
    prompt = (kwargs.get("prompt") or "").strip()
    provider = (kwargs.get("provider") or "").strip()
    model = (kwargs.get("model") or "").strip()
    api_key = (kwargs.get("api_key") or "").strip()
    base_url = (kwargs.get("base_url") or "").strip()
    image_path = (kwargs.get("image_path") or "").strip()

    try:
        max_tokens = int(kwargs.get("max_tokens") or 4096)
    except (TypeError, ValueError):
        max_tokens = 4096

    # ── 参数校验 ──
    if not prompt:
        return {"status": "failed", "output_format": "text",
                "message": "缺少必填参数：prompt（要发送给模型的内容）", "data": {}}
    if not model:
        return {"status": "failed", "output_format": "text",
                "message": "缺少必填参数：model（模型名称）", "data": {}}
    # api_key 已改为可选：key 已配置在 .env（<PROVIDER>_API_KEY 或主 LLM_API_KEY）
    # 时无需传入，避免密钥出现在聊天记录/工具参数里。降级链由
    # 【LLM自定义配置对话API】完成：显式 api_key > .env 副 key > .env 主 key。
    # 仅当「无显式 key 且无法定位服务商」时才失败。
    if not api_key and not provider and not base_url:
        return {"status": "failed", "output_format": "text",
                "message": (
                    "缺少服务商信息：请传 provider（推荐，key 会自动从 .env 的 "
                    "<PROVIDER>_API_KEY 读取，无需粘贴密钥），或直接传 api_key。"
                ), "data": {}}

    # ── 构造消息（支持多模态）──
    messages: list = []
    has_image = False

    if image_path:
        img = _resolve_image_path(image_path)
        if img is None:
            return {"status": "failed", "output_format": "text",
                    "message": f"图片未找到: {image_path}（相对路径请相对项目根目录填写）",
                    "data": {}}
        try:
            b64 = _encode_image(img)
        except Exception as e:
            return {"status": "failed", "output_format": "text",
                    "message": f"图片读取失败: {type(e).__name__}: {str(e)[:200]}", "data": {}}
        content = [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": prompt},
        ]
        has_image = True
    else:
        content = prompt

    messages.append({"role": "user", "content": content})

    # ── 调用 ──
    try:
        result = _call_api(
            "【【LLM自定义配置对话API】】",
            messages=messages,
            api_key=api_key,
            model=model,
            provider=provider,
            base_url=base_url,
            max_tokens=max_tokens,
        )
    except Exception as e:
        return {
            "status": "failed", "output_format": "text",
            "message": f"调用失败: {type(e).__name__}: {str(e)[:300]}",
            "data": {"api_key_masked": _mask(api_key)},
        }

    # 结果可能是 dict 或含 content 的对象
    if not isinstance(result, dict):
        return {"status": "failed", "output_format": "text",
                "message": f"接口返回格式异常: {type(result).__name__}", "data": {}}

    err = result.get("error")
    if err:
        return {
            "status": "failed", "output_format": "text",
            "message": f"调用失败: {str(err)[:300]}",
            "data": {
                "model": result.get("model", model),
                "base_url": result.get("base_url", base_url),
                "api_key_masked": _mask(api_key),
            },
        }

    text = result.get("content", "") or ""
    if not text.strip():
        return {
            "status": "failed", "output_format": "text",
            "message": "模型返回空内容。若为推理模型，请把 max_tokens 调大（建议 >= 1500）",
            "data": {
                "model": result.get("model", model),
                "base_url": result.get("base_url", base_url),
                "api_key_masked": _mask(api_key),
            },
        }

    return {
        "status": "success",
        "output_format": "text",
        "message": f"调用成功（{result.get('model', model)}"
                   f"{'，含图片' if has_image else ''}，耗时 {time.time() - t0:.1f}s）",
        "data": {
            "text": text,
            "model": result.get("model", model),
            "base_url": result.get("base_url", base_url),
            "api_key_masked": result.get("api_key_masked") or _mask(api_key),
            "has_image": has_image,
        },
    }


if __name__ == "__main__":
    import json
    import sys

    # 独立测试入口：不依赖注入的 _call_api
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    globals()["_call_api"] = lambda name, **kw: {
        "content": "[测试模式] 未真实调用",
        "model": kw.get("model", ""),
        "base_url": kw.get("base_url", ""),
        "api_key_masked": _mask(kw.get("api_key", "")),
    }
    print(json.dumps(execute(**payload), ensure_ascii=False))
