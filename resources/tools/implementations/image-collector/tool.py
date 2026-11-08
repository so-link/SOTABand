
# === SOTABand 工具标准模板 ===
import os, sys, json, time
from pathlib import Path
from typing import Any
import requests

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

import re
from datetime import datetime

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, desc="", **kwargs):
        for item in iterable:
            yield item


def execute(**kwargs) -> dict[str, Any]:
    """图片采集器主函数"""
    try:
        # ── 1. 参数提取与验证 ──
        req = kwargs.get("req", "")
        n = kwargs.get("n", 0)
        dataset = kwargs.get("dataset", "")

        try:
            n = int(n)
        except (ValueError, TypeError):
            return {
                "status": "failed",
                "message": "参数验证错误：n 必须为整数",
                "output_format": "text",
                "data": {}
            }

        if not req or not isinstance(req, str) or req.strip() == "":
            return {
                "status": "failed",
                "message": "参数验证错误：req 不能为空",
                "output_format": "text",
                "data": {}
            }

        if n < 1:
            return {
                "status": "failed",
                "message": "参数验证错误：n 必须为大于 0 的整数",
                "output_format": "text",
                "data": {}
            }

        if not dataset or not isinstance(dataset, str) or dataset.strip() == "":
            return {
                "status": "failed",
                "message": "参数验证错误：dataset 不能为空",
                "output_format": "text",
                "data": {}
            }

        req = req.strip()
        dataset = dataset.strip()

        # ── 2. 创建时间戳目录 ──
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        save_dir = _PROJECT_ROOT / "data" / "download" / timestamp

        try:
            save_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {
                "status": "failed",
                "message": f"目录创建失败：{str(e)}",
                "output_format": "text",
                "data": {}
            }

        # ── 3. 获取图片 URL 列表 ──
        all_image_urls = []
        source_desc = ""  # 描述图片来源

        # 尝试使用 Unsplash API（需 Access Key）
        unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
        if unsplash_key:
            try:
                unsplash_url = (
                    "https://api.unsplash.com/search/photos"
                    f"?query={requests.utils.quote(req)}"
                    "&per_page=30"
                    "&page=1"
                )
                headers = {
                    "Authorization": f"Client-ID {unsplash_key}",
                    "Accept": "application/json",
                }
                resp = requests.get(unsplash_url, headers=headers, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    for img in results:
                        img_url = img.get("urls", {}).get("regular", "")
                        if img_url:
                            all_image_urls.append(img_url)
                        if len(all_image_urls) >= n:
                            break
                    if all_image_urls:
                        source_desc = "Unsplash API"
            except Exception:
                pass  # 忽略 Unsplash API 错误，后续回退

        # 如果没有从 Unsplash API 获取到图片，回退到 LoremFlickr（无需密钥）
        if not all_image_urls:
            # LoremFlickr 每次请求返回一张随机图，循环 n 次
            # 将关键词中的空格替换为逗号，以支持多个词
            keywords = req.replace(" ", ",")
            for idx in range(1, n + 1):
                lorem_url = f"https://loremflickr.com/640/480/{keywords}?random={idx}"
                all_image_urls.append(lorem_url)
            source_desc = "LoremFlickr (免费回退)"

        if not all_image_urls:
            try:
                save_dir.rmdir()
            except Exception:
                pass
            return {
                "status": "failed",
                "message": "未找到任何图片，无图片下载成功",
                "output_format": "text",
                "data": {}
            }

        urls_to_download = all_image_urls[:n]

        # ── 4. 下载图片 ──
        first_image_path = None
        download_count = 0
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

        for idx, img_url in enumerate(tqdm(urls_to_download, desc="下载图片"), start=1):
            try:
                img_resp = requests.get(img_url, headers=headers, timeout=30)
                if img_resp.status_code == 200:
                    # 推断扩展名
                    ext = ".jpg"
                    content_type = img_resp.headers.get("Content-Type", "")
                    if "png" in content_type:
                        ext = ".png"
                    elif "webp" in content_type:
                        ext = ".webp"
                    elif "jpeg" in content_type or "jpg" in content_type:
                        ext = ".jpg"
                    elif "image" not in content_type:
                        # 可能从 URL 推断
                        url_path = img_url.split("?")[0]
                        _, url_ext = os.path.splitext(url_path)
                        if url_ext.lower() in (".png", ".webp", ".jpg", ".jpeg", ".gif"):
                            ext = url_ext.lower()

                    file_path = save_dir / f"{idx}{ext}"
                    with open(file_path, "wb") as f:
                        f.write(img_resp.content)

                    if first_image_path is None:
                        first_image_path = str(file_path.resolve())
                    download_count += 1
            except requests.RequestException:
                continue

        if download_count == 0:
            try:
                for f in save_dir.iterdir():
                    f.unlink()
                save_dir.rmdir()
            except Exception:
                pass
            return {
                "status": "failed",
                "message": "无图片下载成功",
                "output_format": "text",
                "data": {}
            }

        # ── 5. 统计文件信息 ──
        file_count = 0
        total_size = 0
        formats_set = set()

        for f in save_dir.iterdir():
            if f.is_file():
                file_count += 1
                total_size += f.stat().st_size
                ext = f.suffix.lower().lstrip(".")
                if ext:
                    formats_set.add(ext)

        formats = sorted(list(formats_set)) if formats_set else ["jpg"]

        # ── 6. 调用数据集注册 API ──
        dataset_id = f"{dataset}_{timestamp}"

        try:
            register_result = _call_api(
                "api-data-register",
                id=dataset_id,
                name=dataset,
                raw_md=f"图片采集结果 - 关键词: {req}（来源: {source_desc}）",
                data_path=str(save_dir.resolve()),
                file_count=file_count,
                total_size=total_size,
                formats=formats
            )
        except Exception as e:
            return {
                "status": "failed",
                "message": f"数据集注册失败：{str(e)}",
                "output_format": "text",
                "data": {}
            }

        if isinstance(register_result, dict) and register_result.get("status") == "failed":
            return {
                "status": "failed",
                "message": f"数据集注册失败：{register_result.get('message', '未知错误')}",
                "output_format": "text",
                "data": {}
            }

        # ── 7. 返回成功结果 ──
        return {
            "status": "success",
            "message": (
                f"成功从 {source_desc} 下载 {download_count} 张图片（关键词：{req}），"
                f"已注册数据集：{dataset}"
            ),
            "output_format": "image",
            "data": {
                "image_path": first_image_path if first_image_path else ""
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "message": f"工具执行异常：{str(e)}",
            "output_format": "text",
            "data": {}
        }
