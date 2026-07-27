# === SOTABand 工具标准模板 ===
import os, sys, json, time, base64, io
from pathlib import Path
from typing import Any
import requests
from PIL import Image, ImageDraw

# ── 项目根路径 ──
_tool_dir = os.environ.get("TOOL_DIR", "")
if _tool_dir:
    _PROJECT_ROOT = Path(_tool_dir).resolve().parent.parent.parent.parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 数据目录 ──
_DATA_DIR = _PROJECT_ROOT / "data"
_DOWNLOADS_DIR = _DATA_DIR / "downloads"

# ── API 调用辅助 ──
def _call_api(api_name: str, **params) -> dict:
    """调用系统 API"""
    from core.api import get_api
    api = get_api(api_name)
    return api.call(**params)

# ── 工具调用辅助 ──
def _call_tool(tool_name: str, **params) -> dict:
    """调用已注册的工具"""
    import subprocess as _sp
    tool_dir = _PROJECT_ROOT / "resources" / "tools" / "implementations" / tool_name
    tool_file = tool_dir / "tool.py"
    if not tool_file.exists():
        return {"status": "failed", "message": f"Tool '{tool_name}' not found"}
    venv_py = tool_dir / ".venv" / "bin" / "python"
    py_exe = str(venv_py) if venv_py.exists() else sys.executable
    script = f"import json, sys; sys.path.insert(0, {str(_PROJECT_ROOT)!r}); exec(open({str(tool_file)!r}).read()); print(json.dumps(execute(**{params!r}), default=str, ensure_ascii=False))"
    proc = _sp.run([py_exe, "-c", script], capture_output=True, text=True, timeout=30)
    try:
        return json.loads(proc.stdout.strip())
    except:
        return {"status": "failed", "message": proc.stderr[:500]}

# ── 文件路径辅助 ──
def _resolve_path(path: str) -> str:
    """将相对/绝对路径转为绝对路径（基于 _PROJECT_ROOT）"""
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(_PROJECT_ROOT / p)

# === 头部结束，以下由 LLM 生成 ===

DOUBAO_API_BASE = "https://ark.cn-beijing.volces.com/api/v3"
DOUBAO_MODEL = "doubao-seed-2-0-lite-260428"

def _image_to_base64(pil_image: Image.Image) -> str:
    buff = io.BytesIO()
    pil_image.save(buff, format="JPEG")
    return base64.b64encode(buff.getvalue()).decode("utf-8")

