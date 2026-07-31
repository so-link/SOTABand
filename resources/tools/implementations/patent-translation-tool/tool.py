# === SOTABand 工具标准模板 ===
import os, sys, json, time
from pathlib import Path
from typing import Any
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
    """调用已注册的工具（通过 registry.json 查找工具 ID 对应的实现目录）"""
    import subprocess as _sp
    # 从 registry.json 中查找工具 ID（目录名）
    reg_path = _PROJECT_ROOT / "resources" / "tools" / "registry.json"
    tool_id = tool_name  # 默认用名称作为 ID
    if reg_path.exists():
        try:
            tools = json.loads(reg_path.read_text(encoding="utf-8"))
            # 先精确匹配 id，再模糊匹配 name
            for t in tools:
                if t.get("id") == tool_name or t.get("name") == tool_name:
                    tool_id = t["id"]
                    break
        except Exception:
            pass
    tool_dir = _PROJECT_ROOT / "resources" / "tools" / "implementations" / tool_id
    tool_file = tool_dir / "tool.py"
    if not tool_file.exists():
        return {"status": "failed", "message": f"Tool '{tool_name}' (id={tool_id}) not found"}
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

import openai
import logging

def execute(**kwargs) -> dict[str, Any]:
    # 1. 参数提取与校验
    req = kwargs.get("req")
    n = kwargs.get("n")
    year = kwargs.get("year")
    dataset = kwargs.get("dataset")

    # 基本类型与有效性检查
    if not isinstance(req, str) or not req.strip():
        return {"status": "failed", "message": "需求描述(req)必须是非空字符串", "output_format": "text", "data": {}}
    
    # 尝试将 n 转换为整数（允许传入字符串形式的数字）
    try:
        n = int(n)
    except (TypeError, ValueError):
        return {"status": "failed", "message": "专利数量(n)必须为正整数", "output_format": "text", "data": {}}
    if n <= 0:
        return {"status": "failed", "message": "专利数量(n)必须为正整数", "output_format": "text", "data": {}}
    
    # 尝试将 year 转换为整数
    try:
        year = int(year)
    except (TypeError, ValueError):
        return {"status": "failed", "message": "发表年限(year)必须为四位数的年份", "output_format": "text", "data": {}}
    if year < 1000 or year > 9999:
        return {"status": "failed", "message": "发表年限(year)必须为四位数的年份", "output_format": "text", "data": {}}
    
    if not isinstance(dataset, str) or not dataset.strip():
        return {"status": "failed", "message": "数据集名称(dataset)必须是非空字符串", "output_format": "text", "data": {}}

    # 2. 调用 Lens专利检索与注册工具
    try:
        tool_res = _call_tool("Lens专利检索与注册工具", req=req, n=n, year=year, dataset=dataset)
    except Exception as e:
        return {"status": "failed", "message": f"调用专利检索与注册工具失败: {str(e)}", "output_format": "text", "data": {}}
    if not isinstance(tool_res, dict) or tool_res.get("status") != "success":
        return {"status": "failed", "message": f"专利检索与注册工具异常: {tool_res.get('message', '未知错误')}", "output_format": "text", "data": {}}

    # 3. 获取 DeepSeek API 密钥
    try:
        key_res = _call_api("api-deepseek-get-key")
    except Exception as e:
        return {"status": "failed", "message": f"获取 DeepSeek API KEY 失败: {str(e)}", "output_format": "text", "data": {}}
    api_key = key_res.get("api_key")
    base_url = key_res.get("base_url")
    model = key_res.get("model")
    if not api_key or not base_url or not model:
        return {"status": "failed", "message": "无法获取完整的 DeepSeek API 信息 (api_key/base_url/model)", "output_format": "text", "data": {}}

    # 4. 获取数据集本地路径
    data_path = None

    # 优先从 Lens 工具返回结果中提取路径
    tool_data = tool_res.get("data", {}) if isinstance(tool_res, dict) else {}
    if isinstance(tool_data, dict):
        def extract_path(obj, depth=0):
            """递归查找路径，最大深度5"""
            if depth > 5:
                return None
            if isinstance(obj, dict):
                for key in ('path','directory','local_path','dir','location','file_path','folder','data_path','download_path','root_path'):
                    if key in obj and isinstance(obj[key], str) and obj[key].strip():
                        return obj[key]
                for v in obj.values():
                    res = extract_path(v, depth+1)
                    if res:
                        return res
            elif isinstance(obj, list):
                for item in obj:
                    res = extract_path(item, depth+1)
                    if res:
                        return res
            return None
        data_path = extract_path(tool_data)

    # 若 Lens 工具未提供路径，尝试从系统 API 获取
    if not data_path:
        try:
            data_info = _call_api("api-data-get", name=dataset)
        except Exception as e:
            return {"status": "failed", "message": f"获取数据集信息失败: {str(e)}", "output_format": "text", "data": {}}

        if data_info is None:
            return {"status": "failed", "message": "获取数据集信息失败：API 返回为空", "output_format": "text", "data": {}}
        if isinstance(data_info, dict) and data_info.get("status") == "failed":
            return {"status": "failed", "message": f"获取数据集信息失败: {data_info.get('message', '未知错误')}", "output_format": "text", "data": {}}

        # 增强路径提取（复用上面的递归函数）
        if isinstance(data_info, str):
            data_path = data_info
        elif isinstance(data_info, dict):
            data_path = extract_path(data_info)

    # 5. 备选路径：常见的数据集存放位置
    if not data_path:
        candidate_dirs = [
            _DOWNLOADS_DIR / dataset,
            _DATA_DIR / dataset,
            _PROJECT_ROOT / "data" / dataset,
        ]
        for cand in candidate_dirs:
            if cand.exists() and cand.is_dir():
                data_path = str(cand)
                break

    # 6. 最终路径验证
    if not data_path:
        searched = "、".join([str(d) for d in candidate_dirs]) if candidate_dirs else "无"
        return {
            "status": "failed",
            "message": f"数据集 '{dataset}' 未返回有效路径，且自动查找位置均不存在（尝试过：{searched}）",
            "output_format": "text",
            "data": {}
        }

    data_path = Path(data_path)
    if not data_path.exists() or not data_path.is_dir():
        return {"status": "failed", "message": f"数据集路径无效或不存在: {data_path}", "output_format": "text", "data": {}}

    # 7. 初始化 DeepSeek 客户端
    try:
        client = openai.OpenAI(api_key=api_key, base_url=base_url)
    except Exception as e:
        return {"status": "failed", "message": f"初始化 OpenAI 客户端失败: {str(e)}", "output_format": "text", "data": {}}

    # 8. 遍历并翻译所有 .md 文件
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    md_files = list(data_path.rglob("*.md"))
    if not md_files:
        return {"status": "success", "message": "没有找到 Markdown 文件", "output_format": "text",
                "data": {"data_path": str(data_path), "text": f"翻译完成，未发现 Markdown 文件，路径: {data_path}"}}

    failed_files = []
    total = len(md_files)
    for idx, md_file in enumerate(md_files, 1):
        try:
            original_content = md_file.read_text(encoding="utf-8")
            if not original_content.strip():
                logger.info(f"跳过空文件: {md_file}")
                continue

            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是一个专业的专利文档翻译助手。请将以下 Markdown 内容完整翻译为中文，严格保留所有 Markdown 格式、代码块、标签和链接，不要修改任何非文本内容。只返回翻译后的内容，不要添加任何额外解释。"},
                    {"role": "user", "content": original_content}
                ],
                temperature=0.1,
                max_tokens=40960
            )
            translated = completion.choices[0].message.content
            md_file.write_text(translated, encoding="utf-8")
            logger.info(f"翻译完成 ({idx}/{total}): {md_file.name}")

        except Exception as e:
            failed_files.append(f"{md_file}: {str(e)}")
            logger.error(f"翻译失败: {md_file} - {str(e)}")

    # 9. 返回结果
    if failed_files:
        fail_list = "; ".join(failed_files)
        return {
            "status": "failed",
            "message": f"部分文件翻译失败: {fail_list}",
            "output_format": "text",
            "data": {
                "data_path": str(data_path),
                "text": f"翻译部分失败，路径: {data_path}，失败文件: {fail_list}"
            }
        }
    else:
        return {
            "status": "success",
            "message": "翻译完成",
            "output_format": "text",
            "data": {
                "data_path": str(data_path),
                "text": f"翻译成功，文件路径: {data_path}"
            }
        }