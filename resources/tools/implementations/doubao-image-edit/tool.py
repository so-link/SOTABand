import os
import time
import base64
import re
from pathlib import Path
from typing import Any, Dict

# Attempt to use the project's root path if defined; otherwise use current working directory
try:
    from config import _PROJECT_ROOT  # type: ignore[import-not-found]
except ImportError:
    _PROJECT_ROOT = Path.cwd()

from core.api import get_api


def execute(**kwargs: Any) -> Dict[str, Any]:
    """
    图片编辑工具（Doubao）
    通过火山引擎 Doubao 图片编辑模型，根据原始图片和编辑描述生成编辑后的图片。
    """

    # ------------------------------------------------------------------
    # 1. Input extraction with defaults
    # ------------------------------------------------------------------
    image_path = kwargs.get("image_path")
    prompt = kwargs.get("prompt")
    negative_prompt = kwargs.get("negative_prompt", "模糊，水印，文字，畸形")
    strength = kwargs.get("strength", 0.7)
    size = kwargs.get("size", "2048x2048")

    # ------------------------------------------------------------------
    # 2. Parameter validation
    # ------------------------------------------------------------------
    if not image_path or not isinstance(image_path, str):
        return {
            "status": "failed",
            "output_format": "image",
            "message": "参数错误：image_path 必须提供且为非空字符串",
            "data": {}
        }
    if not prompt or not isinstance(prompt, str) or not prompt.strip():
        return {
            "status": "failed",
            "output_format": "image",
            "message": "参数错误：prompt 不能为空",
            "data": {}
        }
    # strength range check
    try:
        strength = float(strength)
    except (TypeError, ValueError):
        return {
            "status": "failed",
            "output_format": "image",
            "message": "参数错误：strength 必须为 0~1 之间的数字",
            "data": {}
        }
    if not (0.0 <= strength <= 1.0):
        return {
            "status": "failed",
            "output_format": "image",
            "message": "参数错误：strength 必须在 0.0 到 1.0 之间",
            "data": {}
        }
    # size format check (e.g., "2048x2048")
    if not re.fullmatch(r"\d+x\d+", re.sub(r"\s", "", str(size))):
        return {
            "status": "failed",
            "output_format": "image",
            "message": "参数错误：size 格式不正确，应为 '宽x高'，例如 '2048x2048'",
            "data": {}
        }

    # ------------------------------------------------------------------
    # 3. Check input image file
    # ------------------------------------------------------------------
    img_path = Path(image_path).expanduser().resolve()
    if not img_path.is_file():
        return {
            "status": "failed",
            "output_format": "image",
            "message": f"图片文件不存在：{image_path}",
            "data": {}
        }
    # Check file size (>10MB warning but still proceed)
    file_size_mb = img_path.stat().st_size / (1024 * 1024)
    if file_size_mb > 10:
        # According to spec we should give a reasonable prompt, we can still continue
        # but return a warning? Return success later? We'll inject a note.
        size_warning = f"输入图片大小为 {file_size_mb:.1f}MB，可能影响处理速度或失败，建议压缩。"
    else:
        size_warning = ""

    # Read and convert image to base64
    try:
        with open(img_path, "rb") as f:
            image_base64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return {
            "status": "failed",
            "output_format": "image",
            "message": f"读取图片文件失败：{str(e)}",
            "data": {}
        }

    # ------------------------------------------------------------------
    # 4. Get API config
    try:
        config_api = get_api("api-llm-get-config")
        config = config_api.call(provider="doubao")
    except Exception as e:
        return {"status": "failed", "output_format": "image", "message": f"无法获取配置: {e}", "data": {}}

    api_key = config.get("api_key")
    base_url = config.get("base_url", "https://ark.cn-beijing.volces.com/api/v3")
    if not api_key:
        return {"status": "failed", "output_format": "image", "message": "缺少 API Key", "data": {}}

    # 5. Call Doubao image edit API
    import requests
    endpoint = f"{base_url.rstrip('/')}/images/generations"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    payload = {
        "model": "doubao-seedream-5-0-260128",
        "image": f"data:image/png;base64,{image_base64}",
        "prompt": prompt.strip(),
        "size": str(size).replace(" ", ""),
        "n": 1,
        "response_format": "url",
    }
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt.strip()
    if strength:
        payload["strength"] = float(strength)

    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=120)
        if resp.status_code != 200:
            return {"status": "failed", "output_format": "image", "message": f"API错误: {resp.text[:200]}", "data": {}}
        edit_data = resp.json()
        output_url = None
        if "data" in edit_data and edit_data["data"]:
            output_url = edit_data["data"][0].get("url", "")

        if not output_url:
            return {"status": "failed", "output_format": "image", "message": "未获取到编辑后的图片", "data": {}}

        # Download image
        img_resp = requests.get(output_url, timeout=60)
        img_bytes = img_resp.content

    except Exception as e:
        return {
            "status": "failed",
            "output_format": "image",
            "message": f"图片编辑 API 调用失败：{str(e)}",
            "data": {}
        }

    # ------------------------------------------------------------------
    # 6. Save output image to local download folder
    # ------------------------------------------------------------------
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    download_dir = _PROJECT_ROOT / "data" / "downloads" / timestamp
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {
            "status": "failed",
            "output_format": "image",
            "message": f"创建下载目录失败：{str(e)}",
            "data": {}
        }

    output_filename = f"edited_{timestamp}.jpg"
    output_path = download_dir / output_filename
    try:
        with open(output_path, "wb") as f:
            f.write(img_bytes)
    except Exception as e:
        return {
            "status": "failed",
            "output_format": "image",
            "message": f"保存图片失败：{str(e)}",
            "data": {}
        }

    # Build final message, possibly appending size warning
    final_message = "图片编辑成功"
    if size_warning:
        final_message += f"（提示：{size_warning}）"

    return {
        "status": "success",
        "output_format": "image",
        "message": final_message,
        "data": {
            "image_path": str(output_path.resolve())
        }
    }