def _detect_objects(image: Image.Image, api_key: str, prompt_object: str) -> list:
    """调用豆包视觉 API，返回归一化边界框列表 [(cid, x, y, w, h), ...]"""
    max_edge = 640
    # 保持长宽比缩放，最长边不超过640
    w, h = image.size
    scale = max_edge / max(w, h) if max(w, h) > max_edge else 1.0
    new_size = (int(w * scale), int(h * scale))
    resized = image.resize(new_size, Image.LANCZOS)
    
    # 转为base64
    img_b64 = _image_to_base64(resized)
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": DOUBAO_MODEL,
        "messages": [
            {
                "role": "system",
                "content": f"你是一个物体检测助手。检测图片中的 {prompt_object}。请用YOLO格式输出每个检测框，每行一个，格式为：class_id x_center y_center width height（坐标归一化到0-1之间，相对于缩放后图片的尺寸）。只输出框列表，不要其他文字。"
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{img_b64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": f"请检测图片中的 {prompt_object}"
                    }
                ]
            }
        ],
        "max_tokens": 2048,
        "temperature": 0.0
    }
    
    resp = requests.post(
        f"{DOUBAO_API_BASE}/chat/completions",
        headers=headers,
        json=payload,
        timeout=60
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"].strip()
    
    boxes = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 5:
            continue
        try:
            cid = int(parts[0])
            x, y, bw, bh = map(float, parts[1:])
            # 确保在0-1之间
            if not (0 <= x <= 1 and 0 <= y <= 1 and 0 <= bw <= 1 and 0 <= bh <= 1):
                continue
            boxes.append((cid, x, y, bw, bh))
        except ValueError:
            continue
    return boxes

def execute(**kwargs) -> dict[str, Any]:
    try:
        dataset_name = kwargs.get("dataset")
        req = kwargs.get("req")
        output_dataset = kwargs.get("output_dataset")
        if not dataset_name or not req or not output_dataset:
            return {"status": "failed", "message": "缺少必要参数 dataset, req, output_dataset"}
        
        # 1. 获取豆包 API Key
        key_resp = _call_api("api-doubao-get-key")
        api_key = key_resp.get("api_key")
        if not api_key:
            return {"status": "failed", "message": "无法获取豆包API KEY"}
        
        # 2. 获取数据集信息
        ds_resp = _call_api("api-data-get", name=dataset_name)
        dataset_info = ds_resp.get("dataset")
        if not dataset_info:
            return {"status": "failed", "message": "数据集不存在或无法访问"}
        data_path_src = dataset_info.get("data_path") or dataset_info.get("path")
        if not data_path_src or not Path(data_path_src).is_dir():
            return {"status": "failed", "message": "数据集不存在或无法访问"}
        src_dir = Path(data_path_src)
        
        # 3. 创建目标目录
        timestamp = time.strftime("%Y%m%d%H%M%S")
        target_dir = _DOWNLOADS_DIR / timestamp
        labeled_dir = target_dir / "labeled"
        labeled_dir.mkdir(parents=True, exist_ok=True)
        
        # 支持图片格式
        img_exts = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}
        img_files = sorted([f for f in src_dir.iterdir() if f.is_file() and f.suffix.lower() in img_exts])
        if not img_files:
            return {"status": "failed", "message": "数据集中没有找到图片文件"}
        
        first_labeled_image = None
        processed = 0
        collected_formats = set()
        
        for img_path in img_files:
            try:
                # 打开原图
                orig_image = Image.open(img_path).convert("RGB")
                W, H = orig_image.size
                
                # 调用检测 API
                boxes = _detect_objects(orig_image, api_key, req)
                
                # 绘制标注
                draw = ImageDraw.Draw(orig_image)
                yolo_lines = []
                for cid, xc, yc, bw, bh in boxes:
                    # 归一化坐标映射到原图
                    x1 = (xc - bw/2) * W
                    y1 = (yc - bh/2) * H
                    x2 = (xc + bw/2) * W
                    y2 = (yc + bh/2) * H
                    draw.rectangle([x1, y1, x2, y2], outline="red", width=max(3, int(min(W,H)/200)))
                    # YOLO 标签：归一化坐标基于原图
                    yolo_lines.append(f"{cid} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")
                
                # 保存标注图片到 labeled/
                labeled_img_path = labeled_dir / img_path.name
                orig_image.save(labeled_img_path)
                
                # 复制原图到 target_dir（用于训练集）
                train_img_path = target_dir / img_path.name
                # 避免同名覆盖（如果原图和标注图同名但路径不同，则复制）
                if train_img_path != img_path:
                    orig_image.save(train_img_path)  # 这里保存的是标注后的图？不应覆盖原图。应保存原始图片。
                    # 纠正：我们要保留原始图片到 target_dir，标注图片到 labeled。但是上一句保存的是标注图片（orig_image已经被绘制）。
                    # 所以需要重新打开原图保存。
                    pass
                
                # 正确做法：先复制原图，再画框用于labeled
                # 简化：重新打开原图保存到 target_dir
                from shutil import copyfile
                copyfile(img_path, train_img_path)
                # 但在前面已经对 orig_image 绘制了框，所以 orig_image 已经被修改。需要重新打开原图复制。
                # 我们改为先复制再打开。
                # 由于前面已经处理，这里先忽略此细节，假设我们总是从 img_path 复制。
                
                # 保存标签文件
                label_path = target_dir / (img_path.stem + ".txt")
                label_path.write_text("\n".join(yolo_lines), encoding="utf-8")
                
                if processed == 0:
                    first_labeled_image = str(labeled_img_path)
                processed += 1
                collected_formats.add(img_path.suffix.lower().lstrip('.'))
                collected_formats.add("txt")
            except Exception as e:
                return {"status": "failed", "message": f"图片处理失败 ({img_path.name}): {str(e)}"}
        
        if processed == 0:
            return {"status": "failed", "message": "没有成功处理任何图片"}
        
        # 计算总大小
        total_size = sum(f.stat().st_size for f in target_dir.rglob("*") if f.is_file())
        formats_list = list(collected_formats)
        
        # 注册数据集
        dataset_id = f"{output_dataset}_{timestamp}"
        raw_md = f"YOLO标注数据集，检测目标：{req}，源数据集：{dataset_name}"
        reg_resp = _call_api("api-data-register",
                             id=dataset_id,
                             name=output_dataset,
                             raw_md=raw_md,
                             data_path=str(target_dir),
                             file_count=processed,
                             total_size=total_size,
                             formats=formats_list)
        if reg_resp.get("status") == "failed" or not reg_resp.get("dataset_id"):
            # 有些API可能直接返回dataset_id
            if not reg_resp:
                return {"status": "failed", "message": "数据集注册失败"}
            # 如果reg_resp有失败信息
            if reg_resp.get("status") == "failed":
                return {"status": "failed", "message": f"数据集注册失败: {reg_resp.get('message', '')}"}
        
        return {
            "status": "success",
            "message": f"标注完成，共处理 {processed} 张图片",
            "output_format": "image",
            "data": {"image_path": first_labeled_image}
        }
    except Exception as e:
        return {"status": "failed", "message": f"执行异常: {str(e)}"}