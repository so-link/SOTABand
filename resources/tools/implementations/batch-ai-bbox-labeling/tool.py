# === SOTABand 工具标准模板 ===
import base64
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


# ── 运行目录（由平台注入，禁止写死绝对路径或按 __file__ 层级反推）──
# SOTABAND_ROOT      系统根：代码 + 内置资源，视为只读
# SOTABAND_USER_HOME 当前用户目录：本工具的一切产物写这里
# TOOL_DIR           本工具实现目录
def _env_path(name):
    v = os.environ.get(name, "").strip()
    return Path(v) if v else None

def _find_up(path, marker):
    """兜底：向上找 marker 所在目录。只按特征文件定位，不假定固定层级。"""
    p = path
    for _ in range(12):
        if (p / marker).exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return None

_tool_dir = _env_path("TOOL_DIR") or (Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd())
_PROJECT_ROOT = _env_path("SOTABAND_ROOT") or _find_up(_tool_dir, "core") or Path.cwd()
# 用户目录：env 缺失时按「SOTABAND_HOME 根 + uid」还原，绝不退回系统根
_USER_HOME = _env_path("SOTABAND_USER_HOME") or (
    (_env_path("SOTABAND_HOME") or _PROJECT_ROOT.parent / "SB-USER")
    / (os.environ.get("SOTABAND_USER_ID", "").strip() or "0")
)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 数据目录（产物一律落用户目录，系统根随 git 分发不可写脏）──
_DATA_DIR = _USER_HOME / "data"
_DOWNLOADS_DIR = _DATA_DIR / "downloads"

def _out_dir(*parts) -> Path:
    """本工具产物目录：USER_HOME/data/<工具或子目录>/<...>，自动创建。"""
    d = _DATA_DIR.joinpath(*parts) if parts else _DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d

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
    # 资源根读取顺序：私有优先（用户「复制为副本」后的修改生效），系统公共层兜底
    _roots = [_USER_HOME, _env_path("SOTABAND_HOME") or (_PROJECT_ROOT.parent / "SB-USER")
              / (os.environ.get("SOTABAND_SYSTEM_UID", "100").strip() or "100")]
    tool_dir = next((r / "tools" / "implementations" / tool_name for r in _roots
                         if (r / "tools" / "implementations" / tool_name / "tool.py").exists()),
                    _roots[0] / "tools" / "implementations" / tool_name)
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

# DeepSeek 视觉模型（端点与密钥由平台按 provider 解析，不进工具代码）
DEEPSEEK_MODEL = "deepseek-flash"

def _image_to_base64(pil_image: Image.Image) -> str:
    buff = io.BytesIO()
    pil_image.save(buff, format="JPEG")
    return base64.b64encode(buff.getvalue()).decode("utf-8")

def _detect_objects(image: Image.Image, prompt_object: str, max_boxes: int = 50) -> list:
    """经平台统一通道调用 DeepSeek 视觉 API，返回归一化边界框列表 [(cid, x, y, w, h), ...]。

    DeepSeek-flash 原生支持用坐标锚定图像区域（视觉原语 grounding），
    但边界框可能偏移；因此只框「确实看清的独立完整目标」，宁缺毋滥。
    """
    max_edge = 640
    # 保持长宽比缩放，最长边不超过640
    w, h = image.size
    scale = max_edge / max(w, h) if max(w, h) > max_edge else 1.0
    new_size = (int(w * scale), int(h * scale))
    resized = image.resize(new_size, Image.LANCZOS)

    # 转为base64
    img_b64 = _image_to_base64(resized)

    system_prompt = (
        "你是一个精确的目标检测助手。请逐颗观察图片，框出你能清楚辨认的、"
        f"独立完整的「{prompt_object}」。\n"
        "关键要求：\n"
        "- 每个框的中心和大小必须来自你在这张图上实际看到的目标的真实位置，"
        "不要使用等间距坐标、不要使用固定宽高、不要按网格铺框。\n"
        "- 宁可少框也不要编造：只框边界清晰、独立可辨的目标；"
        "紧密堆叠成一堆、无法区分单个的区域不要框。\n"
        f"- 最多输出 {max_boxes} 个框；确实没有清晰可辨的独立目标时输出 []。\n"
        "- 只输出 JSON 数组，不要解释、不要代码块。\n"
        "每个元素格式：{\"class_id\":0,\"x_center\":0.5,\"y_center\":0.5,"
        "\"width\":0.1,\"height\":0.1}，坐标是相对整图的归一化值(0-1)。"
    )
    result = _call_api(
        "api-llm-chat-with-config",
        messages=[
            {"role": "system", "content": system_prompt},
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
                        "text": f"只检测独立的完整「{prompt_object}」，按系统要求输出 JSON 数组，最多 {max_boxes} 个。"
                    }
                ]
            }
        ],
        provider="deepseek",
        model=DEEPSEEK_MODEL,
        max_tokens=4096,
        temperature=0.0,
        # DeepSeek V4.1 Flash 默认开启思考，2048 额度会被 reasoning 吃光导致
        # content 为空；显式关闭思考，检测任务不需要推理链。
        extra_body={"thinking": {"type": "disabled"}},
    )
    if not result or result.get("error"):
        raise RuntimeError(f"大模型调用失败: {(result or {}).get('error', '无返回')}")

    content = (result.get("content") or "").strip()
    # 去除可能的 Markdown 代码块围栏（含 ```json 语言标记）
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    boxes = []
    # 优先解析 JSON 数组（新格式）
    parsed = None
    try:
        candidate = json.loads(content)
        if isinstance(candidate, list):
            parsed = candidate
        elif isinstance(candidate, dict):
            for key in ("boxes", "detections", "objects"):
                if isinstance(candidate.get(key), list):
                    parsed = candidate[key]
                    break
    except (ValueError, TypeError):
        # 兜底：从文本中逐个抠出完整的 JSON 对象（容忍前后有解释文字或截断）
        objects = re.findall(r'\{[^{}]*\}', content)
        if objects:
            parsed = []
            for obj in objects:
                try:
                    parsed.append(json.loads(obj))
                except (ValueError, TypeError):
                    continue

    if parsed is not None:
        for item in parsed:
            if not isinstance(item, dict):
                continue
            try:
                cid = int(item.get("class_id", 0))
                x = float(item["x_center"])
                y = float(item["y_center"])
                bw = float(item["width"])
                bh = float(item["height"])
                if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < bw <= 1 and 0 < bh <= 1):
                    continue
                boxes.append((cid, x, y, bw, bh))
            except (KeyError, ValueError, TypeError):
                continue
    else:
        # 兼容旧的行格式 "cid x y w h"
        for line in content.splitlines():
            parts = line.strip().split()
            if len(parts) != 5:
                continue
            try:
                cid = int(parts[0])
                x, y, bw, bh = map(float, parts[1:])
                if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < bw <= 1 and 0 < bh <= 1):
                    continue
                boxes.append((cid, x, y, bw, bh))
            except ValueError:
                continue

    # 限幅：宁缺毋滥，最多 max_boxes 个
    return boxes[:max_boxes]

