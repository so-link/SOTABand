# === SOTABand 工具标准模板 ===
import os, sys, json, time, re, base64, shutil, struct, io
from pathlib import Path
from typing import Any, Optional, List, Dict
import requests

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

# 需要安装的依赖由系统管理，这里只做导入检查
try:
    from PIL import Image
except ImportError:
    Image = None
try:
    import yaml
except ImportError:
    yaml = None

# 支持的图片后缀
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp', '.gif'}


def _is_image(file_path: str) -> bool:
    """判断是否为支持的图片文件"""
    return Path(file_path).suffix.lower() in IMAGE_EXTENSIONS


def _get_image_size(image_path: str):
    """获取图片宽高，优先使用PIL，失败时使用标准库解析"""
    if Image is not None:
        with Image.open(image_path) as img:
            return img.size  # (width, height)

    # 无PIL时的后备方案：解析文件头
    with open(image_path, 'rb') as f:
        header = f.read(32)

    # JPEG (SOF0/1/2 段)
    if header[:2] == b'\xff\xd8':
        f.seek(2)
        while True:
            marker = f.read(2)
            if len(marker) < 2:
                break
            if marker[0] != 0xFF:
                break
            tag = marker[1]
            if tag == 0x01 or (0xD0 <= tag <= 0xD7):
                continue
            if 0xC0 <= tag <= 0xC3 or 0xC5 <= tag <= 0xC7 or 0xC9 <= tag <= 0xCF:
                seg_len = struct.unpack('>H', f.read(2))[0] - 2
                precision, height, width = struct.unpack('>BHH', f.read(5))
                return (width, height)
            else:
                seg_len = struct.unpack('>H', f.read(2))[0] - 2
                f.seek(seg_len, 1)

    # PNG
    if header[:8] == b'\x89PNG\r\n\x1a\n':
        f.seek(16)
        width, height = struct.unpack('>II', f.read(8))
        return (width, height)

    # GIF
    if header[:6] in [b'GIF87a', b'GIF89a']:
        width, height = struct.unpack('<HH', header[6:10])
        return (width, height)

    # BMP
    if header[:2] == b'BM':
        width = struct.unpack('<I', header[18:22])[0]
        height = struct.unpack('<I', header[22:26])[0]
        return (width, height)

    raise ValueError(f"无法解析图片尺寸: {image_path}")


def _image_to_base64(image_path: str) -> str:
    """读取图片并转为base64 data uri"""
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    ext = Path(image_path).suffix.lower()
    mime = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.bmp': 'image/bmp',
        '.tiff': 'image/tiff',
        '.tif': 'image/tiff',
        '.webp': 'image/webp',
        '.gif': 'image/gif'
    }.get(ext, 'image/jpeg')
    return f"data:{mime};base64,{data}"


def _call_doubao_vision(api_key: str, base_url: str, model: str, image_path: str, req: str) -> dict:
    """调用豆包大模型视觉接口，返回解析后的响应JSON"""
    image_b64 = _image_to_base64(image_path)
    prompt = (
        f"检测图像中的“{req}”。"
        "请返回一个JSON对象，格式为 {\"objects\": [{\"class\": \"类别名\", \"bbox\": [x_min, y_min, x_max, y_max]}, ...]}，"
        "其中bbox坐标为像素整数。如果未检测到任何目标，返回 {\"objects\": []}。"
        "不要添加任何其他文字说明，只返回严格JSON。"
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_b64, "detail": "auto"}}
                ]
            }
        ],
        "temperature": 0.1
    }
    # 拼接完整的URL：避免重复添加 /chat/completions
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        # 将HTTP错误详细信息返回，便于排查
        return {"objects": [], "_error": f"HTTP {resp.status_code}: {resp.reason}, {resp.text[:200] if resp.text else ''}"}
    try:
        result = resp.json()
    except ValueError:
        return {"objects": [], "_error": "响应不是有效JSON"}
    content = result["choices"][0]["message"]["content"]
    # 尝试从回复中提取JSON
    json_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', content)
    if json_match:
        content = json_match.group(1)
    else:
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            content = json_match.group()
    try:
        parsed = json.loads(content)
        if "objects" not in parsed:
            if isinstance(parsed, list):
                parsed = {"objects": parsed}
            else:
                parsed = {"objects": []}
        return parsed
    except json.JSONDecodeError:
        return {"objects": [], "_error": f"JSON解析失败，原始返回: {content[:200]}"}


def _parse_req_classes(req: str) -> List[str]:
    """解析检测目标字符串，返回类别列表"""
    parts = re.split(r'[,，、\s]+', req)
    return [p.strip() for p in parts if p.strip()]


