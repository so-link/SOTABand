
# === SOTABand 工具标准模板 ===
import os, sys, json, time, base64
from pathlib import Path
from typing import Any
import requests

# ── 项目根路径 ──
_tool_dir = os.environ.get("TOOL_DIR", "")
if _tool_dir:
    _PROJECT_ROOT = Path(_tool_dir).resolve().parent.parent.parent.parent
else:
    # 更加健壮的项目根确定方式：优先通过标记文件/目录查找
    def _find_project_root() -> Path:
        start = Path(__file__).resolve().parent
        # 如果当前脚本所在目录本身看起来就是项目根（有 data 子目录）
        if (start / "data").is_dir():
            return start
        # 向上寻找包含 core 目录或 setup.py 或 data 目录的父目录
        for p in start.parents:
            if (p / "core").is_dir() or (p / "setup.py").exists() or (p / "data").is_dir():
                return p
        # 如果都没找到，则认为脚本所在目录就是项目根（针对工具直接放在项目根运行等情况）
        return start

    _PROJECT_ROOT = _find_project_root()

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

def execute(**kwargs) -> dict[str, Any]:
    """编辑图片工具主函数"""
    try:
        dataset = kwargs.get("dataset", "")
        req = kwargs.get("req", "")
        output_dataset = kwargs.get("output_dataset", "")

        # 参数校验
        if not dataset:
            return {"status": "failed", "message": "缺少参数：dataset"}
        if not req:
            return {"status": "failed", "message": "缺少参数：req"}
        if not output_dataset:
            return {"status": "failed", "message": "缺少参数：output_dataset"}

        # 1. 获取豆包 API KEY 和模型配置
        try:
            api_info = _call_api("api-doubao-get-key")
        except Exception as e:
            return {"status": "failed", "message": f"无法获取豆包API KEY: {str(e)}"}

        api_key = api_info.get("api_key", "")
        base_url = api_info.get("base_url", "https://ark.cn-beijing.volces.com/api/v3")
        model = api_info.get("model", "doubao-seedream-5-0-lite-260128")

        if not api_key:
            return {"status": "failed", "message": "无法获取豆包API KEY"}

        # 2. 获取数据集信息
        try:
            dataset_info = _call_api("api-data-get", name=dataset)
        except Exception as e:
            return {"status": "failed", "message": f"获取数据集信息失败: {str(e)}"}

        ds = dataset_info.get("dataset", {})
        if not ds:
            return {"status": "failed", "message": f"数据集 '{dataset}' 不存在或返回为空"}

        data_path = ds.get("data_path", ds.get("path", ""))
        if not data_path:
            return {"status": "failed", "message": f"数据集 '{dataset}' 缺少路径信息"}
        data_path = Path(data_path)
        if not data_path.is_absolute():
            data_path = _PROJECT_ROOT / data_path
        if not data_path.exists():
            return {"status": "failed", "message": f"数据集路径不存在: {data_path}"}

        # 3. 创建输出目录
        timestamp = str(int(time.time()))
        out_dir = _DOWNLOADS_DIR / timestamp
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as pe:
            return {"status": "failed", "message": f"无权限创建输出目录 {out_dir}: {str(pe)}"}

        # 4. 收集图片文件
        image_extensions = {
            ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff", ".gif",
            ".JPG", ".JPEG", ".PNG", ".BMP", ".WEBP", ".TIFF", ".GIF"
        }
        image_files = []
        for ext in image_extensions:
            image_files.extend(data_path.glob(f"*{ext}"))

        if not image_files:
            return {"status": "failed", "message": f"数据集路径 {data_path} 中没有找到图片文件"}

        # 导入 SDK（可能未安装，提前报错）
        try:
            from volcenginesdkarkruntime import Ark
        except ImportError as e:
            return {"status": "failed", "message": "缺少依赖包 volcengine-python-sdk[ark]，请安装后重试"}

        client = Ark(
            base_url=base_url,
            api_key=api_key,
        )

        # MIME 类型映射
        mime_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".gif": "image/gif",
        }

        edited_images = []
        first_image_path = None

        for img_path in image_files:
            try:
                # 将本地图片转为 base64 data URI，因为 API 需要 URL 或 base64 编码图片
                suffix = img_path.suffix.lower()
                mime = mime_types.get(suffix, "image/png")
                with open(img_path, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode("utf-8")
                image_param = f"data:{mime};base64,{img_data}"

                # 调用豆包图片编辑 API
                response = client.images.generate(
                    model=model,
                    prompt=req,
                    image=image_param,
                    size="2K",
                    response_format="url",
                    watermark=False,
                )

                if not response or not response.data or len(response.data) == 0:
                    raise RuntimeError("API 返回空结果")

                image_url = response.data[0].url
                if not image_url:
                    raise RuntimeError("API 未返回图片 URL")

                # 下载图片
                dl_resp = requests.get(image_url, timeout=30)
                dl_resp.raise_for_status()

                # 保存到输出目录，保持原文件名（冲突则加后缀）
                out_file = out_dir / img_path.name
                counter = 0
                while out_file.exists():
                    counter += 1
                    stem = img_path.stem
                    out_file = out_dir / f"{stem}_{counter}{img_path.suffix}"
                with open(out_file, "wb") as f:
                    f.write(dl_resp.content)
                edited_images.append(out_file)
                if first_image_path is None:
                    first_image_path = out_file
            except Exception as e:
                print(f"处理图片 {img_path} 失败: {str(e)}", file=sys.stderr)
                continue

        if not edited_images:
            return {"status": "failed", "message": "所有图片编辑均失败，请检查 API 调用或网络"}

        # 5. 注册合成数据集
        try:
            file_count = len(edited_images)
            total_size = sum(f.stat().st_size for f in edited_images)
            formats = list({"png"})

            raw_md = f"合成图片数据集，原始数据集：{dataset}，编辑要求：{req}"

            register_result = _call_api(
                "api-data-register",
                id=output_dataset,
                name=output_dataset,
                raw_md=raw_md,
                data_path=str(out_dir.absolute()),
                file_count=file_count,
                total_size=total_size,
                formats=formats
            )
            dataset_id = register_result.get("dataset_id", output_dataset)
        except Exception as e:
            return {"status": "failed", "message": f"注册数据集失败: {str(e)}"}

        return {
            "status": "success",
            "output_format": "image",
            "message": f"图片编辑完成，已生成 {file_count} 张图片并注册数据集为 {dataset_id}",
            "data": {
                "image_path": str(first_image_path) if first_image_path else "",
                "dataset_id": dataset_id,
                "file_count": file_count,
                "output_dir": str(out_dir.absolute())
            }
        }

    except Exception as e:
        return {"status": "failed", "message": str(e)}
