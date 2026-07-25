
# === SOTABand 工具标准模板 ===
import os, sys, json, time, tempfile
from pathlib import Path
from typing import Any
import requests
from volcenginesdkarkruntime import Ark

# ── 项目根路径 ──
_tool_dir = os.environ.get("TOOL_DIR", "")
if _tool_dir:
    _PROJECT_ROOT = Path(_tool_dir).resolve().parent.parent.parent.parent
else:
    try:
        _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
    except NameError:
        # 当通过 exec / -c 运行时 __file__ 未定义，回退到当前工作目录
        _PROJECT_ROOT = Path.cwd()
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
    """
    根据文本描述生成图片，注册为数据集。
    参数:
        req (str): 提示词
        n (int): 生成数量
        dataset (str): 数据集名称
    返回:
        dict: 标准工具输出格式
    """
    try:
        # 1. 获取输入参数
        req = kwargs.get("req")
        n = kwargs.get("n")
        dataset = kwargs.get("dataset")

        if not req or not n or not dataset:
            return {
                "status": "failed",
                "message": "缺少必填参数: req, n, dataset"
            }

        try:
            n = int(n)
        except (TypeError, ValueError):
            return {"status": "failed", "message": "参数 n 必须为整数"}

        if n <= 0:
            return {"status": "failed", "message": "生成数量 n 必须大于 0"}

        # 2. 获取豆包 API KEY
        key_resp = _call_api("api-doubao-get-key")
        if key_resp.get("status") == "failed" or not key_resp.get("api_key"):
            return {
                "status": "failed",
                "message": f"获取豆包API KEY失败: {key_resp.get('message', '未知错误')}"
            }
        api_key = key_resp["api_key"]

        # 3. 创建时间戳子目录（处理无权限时回退到临时目录）
        timestamp = str(int(time.time() * 1000))
        try:
            save_dir = _DOWNLOADS_DIR / timestamp
            save_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            # 回退到系统临时目录
            save_dir = Path(tempfile.mkdtemp(prefix="image_gen_"))

        # 4. 初始化豆包客户端
        client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=api_key,
        )

        # 5. 循环生成图片并下载
        first_image_path = None
        success_count = 0
        fail_messages = []

        for i in range(1, n + 1):
            try:
                imagesResponse = client.images.generate(
                    model="doubao-seedream-5-0-lite-260128",
                    prompt=req,
                    size="2K",
                    response_format="url",
                    watermark=False
                )
                # 获取图片 URL
                if hasattr(imagesResponse, 'data') and len(imagesResponse.data) > 0:
                    image_url = imagesResponse.data[0].url
                else:
                    raise RuntimeError("生成响应中未包含图片URL")

                # 下载图片
                img_resp = requests.get(image_url, timeout=30)
                img_resp.raise_for_status()

                # 保存到本地，如 image_1.png
                file_name = f"image_{i}.png"
                file_path = save_dir / file_name
                with open(file_path, "wb") as f:
                    f.write(img_resp.content)

                success_count += 1
                if first_image_path is None:
                    first_image_path = str(file_path)

            except Exception as e:
                fail_messages.append(f"第{i}张图片失败: {str(e)}")

        if success_count == 0:
            return {
                "status": "failed",
                "message": "所有图片生成或下载均失败: " + "; ".join(fail_messages)
            }

        # 6. 注册数据集
        # 计算总大小与格式
        total_size = sum(f.stat().st_size for f in save_dir.glob("*") if f.is_file())
        formats = ["png"]  # 所有输出固定为 png

        register_resp = _call_api(
            "api-data-register",
            id=dataset,
            name=dataset,
            raw_md=f"由豆包大模型合成的图片数据集，提示词: {req[:100]}",
            data_path=str(save_dir),
            file_count=success_count,
            total_size=total_size,
            formats=formats
        )

        if register_resp.get("status") == "failed":
            return {
                "status": "failed",
                "message": f"数据集注册失败: {register_resp.get('message', '未知错误')}，图片已保存至 {save_dir}"
            }

        # 7. 返回成功结果，包含第一张图片路径
        return {
            "status": "success",
            "output_format": "image",
            "message": f"合成图片数据集注册成功，共生成{success_count}张图片（失败{len(fail_messages)}张）",
            "data": {
                "image_path": first_image_path
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "message": f"执行过程中发生未预期错误: {str(e)}"
        }
