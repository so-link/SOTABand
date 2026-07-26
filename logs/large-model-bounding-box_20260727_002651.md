# 自动调试日志

- **工具**: large-model-bounding-box
- **时间**: 20260727_002651
- **结果**: 成功（共 2 轮）
- **日志条目**: 1 轮

---

## 第 1 轮

### 执行结果

```
stdout:
{"status": "failed", "output_format": "text", "message": "调用豆包大模型 API 失败：HTTPSConnectionPool(host='ark.cn-beijing.volces.com', port=443): Read timed out. (read timeout=60)", "data": {}}

stderr:

```

### 发送给 LLM 的 Prompt

```
Debug this tool code. It failed execution.

=== CURRENT CODE ===
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
    path = path.strip()  # 防止前导/后缀空格导致路径判断错误
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(_PROJECT_ROOT / p)

# === 头部结束，以下由 LLM 生成 ===

import base64
import io
import re
import unicodedata
from PIL import Image, ImageDraw


def execute(**kwargs) -> dict[str, Any]:
    """
    执行大模型标框工具：
    1. 获取豆包API KEY
    2. 调用豆包大模型 API 进行目标检测，获取 YOLO 格式的边界框
    3. 在原图上绘制加粗红框并保存
    """
    # 1. 参数校验
    file_path = kwargs.get("file_path", "").strip()
    req = kwargs.get("req", "")
    if not file_path or not req:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "缺少必要参数：file_path 和 req 均为必填项。",
            "data": {}
        }

    # 2. 解析文件路径，确保文件存在（兼容 macOS Unicode 归一化问题）
    original_path_str = _resolve_path(file_path)
    original_path = Path(original_path_str)

    if not original_path.exists():
        # macOS 文件系统使用 NFD 归一化，尝试转换后重试
        nfd_str = unicodedata.normalize('NFD', original_path_str)
        if Path(nfd_str).exists():
            original_path = Path(nfd_str)
        else:
            nfc_str = unicodedata.normalize('NFC', original_path_str)
            if Path(nfc_str).exists():
                original_path = Path(nfc_str)
            else:
                return {
                    "status": "failed",
                    "output_format": "text",
                    "message": f"文件不存在或无法访问：{original_path_str}",
                    "data": {}
                }

    try:
        # 3. 获取豆包 API KEY
        api_key_response = _call_api("api-doubao-get-key")
        if not isinstance(api_key_response, dict):
            return {
                "status": "failed",
                "output_format": "text",
                "message": "获取豆包 API KEY 时返回格式异常。",
                "data": {"raw": str(api_key_response)}
            }
        api_key = api_key_response.get("api_key")
        if not api_key:
            msg = api_key_response.get("message", "未知错误")
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"获取豆包 API KEY 失败：{msg}",
                "data": api_key_response
            }

        # 4. 读取图片并转为 base64
        img = Image.open(original_path).convert("RGB")
        img_width, img_height = img.size
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=95)
        img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        # 5. 构造豆包大模型请求
        url = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        payload = {
            "model": "doubao-seed-2-1-pro-260628",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_base64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": (
                                f"请在图片中检测目标“{req}”，并以 JSON 数组格式返回所有检测到的边界框。"
                                "每个框的格式为 {{\"x\": x_center, \"y\": y_center, \"width\": width, \"height\": height}}，"
                                "所有坐标均为归一化到 0-1 的值。只返回 JSON 数组，不要包含 Markdown 标记或任何附加说明。"
                            )
                        }
                    ]
                }
            ],
            "temperature": 0.1,
            "max_tokens": 2000
        }

        # 6. 调用豆包 API
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            result_json = resp.json()
        except requests.exceptions.RequestException as e:
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"调用豆包大模型 API 失败：{str(e)}",
                "data": {}
            }

        # 7. 解析返回的边界框
        choices = result_json.get("choices", [])
        if not choices:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "豆包大模型返回结果为空，无有效检测结果。",
                "data": {"api_response": result_json}
            }

        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            text_parts = [item.get("text", "") for item in content if item.get("type") == "text"]
            content = "".join(text_parts)

        content = re.sub(r'```json\s*', '', content)
        content = re.sub(r'```\s*', '', content)
        content = content.strip()

        try:
            boxes = json.loads(content)
            if not isinstance(boxes, list) or len(boxes) == 0:
                raise ValueError("检测结果为空")
        except (json.JSONDecodeError, ValueError):
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if match:
                try:
                    boxes = json.loads(match.group())
                except json.JSONDecodeError:
                    return {
                        "status": "failed",
                        "output_format": "text",
                        "message": f"未能从豆包响应中解析出有效的边界框列表。原始内容：{content[:200]}",
                        "data": {"raw_content": content}
                    }
            else:
                return {
                    "status": "failed",
                    "output_format": "text",
                    "message": "豆包大模型未返回任何检测框，可能图片中未找到指定目标或返回格式异常。",
                    "data": {"raw_content": content}
                }

        if not boxes:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "豆包大模型未检测到指定目标。",
                "data": {"raw_content": content}
            }

        # 8. 绘制边界框
        draw = ImageDraw.Draw(img)
        line_width = max(2, min(img_width, img_height) // 150)

        for box in boxes:
            if not all(k in box for k in ("x", "y", "width", "height")):
                continue
            x_center = box["x"] * img_width
            y_center = box["y"] * img_height
            w = box["width"] * img_width
            h = box["height"] * img_height
            left = x_center - w / 2
            top = y_center - h / 2
            right = x_center + w / 2
            bottom = y_center + h / 2
            draw.rectangle([left, top, right, bottom], outline="red", width=line_width)

        # 9. 保存结果图片
        output_dir = original_path.parent
        stem = original_path.stem
        output_path = output_dir / f"{stem}_bbox.jpg"
        img.save(output_path, "JPEG", quality=95)

        return {
            "status": "success",
            "output_format": "image",
            "message": f"成功检测到 {len(boxes)} 个目标，结果已保存至 {output_path}",
            "data": {
                "image_path": str(output_path),
                "num_boxes": len(boxes)
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"工具执行异常：{str(e)}",
            "data": {}
        }
=== END CODE ===

=== TEST INPUT ===
{
  "file_path": "/Users/leo/Documents/1重要备份/project/2026SOTABand/SOTABand/data/downloads/1785080959084/image_8.png",
  "req": "船只"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "output_format": "text", "message": "调用豆包大模型 API 失败：HTTPSConnectionPool(host='ark.cn-beijing.volces.com', port=443): Read timed out. (read timeout=60)", "data": {}}
stderr: 
=== END RESULT ===


Fix the code based on the error and the dependency feedback above.
- If a dependency was installed successfully: keep the import, fix other code logic issues.
- If a dependency failed to install: replace it with an alternative library or stdlib approach.
- If the error is a code logic bug (not dependency-related): fix the bug.

Output the COMPLETE fixed Python file (including template header).
INTERFACE RULES: execute(**kwargs)->dict, kwargs.get, {status,output_format,message,data}, try/except.
Output ONLY Python code. NO pip install, NO subprocess, NO install directives, NO markdown.
```

### LLM 返回

```
```python
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
    path = path.strip()  # 防止前导/后缀空格导致路径判断错误
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(_PROJECT_ROOT / p)

# === 头部结束，以下由 LLM 生成 ===

import base64
import io
import re
import unicodedata
from PIL import Image, ImageDraw


def execute(**kwargs) -> dict[str, Any]:
    """
    执行大模型标框工具：
    1. 获取豆包API KEY
    2. 调用豆包大模型 API 进行目标检测，获取 YOLO 格式的边界框
    3. 在原图上绘制加粗红框并保存
    """
    # 1. 参数校验
    file_path = kwargs.get("file_path", "").strip()
    req = kwargs.get("req", "")
    if not file_path or not req:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "缺少必要参数：file_path 和 req 均为必填项。",
            "data": {}
        }

    # 2. 解析文件路径，确保文件存在（兼容 macOS Unicode 归一化问题）
    original_path_str = _resolve_path(file_path)
    original_path = Path(original_path_str)

    if not original_path.exists():
        # macOS 文件系统使用 NFD 归一化，尝试转换后重试
        nfd_str = unicodedata.normalize('NFD', original_path_str)
        if Path(nfd_str).exists():
            original_path = Path(nfd_str)
        else:
            nfc_str = unicodedata.normalize('NFC', original_path_str)
            if Path(nfc_str).exists():
                original_path = Path(nfc_str)
            else:
                return {
                    "status": "failed",
                    "output_format": "text",
                    "message": f"文件不存在或无法访问：{original_path_str}",
                    "data": {}
                }

    try:
        # 3. 获取豆包 API KEY
        api_key_response = _call_api("api-doubao-get-key")
        if not isinstance(api_key_response, dict):
            return {
                "status": "failed",
                "output_format": "text",
                "message": "获取豆包 API KEY 时返回格式异常。",
                "data": {"raw": str(api_key_response)}
            }
        api_key = api_key_response.get("api_key")
        if not api_key:
            msg = api_key_response.get("message", "未知错误")
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"获取豆包 API KEY 失败：{msg}",
                "data": api_key_response
            }

        # 4. 读取图片并转为 base64
        img = Image.open(original_path).convert("RGB")
        img_width, img_height = img.size
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=95)
        img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        # 5. 构造豆包大模型请求
        url = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        payload = {
            "model": "doubao-seed-2-1-pro-260628",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_base64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": (
                                f"请在图片中检测目标“{req}”，并以 JSON 数组格式返回所有检测到的边界框。"
                                "每个框的格式为 {\"x\": x_center, \"y\": y_center, \"width\": width, \"height\": height}，"
                                "所有坐标均为归一化到 0-1 的值。只返回 JSON 数组，不要包含 Markdown 标记或任何附加说明。"
                            )
                        }
                    ]
                }
            ],
            "temperature": 0.1,
            "max_tokens": 2000
        }

        # 6. 调用豆包 API（增加超时时间并加入重试机制，避免网络偶尔超时）
        max_retries = 2
        last_exception = None
        for attempt in range(max_retries):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=120)
                resp.raise_for_status()
                result_json = resp.json()
                break
            except requests.exceptions.ReadTimeout as e:
                last_exception = e
                if attempt < max_retries - 1:
                    time.sleep(2)  # 稍等后重试
                    continue
                raise
            except requests.exceptions.RequestException as e:
                # 其他请求异常直接抛出
                last_exception = e
                raise
        else:
            # 如果所有重试都失败
            raise last_exception

        # 7. 解析返回的边界框
        choices = result_json.get("choices", [])
        if not choices:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "豆包大模型返回结果为空，无有效检测结果。",
                "data": {"api_response": result_json}
            }

        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            text_parts = [item.get("text", "") for item in content if item.get("type") == "text"]
            content = "".join(text_parts)

        content = re.sub(r'```json\s*', '', content)
        content = re.sub(r'```\s*', '', content)
        content = content.strip()

        try:
            boxes = json.loads(content)
            if not isinstance(boxes, list) or len(boxes) == 0:
                raise ValueError("检测结果为空")
        except (json.JSONDecodeError, ValueError):
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if match:
                try:
                    boxes = json.loads(match.group())
                except json.JSONDecodeError:
                    return {
                        "status": "failed",
                        "output_format": "text",
                        "message": f"未能从豆包响应中解析出有效的边界框列表。原始内容：{content[:200]}",
                        "data": {"raw_content": content}
                    }
            else:
                return {
                    "status": "failed",
                    "output_format": "text",
                    "message": "豆包大模型未返回任何检测框，可能图片中未找到指定目标或返回格式异常。",
                    "data": {"raw_content": content}
                }

        if not boxes:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "豆包大模型未检测到指定目标。",
                "data": {"raw_content": content}
            }

        # 8. 绘制边界框
        draw = ImageDraw.Draw(img)
        line_width = max(2, min(img_width, img_height) // 150)

        for box in boxes:
            if not all(k in box for k in ("x", "y", "width", "height")):
                continue
            x_center = box["x"] * img_width
            y_center = box["y"] * img_height
            w = box["width"] * img_width
            h = box["height"] * img_height
            left = x_center - w / 2
            top = y_center - h / 2
            right = x_center + w / 2
            bottom = y_center + h / 2
            draw.rectangle([left, top, right, bottom], outline="red", width=line_width)

        # 9. 保存结果图片
        output_dir = original_path.parent
        stem = original_path.stem
        output_path = output_dir / f"{stem}_bbox.jpg"
        img.save(output_path, "JPEG", quality=95)

        return {
            "status": "success",
            "output_format": "image",
            "message": f"成功检测到 {len(boxes)} 个目标，结果已保存至 {output_path}",
            "data": {
                "image_path": str(output_path),
                "num_boxes": len(boxes)
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"工具执行异常：{str(e)}",
            "data": {}
        }
```
```
