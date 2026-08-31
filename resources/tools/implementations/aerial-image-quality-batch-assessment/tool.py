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

# ── LLM 调用辅助（统一走系统配置的 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL） ──
def _llm_chat(messages: list, **kwargs) -> str:
    """同步调用系统统一大模型客户端，返回完整文本。

    跟随全局配置（config/settings.py 的 PROVIDER_PRESETS）自动选择服务商：
    DeepSeek / OpenAI / Kimi / 智谱 / 通义 / 硅基流动 / MiniMax / MiMo / 豆包 等。
    禁止在本工具内直连任何具体服务商端点或硬编码模型名。
    """
    import asyncio
    from core.llm.client import create_llm_client
    client = create_llm_client()
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(client.chat(messages, **kwargs))
        loop.run_until_complete(client.aclose())
        return result
    finally:
        loop.close()

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

import base64
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from PIL import Image


def execute(**kwargs) -> dict[str, Any]:
    """航拍图片批量质量评估工具入口函数"""
    try:
        # 1. 获取参数
        dataset_name = kwargs.get("dataset_name", "")
        if not dataset_name:
            return {
                "status": "failed",
                "output_format": "table",
                "message": "缺少必需参数 dataset_name",
                "data": {}
            }
        
        # 并发数处理
        concurrency = kwargs.get("concurrency", 8)
        try:
            concurrency = int(concurrency)
            if concurrency < 1:
                concurrency = 8
        except (TypeError, ValueError):
            concurrency = 8
        
        # 2. 调用 API 获取数据集信息
        try:
            api_result = _call_api("api-data-get", name=dataset_name)
            dataset_info = api_result.get("dataset", {})
            if not dataset_info:
                return {
                    "status": "failed",
                    "output_format": "table",
                    "message": f"无法获取数据集 '{dataset_name}' 的信息",
                    "data": {}
                }
            
            # 获取图片目录
            data_path = dataset_info.get("data_path", "")
            if not data_path:
                return {
                    "status": "failed",
                    "output_format": "table",
                    "message": f"数据集 '{dataset_name}' 缺少 data_path 字段",
                    "data": {}
                }
        except Exception as e:
            return {
                "status": "failed",
                "output_format": "table",
                "message": f"调用【获取数据集信息】失败: {str(e)}\n\nTraceback:\n{traceback.format_exc()}",
                "data": {}
            }
        
        # 3. 列出目录下所有 .jpg 文件
        try:
            image_dir = Path(_resolve_path(data_path))
            if not image_dir.exists():
                return {
                    "status": "failed",
                    "output_format": "table",
                    "message": f"数据集目录不存在: {image_dir}",
                    "data": {}
                }
            
            # 按文件名排序获取所有 jpg 文件
            jpg_files = sorted([
                f for f in image_dir.iterdir()
                if f.is_file() and f.suffix.lower() in ['.jpg', '.jpeg']
            ], key=lambda x: x.name)
            
            if not jpg_files:
                return {
                    "status": "failed",
                    "output_format": "table",
                    "message": f"数据集目录 {image_dir} 下没有找到 jpg/jpeg 文件",
                    "data": {}
                }
        except Exception as e:
            return {
                "status": "failed",
                "output_format": "table",
                "message": f"访问数据集目录失败: {str(e)}\n\nTraceback:\n{traceback.format_exc()}",
                "data": {}
            }
        
        total_images = len(jpg_files)
        
        # 4. 定义单张图片处理函数
        def process_single_image(image_path: Path) -> dict:
            """处理单张图片，返回评分结果字典"""
            result = {
                "filename": image_path.name,
                "score": 0.0,
                "clarity": 0,
                "light": 0,
                "occlusion": 0,
                "completeness": 0,
                "label": "",
                "issue": "",
                "time_text": None,
                "error": None
            }
            
            try:
                # 4a. 读取并预处理图片
                try:
                    img = Image.open(image_path)
                    # 等比缩放到最长边不超过1024像素
                    max_side = max(img.size)
                    if max_side > 1024:
                        ratio = 1024 / max_side
                        new_size = (int(img.width * ratio), int(img.height * ratio))
                        img = img.resize(new_size, Image.LANCZOS)
                    
                    # 转换为RGB模式（去除alpha通道）
                    if img.mode in ['RGBA', 'P', 'LA']:
                        img = img.convert('RGB')
                    
                    # 保存为JPEG到内存缓冲区
                    buffer = BytesIO()
                    img.save(buffer, format='JPEG', quality=85)
                    buffer.seek(0)
                    
                    # base64编码
                    base64_string = base64.b64encode(buffer.read()).decode('utf-8')
                except Exception as e:
                    result["error"] = f"图片预处理失败: {str(e)}"
                    return result
                
                # 4b. 构造 LLM 提示词
                prompt = """你是航拍图像质量评估专家。请为这张航拍图片打分。

【评分规则】四个维度均为 0-100 分，分数越高代表质量越好：

1. clarity（画面清晰度）：
   - 90-100 细节锐利，边缘清晰可辨
   - 70-89  整体清晰，个别区域略软
   - 40-69  明显模糊，细节有损失
   - 0-39   严重模糊，内容难以辨认

2. light（光照质量）：曝光是否合适
   - 90-100 曝光准确，明暗细节完整
   - 70-89  基本正常，轻微偏亮或偏暗
   - 40-69  明显过曝（发白、高光溢出）或明显欠曝（发黑、暗部无细节）
   - 0-39   严重过曝或欠曝，画面信息大量丢失

3. occlusion（无遮挡程度）：目标被遮挡的反面，分越高代表遮挡越少
   - 90-100 目标完全无遮挡
   - 70-89  轻微遮挡，主体仍完整可见
   - 40-69  中等遮挡，主体部分被挡住
   - 0-39   严重遮挡，主体大部分不可见

4. completeness（目标完整性）：目标是否完整位于画面内
   - 90-100 目标完整在画面内
   - 70-89  目标基本完整，边缘有轻微裁切
   - 40-69  目标被画面边缘明显裁切
   - 0-39   目标大部分在画面外

【标签规则】label 必须严格按以下规则从你打出的分数推导，不要独立判断：
先取四个分数中的最低值 min_score 及其对应的维度：
- 若 min_score >= 70，label = "清晰可用"
- 若最低分维度是 clarity，label = "模糊"
- 若最低分维度是 light，过曝填 "过曝"，欠曝填 "光线不足"
- 若最低分维度是 occlusion，label = "目标遮挡"
- 若最低分维度是 completeness，label = "目标不完整"

【时间水印】若画面上存在可见的时间水印文字，提取为 time_text 字段（没有则填 null）。

只输出JSON，不要任何解释，不要markdown代码块：
{"clarity":0-100,"light":0-100,"occlusion":0-100,"completeness":0-100,"time_text":null,"label":"清晰可用|模糊|光线不足|过曝|目标遮挡|目标不完整","issue":"一句话问题描述"}"""
                
                # 4c. 调用 LLM
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_string}"
                                }
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ]
                    }
                ]
                
                # 关键约束：max_tokens=4000，temperature=0.1
                response = _llm_chat(messages, max_tokens=4000, temperature=0.1)
                
                if not response or not isinstance(response, str) or not response.strip():
                    result["error"] = "LLM 返回空响应"
                    return result
                
                # 4d. 解析 LLM 返回
                try:
                    # 去除可能的 markdown 代码块标记
                    cleaned = response.strip()
                    if cleaned.startswith("```"):
                        # 移除首行的 ```json 或 ```
                        lines = cleaned.split("\n")
                        if len(lines) > 1 and lines[0].strip().startswith("```"):
                            lines = lines[1:]
                        # 移除末尾的 ```
                        if lines and lines[-1].strip() == "```":
                            lines = lines[:-1]
                        cleaned = "\n".join(lines)
                    
                    parsed = json.loads(cleaned)
                    
                    # 提取并验证各个分数字段
                    fields = ["clarity", "light", "occlusion", "completeness"]
                    for field in fields:
                        try:
                            value = parsed.get(field)
                            if value is None:
                                raise ValueError(f"缺少字段 {field}")
                            # 尝试转换为数值
                            value = float(value)
                            if not (0 <= value <= 100):
                                raise ValueError(f"{field} 值超出范围: {value}")
                            result[field] = int(round(value))  # 保存为整数
                        except (TypeError, ValueError, KeyError) as e:
                            result["error"] = f"字段 {field} 解析失败: {str(e)}"
                            return result
                    
                    # 提取其他字段
                    result["label"] = str(parsed.get("label", "未知"))
                    result["issue"] = str(parsed.get("issue", ""))
                    result["time_text"] = parsed.get("time_text")
                    
                    # 计算综合分
                    score = (
                        result["clarity"] * 0.3 +
                        result["light"] * 0.25 +
                        result["occlusion"] * 0.25 +
                        result["completeness"] * 0.2
                    )
                    result["score"] = round(score, 1)  # 保留一位小数
                    
                except json.JSONDecodeError as e:
                    result["error"] = f"JSON 解析失败: {str(e)}\n原始响应前200字符: {response[:200]}"
                except Exception as e:
                    result["error"] = f"处理 LLM 响应时发生错误: {str(e)}"
                
                return result
                
            except Exception as e:
                result["error"] = f"处理图片时发生未预期错误: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
                return result
        
        # 5. 使用线程池并发处理所有图片
        results = []
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            # 提交所有任务
            future_to_image = {
                executor.submit(process_single_image, img): img
                for img in jpg_files
            }
            
            # 收集结果
            for future in as_completed(future_to_image):
                try:
                    result = future.result(timeout=60)  # 每个图片最多处理60秒
                    results.append(result)
                except Exception as e:
                    img_path = future_to_image[future]
                    results.append({
                        "filename": img_path.name,
                        "score": 0.0,
                        "clarity": 0,
                        "light": 0,
                        "occlusion": 0,
                        "completeness": 0,
                        "label": "",
                        "issue": "",
                        "time_text": None,
                        "error": f"任务执行失败: {str(e)}"
                    })
        
        elapsed_sec = time.time() - start_time
        
        # 6. 统计汇总
        total = len(results)
        success_count = sum(1 for r in results if not r.get("error"))
        failed_count = total - success_count
        
        if failed_count == total:
            return {
                "status": "failed",
                "output_format": "table",
                "message": f"所有图片处理失败 (共 {total} 张)",
                "data": {}
            }
        
        # 6.1 收集成功的分数（类型安全）
        valid_scores = []
        dimension_scores = {"clarity": [], "light": [], "occlusion": [], "completeness": []}
        label_counts = {}
        grade_counts = {"优质": 0, "可用": 0, "低质": 0}
        
        for r in results:
            if r.get("error"):
                continue
            
            # 验证分数字段是数值类型
            try:
                score = float(r.get("score", 0))
                clarity = float(r.get("clarity", 0))
                light = float(r.get("light", 0))
                occlusion = float(r.get("occlusion", 0))
                completeness = float(r.get("completeness", 0))
                
                # 过滤 NaN/Inf
                if (score == score and abs(score) != float('inf') and
                    clarity == clarity and abs(clarity) != float('inf') and
                    light == light and abs(light) != float('inf') and
                    occlusion == occlusion and abs(occlusion) != float('inf') and
                    completeness == completeness and abs(completeness) != float('inf')):
                    
                    valid_scores.append(score)
                    dimension_scores["clarity"].append(clarity)
                    dimension_scores["light"].append(light)
                    dimension_scores["occlusion"].append(occlusion)
                    dimension_scores["completeness"].append(completeness)
                    
                    # 标签统计
                    label = str(r.get("label", ""))
                    label_counts[label] = label_counts.get(label, 0) + 1
                    
                    # 等级统计
                    if score >= 80:
                        grade_counts["优质"] += 1
                    elif score >= 60:
                        grade_counts["可用"] += 1
                    else:
                        grade_counts["低质"] += 1
                        
            except (TypeError, ValueError):
                # 如果转换失败，跳过该条目进行统计
                continue
        
        # 6.2 计算维度统计
        dimension_stats = {}
        for dim, scores in dimension_scores.items():
            if scores:
                dimension_stats[dim] = {
                    "avg": round(sum(scores) / len(scores), 1),
                    "min": min(scores),
                    "max": max(scores)
                }
            else:
                dimension_stats[dim] = {"avg": 0, "min": 0, "max": 0}
        
        avg_score = round(sum(valid_scores) / len(valid_scores), 1) if valid_scores else 0
        
        # 7. 生成报告 JSON
        report = {
            "dataset_name": dataset_name,
            "total": total,
            "success": success_count,
            "failed": failed_count,
            "elapsed_sec": round(elapsed_sec, 2),
            "concurrency": concurrency,
            "summary": {
                "avg_score": avg_score,
                "dimensions": dimension_stats,
                "label_stats": label_counts,
                "grade_stats": grade_counts
            },
            "images": results
        }
        
        # 8. 写入报告文件
        try:
            timestamp = int(time.time() * 1000)
            report_dir = _DOWNLOADS_DIR / str(timestamp)
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / "quality_report.json"
            
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            
            report_path_str = str(report_path)
        except Exception as e:
            return {
                "status": "failed",
                "output_format": "table",
                "message": f"生成报告文件失败: {str(e)}\n\nTraceback:\n{traceback.format_exc()}",
                "data": {}
            }
        
        # 9. 准备表格数据（按分数升序取前20条）
        # 过滤出有效的结果并排序
        valid_results = [r for r in results if not r.get("error")]
        valid_results_sorted = sorted(valid_results, key=lambda x: float(x.get("score", 0)))
        
        # 取前20条
        display_rows = valid_results_sorted[:20]
        
        # 转换为列表格式
        table_rows = []
        for r in display_rows:
            row = [
                r.get("filename", ""),
                r.get("score", 0),
                r.get("clarity", 0),
                r.get("light", 0),
                r.get("occlusion", 0),
                r.get("completeness", 0),
                r.get("label", ""),
                r.get("issue", "")
            ]
            table_rows.append(row)
        
        # 10. 返回成功结果
        return {
            "status": "success",
            "output_format": "table",
            "message": f"数据集 '{dataset_name}' 质量评估完成，共处理 {total} 张图片，成功 {success_count} 张，失败 {failed_count} 张",
            "data": {
                "columns": ["文件名", "综合分", "清晰度", "光照", "遮挡", "完整性", "质量标签", "问题描述"],
                "rows": table_rows,
                "report_path": report_path_str
            }
        }
        
    except Exception as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"工具执行失败: {str(e)}\n\nTraceback:\n{traceback.format_exc()}",
            "data": {}
        }