def execute(**kwargs) -> dict[str, Any]:
    try:
        dataset_name = kwargs.get("dataset")
        req = kwargs.get("req")
        output_dataset = kwargs.get("output_dataset")
        if not dataset_name or not req or not output_dataset:
            return {"status": "failed", "message": "缺少必要参数 dataset, req, output_dataset"}
        try:
            max_boxes = max(1, min(500, int(kwargs.get("max_boxes", 50) or 50)))
        except (ValueError, TypeError):
            max_boxes = 50
        
        # 1. 获取数据集信息
        ds_resp = _call_api("api-data-get", name=dataset_name)
        dataset_info = ds_resp.get("dataset")
        if not dataset_info:
            return {"status": "failed", "message": "数据集不存在或无法访问"}
        data_path_src = dataset_info.get("data_path") or dataset_info.get("path")
        if not data_path_src or not Path(data_path_src).is_dir():
            return {"status": "failed", "message": "数据集不存在或无法访问"}
        src_dir = Path(data_path_src)
        
        # 2. 创建目标目录
        timestamp = time.strftime("%Y%m%d%H%M%S")
        target_dir = _DOWNLOADS_DIR / timestamp
        labeled_dir = target_dir / "labeled"
        labeled_dir.mkdir(parents=True, exist_ok=True)
        
        # 支持图片格式。兼容两种数据集布局：
        #   1) 图片在根目录（本工具自身产物 / 原始数据空间导入）
        #   2) 图片在 images/ 子目录（图像清洗工具产出的 YOLO 标准布局）
        # 先扫根目录，没有再扫 images/；都不认才算失败。
        img_exts = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}

        def _scan(d: Path) -> list:
            return sorted(
                f for f in d.iterdir()
                if f.is_file() and f.suffix.lower() in img_exts
            ) if d.is_dir() else []

        img_files = _scan(src_dir)
        if not img_files:
            img_files = _scan(src_dir / "images")
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
                boxes = _detect_objects(orig_image, req, max_boxes=max_boxes)
                
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

                # 复制原始图片到 target_dir（用于训练集）
                train_img_path = target_dir / img_path.name
                from shutil import copyfile
                copyfile(img_path, train_img_path)
                
                # 保存标签文件
                label_path = target_dir / (img_path.stem + ".txt")
                label_path.write_text("\n".join(yolo_lines), encoding="utf-8")
                
                if processed == 0:
                    first_labeled_image = str(labeled_img_path)
                processed += 1
                collected_formats.add(img_path.suffix.lower().lstrip('.'))
                collected_formats.add("txt")
            except Exception as e:
                return {"status": "failed", "message": f"图片处理失败 ({img_path.name}): {e!s}"}
        
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
        return {"status": "failed", "message": f"执行异常: {e!s}"}