def _build_yolo_bbox(class_name: str, bbox: list, image_width: int, image_height: int, class_list: list) -> Optional[str]:
    """根据检测到的类别和bbox生成YOLO格式的一行字符串"""
    if class_name not in class_list:
        return None
    class_id = class_list.index(class_name)
    x_min, y_min, x_max, y_max = bbox
    dw = 1.0 / image_width
    dh = 1.0 / image_height
    x_center = (x_min + x_max) / 2.0 * dw
    y_center = (y_min + y_max) / 2.0 * dh
    box_w = (x_max - x_min) * dw
    box_h = (y_max - y_min) * dh
    return f"{class_id} {x_center:.6f} {y_center:.6f} {box_w:.6f} {box_h:.6f}"


def _generate_tree(dir_path: str) -> str:
    """生成目录树文本"""
    def recurse(current: str, prefix: str) -> List[str]:
        lines = []
        try:
            contents = sorted(os.listdir(current))
        except PermissionError:
            return []
        for i, name in enumerate(contents):
            path = os.path.join(current, name)
            is_last = (i == len(contents) - 1)
            connector = "└── " if is_last else "├── "
            if os.path.isdir(path):
                lines.append(f"{prefix}{connector}{name}/")
                extension = "    " if is_last else "│   "
                lines.extend(recurse(path, prefix + extension))
            else:
                lines.append(f"{prefix}{connector}{name}")
        return lines

    base = Path(dir_path)
    if not base.exists():
        return f"{base.name}/"
    tree_lines = [base.name + "/"] + recurse(dir_path, "")
    return "\n".join(tree_lines)


