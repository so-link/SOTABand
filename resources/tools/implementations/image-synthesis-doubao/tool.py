import os
import sys
from pathlib import Path

_tool_dir = os.environ.get("TOOL_DIR", "")
if _tool_dir:
    _PROJECT_ROOT = Path(_tool_dir).resolve().parent.parent.parent.parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os
import json
import time
import uuid
import datetime
import requests
from typing import Any, Dict

from core.api import get_api


def execute(
    prompt: str,
    num_images: int,
    dataset_name: str
) -> Dict[str, Any]:
    """
    图片合成工具（豆包大模型）

    Args:
        prompt: 图片生成的自然语言描述要求
        num_images: 需要合成的图片数量（正整数）
        dataset_name: 数据集名称，用于注册图片集合

    Returns:
        dict: 包含 status, message, output_format, data 字段
              - status: "success" 或 "failed"
              - message: 结果说明
              - output_format: "image"
              - data: 成功时 {"image_path": "..."}
    """
    # -------------------- 参数校验 --------------------
    if not isinstance(num_images, int) or num_images <= 0:
        return {
            "status": "failed",
            "message": "参数无效",
            "output_format": "image",
            "data": {}
        }
    if not prompt or not isinstance(prompt, str):
        return {
            "status": "failed",
            "message": "参数无效",
            "output_format": "image",
            "data": {}
        }
    if not dataset_name or not isinstance(dataset_name, str):
        return {
            "status": "failed",
            "message": "参数无效",
            "output_format": "image",
            "data": {}
        }

    # -------------------- 1. 获取 LLM 配置 --------------------
    try:
        llm_config_api = get_api("api-llm-get-config")
        llm_config = llm_config_api.call(provider="doubao")
    except Exception as e:
        return {
            "status": "failed",
            "message": f"获取LLM配置失败: {str(e)}",
            "output_format": "image",
            "data": {}
        }

    # 提取 API Key（以及可能的自定义 endpoint，若无则使用默认）
    api_key = llm_config.get("api_key") if isinstance(llm_config, dict) else None
    if not api_key:
        return {
            "status": "failed",
            "message": "获取LLM配置失败: 缺少 api_key",
            "output_format": "image",
            "data": {}
        }

    # 豆包图片生成 API endpoint
    image_api_url = llm_config.get("endpoint", "https://ark.cn-beijing.volces.com/api/v3/images/generations")

    # -------------------- 2. 调用豆包生成图片 --------------------
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "doubao-seedream-5-0-260128",
        "prompt": prompt,
        "n": 1,
        "response_format": "url"
    }

    # 逐张请求生成
    image_urls = []
    for _ in range(num_images):
        try:
            resp = requests.post(image_api_url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            result_json = resp.json()
        except requests.RequestException as e:
            return {"status": "failed", "message": f"图片生成失败: 网络错误 - {str(e)}", "output_format": "image", "data": {}}
        except json.JSONDecodeError:
            return {"status": "failed", "message": "图片生成失败: 响应解析错误", "output_format": "image", "data": {}}

        data_list = result_json.get("data")
        if not data_list or not isinstance(data_list, list):
            continue
        for item in data_list:
            url = item.get("url")
            if url:
                image_urls.append(url)

    if len(image_urls) == 0:
        return {"status": "failed", "message": "图片生成失败: 未获取到有效图片 URL", "output_format": "image", "data": {}}
    actual_num = len(image_urls)

    # -------------------- 3. 创建本地目录 --------------------
    # 微秒级时间戳保证唯一性
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
    download_dir = str(_PROJECT_ROOT / "data" / "downloads" / timestamp)
    try:
        os.makedirs(download_dir, exist_ok=True)
    except OSError as e:
        return {
            "status": "failed",
            "message": f"文件操作失败: 无法创建目录 {download_dir} - {str(e)}",
            "output_format": "image",
            "data": {}
        }

    # -------------------- 4. 下载图片并保存 --------------------
    saved_files = []  # 保存文件路径
    file_formats = []  # 保存每个文件的扩展名（无点）
    total_size = 0

    for idx, url in enumerate(image_urls):
        try:
            img_resp = requests.get(url, timeout=30)
            img_resp.raise_for_status()
        except Exception as e:
            return {
                "status": "failed",
                "message": f"文件操作失败: 下载第 {idx+1} 张图片时出错 - {str(e)}",
                "output_format": "image",
                "data": {}
            }

        # 确定文件扩展名
        content_type = img_resp.headers.get("Content-Type", "").lower()
        if "png" in content_type:
            ext = "png"
        elif "jpeg" in content_type or "jpg" in content_type:
            ext = "jpg"
        elif "webp" in content_type:
            ext = "webp"
        else:
            ext = "png"  # 默认

        # 文件名：img_序号.扩展名
        filename = f"img_{idx+1}.{ext}"
        filepath = os.path.join(download_dir, filename)

        try:
            with open(filepath, "wb") as f:
                f.write(img_resp.content)
        except OSError as e:
            return {
                "status": "failed",
                "message": f"文件操作失败: 写入文件 {filepath} 时出错 - {str(e)}",
                "output_format": "image",
                "data": {}
            }

        saved_files.append(filepath)
        file_formats.append(ext)
        total_size += len(img_resp.content)

    first_image_path = saved_files[0] if saved_files else ""

    # -------------------- 5. 注册数据集 --------------------
    # 生成唯一 id（使用时间戳的一部分或 uuid）
    dataset_id = f"ds_{timestamp}"
    raw_md = json.dumps({
        "prompt": prompt,
        "num_images": num_images,
        "generated_at": timestamp
    }, ensure_ascii=False)

    try:
        data_register_api = get_api("api-data-register")
        data_register_api.call(
            id=dataset_id,
            name=dataset_name,
            raw_md=raw_md,
            data_path=download_dir,
            file_count=actual_num,
            total_size=total_size,
            formats=file_formats,
        )
    except Exception as e:
        # 注册失败但文件已保留，仍然返回 failed
        return {
            "status": "failed",
            "message": f"数据集注册失败: {str(e)}",
            "output_format": "image",
            "data": {}
        }

    # -------------------- 6. 返回结果 --------------------
    return {
        "status": "success",
        "message": f"已生成{actual_num}张图片并注册数据集",
        "output_format": "image",
        "data": {
            "image_path": first_image_path
        }
    }