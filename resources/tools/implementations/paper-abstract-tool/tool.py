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

import logging
import traceback

# 配置简单日志，仅在出现异常时记录但不中断
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def execute(**kwargs) -> dict[str, Any]:
    """
    论文摘要工具主函数
    输入：dataset (str) 数据集名称，n (int) 每篇摘要最大字数
    输出：dict 包含 status, message, output_format, data
    """
    try:
        dataset = kwargs.get("dataset", "")
        n = kwargs.get("n", None)
        if not dataset:
            return {"status": "failed", "message": "参数 'dataset' 是必填项，请提供数据集名称"}
        
        # 修复：允许 n 以字符串形式传入，尝试转换为整数
        if n is not None:
            try:
                n = int(n)
            except (ValueError, TypeError):
                return {"status": "failed", "message": "参数 'n' 必须为整数，表示每篇摘要的最大字数"}
        if n is None or n <= 0:
            return {"status": "failed", "message": "参数 'n' 必须为正整数，表示每篇摘要的最大字数"}
        
        # 1. 获取数据集信息
        ds_response = _call_api("api-data-get", name=dataset)
        logger.info(f"数据集 API 返回: {json.dumps(ds_response, ensure_ascii=False)[:500]}")
        if not ds_response:
            return {"status": "failed", "message": f"数据集 '{dataset}' 信息获取失败，API 返回为空"}
        
        # 尝试多种可能的数据结构提取路径（修复：增加对 data_path 键的支持）
        data_path_str = ""
        if "dataset" in ds_response and isinstance(ds_response["dataset"], dict):
            # 优先尝试 data_path，然后才是 path
            data_path_str = ds_response["dataset"].get("data_path", "") or ds_response["dataset"].get("path", "")
        elif "data" in ds_response and isinstance(ds_response["data"], dict):
            data_path_str = ds_response["data"].get("data_path", "") or ds_response["data"].get("path", "")
        # 如果仍然为空，尝试直接从顶层获取
        if not data_path_str:
            data_path_str = ds_response.get("data_path", "") or ds_response.get("path", "")
        # 再次尝试一些常见别名（同时检查顶层和 dataset 子字典）
        if not data_path_str:
            for key in ("data_path", "file_path", "download_path", "folder"):
                if key in ds_response:
                    data_path_str = ds_response[key]
                    break
                if "dataset" in ds_response and isinstance(ds_response["dataset"], dict) and key in ds_response["dataset"]:
                    data_path_str = ds_response["dataset"][key]
                    break
                if "data" in ds_response and isinstance(ds_response["data"], dict) and key in ds_response["data"]:
                    data_path_str = ds_response["data"][key]
                    break
        # 最后一次尝试：如果 dataset 本身是字符串，检查顶层 path
        if not data_path_str and "dataset" in ds_response and isinstance(ds_response["dataset"], str):
            data_path_str = ds_response.get("data_path", "") or ds_response.get("path", "")
        
        if not data_path_str:
            # 返回详细错误以便调试
            return {
                "status": "failed",
                "message": f"无法从数据集 '{dataset}' 的 API 响应中解析出路径。API 响应内容: {json.dumps(ds_response, ensure_ascii=False)[:300]}"
            }
        
        data_dir = Path(data_path_str)
        if not data_dir.exists():
            return {"status": "failed", "message": f"数据集路径不存在: {data_path_str}，请确认数据集已下载或路径正确"}

        # 2. 获取 DeepSeek API Key
        key_response = _call_api("api-deepseek-get-key")
        logger.info(f"DeepSeek Key API 返回: {json.dumps(key_response, ensure_ascii=False)[:200]}")
        if not key_response or "api_key" not in key_response:
            return {"status": "failed", "message": "无法获取 DeepSeek API KEY，请检查配置，API 返回: " + json.dumps(key_response, ensure_ascii=False)[:200]}
        api_key = key_response["api_key"]
        base_url = key_response.get("base_url", "https://api.deepseek.com/v1")
        model = key_response.get("model", "deepseek-chat")
        
        # 3. 查找所有PDF文件
        pdf_files = list(data_dir.glob("*.pdf"))
        if not pdf_files:
            return {"status": "failed", "message": f"数据集目录下没有 PDF 文件: {data_dir}"}
        
        # 尝试导入所需的库（由系统预装）
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            PdfReader = None
        try:
            import pdfplumber
        except ImportError:
            pdfplumber = None
            
        if PdfReader is None and pdfplumber is None:
            return {"status": "failed", "message": "缺少 PDF 提取库：PyPDF2 或 pdfplumber，请检查环境配置"}
        
        import openai
        
        # 初始化 OpenAI 客户端
        client = openai.OpenAI(api_key=api_key, base_url=base_url)
        
        # 准备 Markdown 内容
        markdown_lines = []
        processed_count = 0
        skipped_files = []
        
        # 4. 遍历处理每个 PDF
        for pdf_path in pdf_files:
            try:
                # 提取文本
                full_text = ""
                if PdfReader:
                    try:
                        reader = PdfReader(str(pdf_path))
                        for page in reader.pages:
                            page_text = page.extract_text()
                            if page_text:
                                full_text += page_text + "\n"
                    except Exception as e:
                        logger.warning(f"PyPDF2 提取失败 ({pdf_path.name}): {e}")
                if not full_text and pdfplumber:
                    try:
                        with pdfplumber.open(str(pdf_path)) as pdf:
                            for page in pdf.pages:
                                page_text = page.extract_text()
                                if page_text:
                                    full_text += page_text + "\n"
                    except Exception as e:
                        logger.warning(f"pdfplumber 提取失败 ({pdf_path.name}): {e}")
                
                if not full_text.strip():
                    logger.warning(f"无法从 {pdf_path.name} 提取任何文本，跳过该文件")
                    skipped_files.append(pdf_path.name)
                    continue
                
                # 限制输入长度，避免超出模型上下文
                max_input_chars = 15000  # 可根据需要调整
                if len(full_text) > max_input_chars:
                    full_text = full_text[:max_input_chars] + "\n...(内容过长已截断)"
                
                # 构造 Prompt
                prompt = (
                    f"你是一个学术论文分析助手。请根据以下论文全文，生成一篇不超过{n}字的中文摘要，"
                    f"并识别论文的标题。\n\n"
                    f"请严格以 JSON 格式返回，包含 \"title\" 和 \"abstract\" 两个字段。"
                    f"不要输出任何其他内容。\n\n论文全文：\n{full_text}"
                )
                
                # 调用大模型
                try:
                    completion = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.3,
                        max_tokens=2000,
                    )
                    response_text = completion.choices[0].message.content.strip()
                except Exception as e:
                    logger.error(f"调用 DeepSeek 模型失败 ({pdf_path.name}): {e}")
                    skipped_files.append(pdf_path.name)
                    continue
                
                # 解析返回的 JSON
                title = None
                abstract = None
                # 尝试去除可能的 markdown 代码块标记
                if response_text.startswith("```json"):
                    response_text = response_text[7:]
                    if response_text.endswith("```"):
                        response_text = response_text[:-3]
                elif response_text.startswith("```"):
                    response_text = response_text[3:]
                    if response_text.endswith("```"):
                        response_text = response_text[:-3]
                try:
                    result_json = json.loads(response_text)
                    title = result_json.get("title", "").strip()
                    abstract = result_json.get("abstract", "").strip()
                except json.JSONDecodeError:
                    logger.warning(f"模型返回无法解析为 JSON，尝试原始内容 ({pdf_path.name})")
                    # 降级处理：使用文件名作为标题，整个响应作为摘要
                    title = pdf_path.stem
                    abstract = response_text
                
                if not title:
                    title = pdf_path.stem  # 若未识别出标题，用文件名代替
                if not abstract:
                    abstract = "（摘要生成失败，模型未返回有效内容）"
                
                # 拼接到 Markdown
                markdown_lines.append(f"## {title}\n\n{abstract}\n")
                processed_count += 1
                
            except Exception as e:
                logger.error(f"处理文件时出现意外错误 ({pdf_path.name}): {e}")
                skipped_files.append(pdf_path.name)
                continue
        
        # 5. 输出文件
        output_path = data_dir / "abstracts.md"
        if processed_count == 0:
            return {"status": "failed", "message": f"未能成功处理任何 PDF 文件，跳过列表：{skipped_files}"}
        
        # 写入文件
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("# 论文摘要\n\n")
                f.write(f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"数据集：{dataset}\n")
                f.write(f"摘要字数限制：不超过{n}字\n\n")
                f.write("\n".join(markdown_lines))
            logger.info(f"摘要文件已保存至: {output_path}")
        except Exception as e:
            return {"status": "failed", "message": f"写入文件失败: {e}"}
        
        # 6. 返回成功结果
        message = f"处理完成：共 {processed_count} 篇论文"
        if skipped_files:
            message += f"，跳过 {len(skipped_files)} 篇（{', '.join(skipped_files)}）"
        message += f"，摘要已保存至 {output_path}"
        return {
            "status": "success",
            "output_format": "file",
            "message": message,
            "data": {"file_path": str(output_path)}
        }
        
    except Exception as e:
        logger.error(f"工具执行异常: {traceback.format_exc()}")
        return {"status": "failed", "message": f"工具执行出错: {str(e)}"}