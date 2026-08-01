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

def execute(**kwargs) -> dict[str, Any]:
    """
    根据城市名称和天数获取天气预报，以表格形式返回。
    
    参数:
        city (str): 城市名称（中文或英文）
        days (int): 未来查询天数，默认 3，范围 1~16
    
    返回:
        dict: 包含 status, message, output_format, data 字段
    """
    city = kwargs.get("city", "")
    days = kwargs.get("days", 3)

    # ── 参数校验 ──
    if not city:
        return {
            "status": "failed",
            "message": "城市名不能为空",
            "output_format": "text",
            "data": {"text": "city 参数为必填项"}
        }

    # 类型转换并校验 days
    try:
        days = int(days)
    except (ValueError, TypeError):
        return {
            "status": "failed",
            "message": "days 参数必须为整数",
            "output_format": "text",
            "data": {"text": "days 参数类型错误"}
        }

    if days < 1 or days > 16:
        return {
            "status": "failed",
            "message": f"查询天数超出范围，有效值为 1~16，当前传入 {days}",
            "output_format": "text",
            "data": {"text": f"days={days} 超出范围"}
        }

    # ── 主逻辑 ──
    try:
        # 1. 地理编码：城市名 → 经纬度（使用 Open-Meteo Geocoding API，免费无需 Key）
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=zh&format=json"
        geo_resp = requests.get(geo_url, timeout=10)
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()

        if not geo_data.get("results"):
            # 如果首次查询无结果，可尝试再次用英文名，这里直接返回失败
            return {
                "status": "failed",
                "message": f"未查询到城市 '{city}' 的地理信息，请确认城市名是否正确",
                "output_format": "text",
                "data": {"text": "城市不存在"}
            }

        result = geo_data["results"][0]
        lat = result["latitude"]
        lon = result["longitude"]
        display_name = result.get("name", city)
        country = result.get("country", "")
        if country:
            display_name = f"{display_name}, {country}"

        # 2. 获取天气预报（Open-Meteo Forecast API）
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max",
            "forecast_days": days,
            "timezone": "auto"
        }
        forecast_url = "https://api.open-meteo.com/v1/forecast"
        forecast_resp = requests.get(forecast_url, params=params, timeout=10)
        forecast_resp.raise_for_status()
        forecast_data = forecast_resp.json()

        daily = forecast_data.get("daily", {})
        if not daily or "time" not in daily or len(daily["time"]) == 0:
            return {
                "status": "failed",
                "message": "获取天气预报数据失败，API 返回数据为空",
                "output_format": "text",
                "data": {"text": "无数据"}
            }

        # 3. 整理表格数据
        # WMO 天气代码简单中文映射（部分）
        wmo_map = {
            0: "晴天", 1: "大部晴朗", 2: "多云", 3: "阴天",
            45: "雾", 48: "雾凇",
            51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
            56: "冻毛毛雨", 57: "冻毛毛雨",
            61: "小雨", 63: "中雨", 65: "大雨",
            66: "冻雨", 67: "冻雨",
            71: "小雪", 73: "中雪", 75: "大雪",
            77: "雪粒",
            80: "阵雨", 81: "中等阵雨", 82: "强阵雨",
            85: "小阵雪", 86: "强阵雪",
            95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹"
        }

        columns = ["日期", "天气", "最高温(℃)", "最低温(℃)", "降雨量(mm)", "风速(km/h)", "预警"]
        rows = []

        for i in range(len(daily["time"])):
            date = daily["time"][i]
            code = daily["weathercode"][i]
            weather_desc = wmo_map.get(code, f"未知({code})")
            max_temp = daily["temperature_2m_max"][i]
            min_temp = daily["temperature_2m_min"][i]
            precip = daily["precipitation_sum"][i]
            wind = daily["windspeed_10m_max"][i]

            # 简单预警判断：根据天气代码标记严重天气
            warning = "无"
            if code in (95, 96, 99):
                warning = "强雷暴"
            elif code in (66, 67):
                warning = "冻雨预警"
            elif code in (75, 86):
                warning = "大雪/强雪"
            elif code >= 80:
                warning = "阵雨/阵雪"

            rows.append([date, weather_desc, max_temp, min_temp, precip, wind, warning])

        return {
            "status": "success",
            "message": f"已获取 {display_name} 未来 {len(rows)} 天天气预报",
            "output_format": "table",
            "data": {
                "columns": columns,
                "rows": rows
            }
        }

    except requests.exceptions.Timeout:
        return {
            "status": "failed",
            "message": "网络连接超时，请稍后重试",
            "output_format": "text",
            "data": {"text": "网络超时"}
        }
    except requests.exceptions.ConnectionError:
        return {
            "status": "failed",
            "message": "网络连接异常，请检查网络或稍后重试",
            "output_format": "text",
            "data": {"text": "网络错误"}
        }
    except Exception as e:
        return {
            "status": "failed",
            "message": f"发生未知错误: {str(e)}",
            "output_format": "text",
            "data": {"text": "未知错误"}
        }