def execute(**kwargs) -> Dict[str, Any]:
    """主执行函数"""
    dataset = kwargs.get("dataset", "")
    req = kwargs.get("req", "")

    if not dataset or not req:
        return {
            "status": "failed",
            "message": "缺少必要参数: dataset 和 req 不能为空",
            "output_format": "text",
            "data": {"text": ""}
        }

    # 1. 获取数据集信息
    try:
        ds_info_raw = _call_api("api-data-get", name=dataset)
        if isinstance(ds_info_raw, dict) and "dataset" in ds_info_raw:
            ds_info = ds_info_raw["dataset"]
        else:
            ds_info = ds_info_raw
        data_path = ds_info.get("data_path", "")
        if not data_path:
            return {
                "status": "failed",
                "message": f"数据集 {dataset} 不存在或未包含 data_path 字段",
                "output_format": "text",
                "data": {"text": ""}
            }
        data_path = _resolve_path(data_path)
    except Exception as e:
        return {
            "status": "failed",
            "message": f"获取数据集信息失败: {str(e)}",
            "output_format": "text",
            "data": {"text": ""}
        }

    # 2. 获取豆包API KEY，并确保模型支持视觉（chat/completions）
    try:
        key_info = _call_api("api-doubao-get-key")
        api_key = key_info.get("api_key", "")
        base_url = key_info.get("base_url", "")
        model = key_info.get("model", "doubao-seed-2-1-pro-260628")
        if not api_key or not base_url:
            return {
                "status": "failed",
                "message": "无法获取豆包API KEY，请检查系统API配置",
                "output_format": "text",
                "data": {"text": ""}
            }
        # 如果获取到的模型不支持多模态（如seedream系列），自动替换为视觉模型
        if model and "seedream" in model.lower():
            model = "doubao-seed-2-1-pro-260628"
    except Exception as e:
        return {
            "status": "failed",
            "message": f"获取豆包API KEY失败: {str(e)}",
            "output_format": "text",
            "data": {"text": ""}
        }

    # 3. 解析类别列表
    class_list = _parse_req_classes(req)
    num_classes = len(class_list)

    # 4. 扫描图片
    if not os.path.exists(data_path):
        return {
            "status": "failed",
            "message": f"数据路径不存在: {data_path}",
            "output_format": "text",
            "data": {"text": ""}
        }

    image_files = []
    for root, dirs, files in os.walk(data_path):
        for f in files:
            if _is_image(f):
                full_path = os.path.join(root, f)
                image_files.append(full_path)

    if not image_files:
        images_dir = os.path.join(data_path, "images")
        labels_dir = os.path.join(data_path, "labels")
        os.makedirs(images_dir, exist_ok=True)
        os.makedirs(labels_dir, exist_ok=True)
        yaml_path = os.path.join(data_path, "data.yaml")
        if yaml is not None:
            yaml_content = {
                "path": data_path,
                "train": "images",
                "val": "images",
                "nc": num_classes,
                "names": class_list
            }
            with open(yaml_path, "w", encoding="utf-8") as fy:
                yaml.dump(yaml_content, fy, allow_unicode=True, default_flow_style=False)
        else:
            yaml_str = f"path: {data_path}\ntrain: images\nval: images\nnc: {num_classes}\nnames: {class_list}\n"
            with open(yaml_path, "w", encoding="utf-8") as fy:
                fy.write(yaml_str)
        tree_text = _generate_tree(data_path)
        return {
            "status": "success",
            "message": "未找到任何图片文件",
            "output_format": "text",
            "data": {"text": tree_text}
        }

    # 5. 创建新的目录结构
    images_dir = os.path.join(data_path, "images")
    labels_dir = os.path.join(data_path, "labels")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(labels_dir, exist_ok=True)

    total_images = len(image_files)
    success_count = 0
    detection_fail_images = 0
    failed_calls = 0
    error_messages = []

    for img_path in image_files:
        try:
            detection_result = _call_doubao_vision(api_key, base_url, model, img_path, req)
            error_msg = detection_result.pop("_error", None)
            if error_msg:
                failed_calls += 1
                error_messages.append(f"处理 {os.path.basename(img_path)} 时出错: {error_msg}")
                # 尽量将图片移动到 images 目录，但不生成标签
                try:
                    shutil.move(img_path, os.path.join(images_dir, os.path.basename(img_path)))
                except Exception:
                    pass
                continue

            objects = detection_result.get("objects", [])
            if not objects:
                detection_fail_images += 1
                shutil.move(img_path, os.path.join(images_dir, os.path.basename(img_path)))
                continue

            # 获取图片尺寸
            width, height = _get_image_size(img_path)

            # 生成标签内容
            label_lines = []
            for obj in objects:
                class_name = obj.get("class", "").strip()
                bbox = obj.get("bbox", [])
                if not (isinstance(bbox, list) and len(bbox) == 4):
                    continue
                line = _build_yolo_bbox(class_name, bbox, width, height, class_list)
                if line:
                    label_lines.append(line)

            # 移动图片
            dst_img = os.path.join(images_dir, os.path.basename(img_path))
            shutil.move(img_path, dst_img)

            if label_lines:
                label_name = Path(img_path).stem + ".txt"
                label_path = os.path.join(labels_dir, label_name)
                with open(label_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(label_lines) + "\n")
                success_count += 1
            else:
                detection_fail_images += 1

        except Exception as e:
            failed_calls += 1
            error_messages.append(f"处理 {os.path.basename(img_path)} 时出错: {str(e)}")
            try:
                shutil.move(img_path, os.path.join(images_dir, os.path.basename(img_path)))
            except Exception:
                pass

    # 6. 生成 data.yaml
    yaml_path = os.path.join(data_path, "data.yaml")
    if yaml is not None:
        yaml_content = {
            "path": data_path,
            "train": "images",
            "val": "images",
            "nc": num_classes,
            "names": class_list
        }
        with open(yaml_path, "w", encoding="utf-8") as fy:
            yaml.dump(yaml_content, fy, allow_unicode=True, default_flow_style=False)
    else:
        yaml_str = f"path: {data_path}\ntrain: images\nval: images\nnc: {num_classes}\nnames: {class_list}\n"
        with open(yaml_path, "w", encoding="utf-8") as fy:
            fy.write(yaml_str)

    # 7. 生成目录树
    tree_text = _generate_tree(data_path)

    # 8. 构建结果消息
    msg_parts = [
        f"总图片数: {total_images}",
        f"成功检测到目标: {success_count}",
        f"未检测到目标: {detection_fail_images}",
    ]
    if failed_calls > 0:
        msg_parts.append(f"调用失败次数: {failed_calls}")
        if error_messages:
            msg_parts.append("错误详情: " + "; ".join(error_messages[:3]))

    if failed_calls == total_images:
        status = "failed"
        msg_parts.insert(0, "大模型调用全部失败;")
    elif success_count == 0 and detection_fail_images == total_images:
        status = "failed"
        msg_parts.insert(0, "大模型调用成功但未检测到任何目标;")
    else:
        status = "success"

    message = " ".join(msg_parts)

    return {
        "status": status,
        "message": message,
        "output_format": "text",
        "data": {"text": tree_text}
    }


if __name__ == "__main__":
    test_args = {
        "dataset": "test_dataset",
        "req": "猫"
    }
    res = execute(**test_args)
    print(json.dumps(res, ensure_ascii=False, indent=2))