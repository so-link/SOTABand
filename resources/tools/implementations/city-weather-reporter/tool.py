
# === SOTABand 工具标准模板 ===
import os, sys, json, time
from pathlib import Path
from typing import Any
import requests
from urllib.parse import quote  # 使用标准库URL编码，更稳定

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

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


# 常用中文城市名到英文名的映射（用于备用天气API）
CITY_NAME_MAP = {
    "广州": "Guangzhou",
    "北京": "Beijing",
    "上海": "Shanghai",
    "深圳": "Shenzhen",
    "杭州": "Hangzhou",
    "成都": "Chengdu",
    "重庆": "Chongqing",
    "武汉": "Wuhan",
    "南京": "Nanjing",
    "天津": "Tianjin",
    "西安": "Xi'an",
    "厦门": "Xiamen",
    "青岛": "Qingdao",
}

# WMO Weather Codes 简单映射表（中英文）
WMO_CODES = {
    0: {"en": "Clear sky", "zh_cn": "晴天"},
    1: {"en": "Mainly clear", "zh_cn": "大部晴朗"},
    2: {"en": "Partly cloudy", "zh_cn": "少云"},
    3: {"en": "Overcast", "zh_cn": "阴天"},
    45: {"en": "Fog", "zh_cn": "雾"},
    48: {"en": "Depositing rime fog", "zh_cn": "雾凇"},
    51: {"en": "Light drizzle", "zh_cn": "小毛毛雨"},
    53: {"en": "Moderate drizzle", "zh_cn": "中毛毛雨"},
    55: {"en": "Dense drizzle", "zh_cn": "大毛毛雨"},
    56: {"en": "Light freezing drizzle", "zh_cn": "小冻毛毛雨"},
    57: {"en": "Dense freezing drizzle", "zh_cn": "大冻毛毛雨"},
    61: {"en": "Slight rain", "zh_cn": "小雨"},
    63: {"en": "Moderate rain", "zh_cn": "中雨"},
    65: {"en": "Heavy rain", "zh_cn": "大雨"},
    66: {"en": "Light freezing rain", "zh_cn": "小冻雨"},
    67: {"en": "Heavy freezing rain", "zh_cn": "大冻雨"},
    71: {"en": "Slight snow fall", "zh_cn": "小雪"},
    73: {"en": "Moderate snow fall", "zh_cn": "中雪"},
    75: {"en": "Heavy snow fall", "zh_cn": "大雪"},
    77: {"en": "Snow grains", "zh_cn": "米雪"},
    80: {"en": "Slight rain showers", "zh_cn": "小阵雨"},
    81: {"en": "Moderate rain showers", "zh_cn": "中阵雨"},
    82: {"en": "Violent rain showers", "zh_cn": "大阵雨"},
    85: {"en": "Slight snow showers", "zh_cn": "小阵雪"},
    86: {"en": "Heavy snow showers", "zh_cn": "大阵雪"},
    95: {"en": "Thunderstorm", "zh_cn": "雷暴"},
    96: {"en": "Thunderstorm with slight hail", "zh_cn": "雷暴伴小冰雹"},
    99: {"en": "Thunderstorm with heavy hail", "zh_cn": "雷暴伴大冰雹"},
}


def _fetch_weather_backup(city: str, units: str, lang: str) -> dict:
    """
    使用 wttr.in 作为备用天气数据源，返回与系统 API 相似的结构。
    具有多级回退机制以提高成功率。
    参数：
        city: 城市名（支持中文，会优先尝试映射为英文名）
        units: metric 或 imperial
        lang: zh_cn 或 en
    """
    # 语言映射：wttr.in 使用带连字符的格式，如 zh-cn
    lang_map = {"zh_cn": "zh-cn", "en": "en"}
    requested_lang = lang_map.get(lang, "en")
    # 单位映射：metric->m, imperial->u
    unit_map = {"metric": "m", "imperial": "u"}
    wttr_unit = unit_map.get(units, "m")

    # 优先使用英文城市名，避免中文导致的500错误
    query_city = CITY_NAME_MAP.get(city, city)
    encoded_city = quote(query_city, safe='')
    base_url = f"https://wttr.in/{encoded_city}?format=j1&u={wttr_unit}"

    # 增加常见请求头，提高成功率
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    # 尝试顺序：用户语言 -> 英文 -> 不加语言参数
    lang_attempts = [requested_lang, "en", None]

    last_error = None
    for attempt_lang in lang_attempts:
        url = base_url
        if attempt_lang is not None:
            url += f"&lang={attempt_lang}"

        try:
            resp = requests.get(url, timeout=10, headers=headers)
            if resp.status_code == 404:
                return {
                    "status": "failed",
                    "message": f"未找到城市 '{city}' 的天气信息",
                    "data": {}
                }
            # wttr.in 偶尔返回 500，尝试降级请求
            if resp.status_code != 200:
                last_error = f"备用天气源返回状态码 {resp.status_code}"
                continue

            wdata = resp.json()

            # 提取当前天气信息
            current = wdata.get("current_condition", [{}])[0]
            if not current:
                last_error = "备用天气源没有返回数据"
                continue

            # 根据单位提取温度和体感温度
            if units == "metric":
                temperature = float(current["temp_C"]) if "temp_C" in current else None
                feels_like = float(current["FeelsLikeC"]) if "FeelsLikeC" in current else None
            else:
                temperature = float(current["temp_F"]) if "temp_F" in current else None
                feels_like = float(current["FeelsLikeF"]) if "FeelsLikeF" in current else None

            humidity = float(current["humidity"]) if "humidity" in current else None
            pressure = float(current["pressure"]) if "pressure" in current else None

            # 风速处理：metric 转换为 m/s，imperial 直接使用 windspeedMiles（mph）
            if units == "metric":
                wind_speed_kmh = float(current.get("windspeedKmph", 0))
                wind_speed = round(wind_speed_kmh / 3.6, 2)
            else:
                wind_speed_mph = current.get("windspeedMiles", 0)
                wind_speed = float(wind_speed_mph) if wind_speed_mph else 0.0

            wind_dir = current.get("winddir16Point", "")
            rainfall = float(current.get("precipMM", 0.0))
            weather_desc = current.get("weatherDesc", [{}])[0].get("value", "")
            city_name = current.get("observation_point", query_city)

            # 警报和台风预警 wttr.in 不提供，设为空
            alerts = []
            typhoon_warning = None

            weather_data = {
                "city": city_name,
                "temperature": temperature,
                "feels_like": feels_like,
                "humidity": humidity,
                "pressure": pressure,
                "wind_speed": wind_speed,
                "wind_direction": wind_dir,
                "rainfall": rainfall,
                "weather_description": weather_desc,
                "alerts": alerts,
                "typhoon_warning": typhoon_warning,
            }
            return {
                "status": "success",
                "message": "天气数据获取成功（备用源）",
                "data": weather_data
            }

        except requests.exceptions.RequestException as e:
            last_error = f"备用天气请求异常: {str(e)}"
            continue
        except Exception as e:
            last_error = f"备用天气解析失败: {str(e)}"
            continue

    return {
        "status": "failed",
        "message": last_error or "备用天气源获取失败",
        "data": {}
    }


def _fetch_weather_openmeteo(city: str, units: str, lang: str) -> dict:
    """
    使用 Open-Meteo 免费 API 作为第二备用天气源。
    需要先通过 Geocoding API 获取城市坐标。
    """
    try:
        # 1. 获取城市坐标
        geo_url = "https://geocoding-api.open-meteo.com/v1/search"
        # 若城市名在映射中，尽量使用英文名提高匹配率
        geo_query = CITY_NAME_MAP.get(city, city)
        geo_params = {
            "name": geo_query,
            "count": 1,
            "language": "zh" if lang == "zh_cn" else "en",
            "format": "json",
        }
        headers = {"Accept": "application/json"}
        geo_resp = requests.get(geo_url, params=geo_params, timeout=10, headers=headers)
        if geo_resp.status_code != 200:
            return {"status": "failed", "message": f"Open-Meteo 地理编码失败: HTTP {geo_resp.status_code}"}
        geo_data = geo_resp.json()
        results = geo_data.get("results", [])
        if not results:
            return {"status": "failed", "message": f"未找到城市 '{city}' 的坐标信息"}
        latitude = results[0]["latitude"]
        longitude = results[0]["longitude"]
        city_name = results[0].get("name", city)

        # 2. 获取天气数据
        weather_url = "https://api.open-meteo.com/v1/forecast"
        # 根据units设置温度单位
        temperature_unit = "celsius" if units == "metric" else "fahrenheit"
        # 请求当前天气参数
        current_params = [
            "temperature_2m",
            "relative_humidity_2m",
            "apparent_temperature",
            "precipitation",
            "weather_code",
            "pressure_msl",
            "wind_speed_10m",
            "wind_direction_10m",
        ]
        weather_params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": ",".join(current_params),
            "temperature_unit": temperature_unit,
            "wind_speed_unit": "ms" if units == "metric" else "mph",  # m/s 或 mph
            "precipitation_unit": "mm",
            "timezone": "auto",
        }
        weather_resp = requests.get(weather_url, params=weather_params, timeout=10, headers=headers)
        if weather_resp.status_code != 200:
            return {"status": "failed", "message": f"Open-Meteo 天气获取失败: HTTP {weather_resp.status_code}"}
        wdata = weather_resp.json()
        current = wdata.get("current", {})

        # 提取各个字段
        temperature = current.get("temperature_2m")
        feels_like = current.get("apparent_temperature")
        humidity = current.get("relative_humidity_2m")
        pressure = current.get("pressure_msl")
        wind_speed = current.get("wind_speed_10m")
        wind_direction = current.get("wind_direction_10m")  # 度数，如果存在
        if wind_direction is not None:
            # 转换为文字风向（简单转换）
            directions = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
            index = int((wind_direction + 22.5) // 45) % 8
            wind_dir_text = directions[index]
        else:
            wind_dir_text = ""
        rainfall = current.get("precipitation", 0.0)
        weather_code = current.get("weather_code", 0)
        # 天气描述映射
        lang_key = "zh_cn" if lang == "zh_cn" else "en"
        weather_desc = WMO_CODES.get(weather_code, {}).get(lang_key, f"Code {weather_code}")

        weather_data = {
            "city": city_name,
            "temperature": temperature,
            "feels_like": feels_like,
            "humidity": humidity,
            "pressure": pressure,
            "wind_speed": wind_speed,
            "wind_direction": wind_dir_text,
            "rainfall": rainfall,
            "weather_description": weather_desc,
            "alerts": [],
            "typhoon_warning": None,
        }
        return {
            "status": "success",
            "message": "天气数据获取成功（Open-Meteo）",
            "data": weather_data
        }
    except Exception as e:
        return {"status": "failed", "message": f"Open-Meteo 调用异常: {str(e)}", "data": {}}


def execute(**kwargs) -> dict:
    try:
        # ── 加载环境变量（备用）──
        if load_dotenv:
            dotenv_path = _PROJECT_ROOT / '.env'
            if dotenv_path.exists():
                load_dotenv(dotenv_path)

        city = kwargs.get("city", "")
        units = kwargs.get("units", "metric")
        lang = kwargs.get("lang", "zh_cn")
        output_format = kwargs.get("output_format", "text")

        # ── 参数校验 ──
        if not city or not isinstance(city, str) or not city.strip():
            return {"status": "failed", "message": "城市名称不能为空", "data": {}}
        city = city.strip()

        valid_units = ["metric", "imperial"]
        if units not in valid_units:
            return {
                "status": "failed",
                "message": f"units 参数无效，应为 {valid_units} 之一",
                "data": {}
            }

        valid_langs = ["zh_cn", "en"]
        if lang not in valid_langs:
            return {
                "status": "failed",
                "message": f"lang 参数无效，应为 {valid_langs} 之一",
                "data": {}
            }

        if output_format not in ["text", "table"]:
            output_format = "text"   # 回退为文本

        # ── 多级天气数据获取策略 ──
        response = {}
        # 第一级：系统内置天气 API
        try:
            response = _call_api("weather", city=city, units=units, lang=lang)
        except Exception:
            response = {"status": "failed", "message": "系统 API 调用失败，尝试备用源"}

        # 第二级：wttr.in 备用源
        if response.get("status") != "success":
            response = _fetch_weather_backup(city, units, lang)

        # 第三级：Open-Meteo 免费 API
        if response.get("status") != "success":
            response = _fetch_weather_openmeteo(city, units, lang)

        # 最终检查
        if response.get("status") != "success":
            return {
                "status": "failed",
                "message": response.get("message", "天气查询失败"),
                "data": {}
            }

        data = response.get("data", {})
        weather_data = data

        # ── 按输出格式返回 ──
        if output_format == "table":
            temp_unit = "°C" if units == "metric" else "°F"
            speed_unit = "m/s" if units == "metric" else "mph"

            city_name = weather_data.get("city", city)
            temperature = weather_data.get("temperature")
            feels_like = weather_data.get("feels_like")
            humidity = weather_data.get("humidity")
            pressure = weather_data.get("pressure")
            wind_speed = weather_data.get("wind_speed")
            wind_dir = weather_data.get("wind_direction", "")
            rainfall = weather_data.get("rainfall", 0.0)
            weather_desc = weather_data.get("weather_description", "")
            alerts = weather_data.get("alerts", [])
            typhoon_warning = weather_data.get("typhoon_warning")

            rows = [
                ["城市", city_name],
                ["温度", f"{temperature}{temp_unit}" if temperature is not None else "N/A"],
                ["体感温度", f"{feels_like}{temp_unit}" if feels_like is not None else "N/A"],
                ["湿度", f"{humidity}%" if humidity is not None else "N/A"],
                ["气压", f"{pressure} hPa" if pressure is not None else "N/A"],
                ["风速", f"{wind_speed} {speed_unit}" if wind_speed is not None else "N/A"],
                ["风向", wind_dir],
                ["降雨量(1h)", f"{rainfall} mm" if rainfall is not None else "N/A"],
                ["天气描述", weather_desc],
            ]
            if alerts:
                for alert in alerts:
                    rows.append(["预警", f"{alert.get('title','')} {alert.get('description','')}"])
            if typhoon_warning:
                rows.append(["台风预警", f"{typhoon_warning.get('warning_level','')} {typhoon_warning.get('description','')}"])

            return {
                "status": "success",
                "message": "获取天气数据成功",
                "output_format": "table",
                "data": {
                    "columns": ["指标", "数值"],
                    "rows": rows,
                }
            }
        else:
            temp_unit = "°C" if units == "metric" else "°F"
            speed_unit = "m/s" if units == "metric" else "mph"

            city_name = weather_data.get("city", city)
            temperature = weather_data.get("temperature")
            feels_like = weather_data.get("feels_like")
            humidity = weather_data.get("humidity")
            pressure = weather_data.get("pressure")
            wind_speed = weather_data.get("wind_speed")
            wind_dir = weather_data.get("wind_direction", "")
            rainfall = weather_data.get("rainfall", 0.0)
            weather_desc = weather_data.get("weather_description", "")
            alerts = weather_data.get("alerts", [])
            typhoon_warning = weather_data.get("typhoon_warning")

            lines = [
                f"城市：{city_name}",
                f"温度：{temperature}{temp_unit}" if temperature is not None else "温度：N/A",
                f"体感温度：{feels_like}{temp_unit}" if feels_like is not None else "体感温度：N/A",
                f"湿度：{humidity}%" if humidity is not None else "湿度：N/A",
                f"气压：{pressure} hPa" if pressure is not None else "气压：N/A",
                f"风速：{wind_speed} {speed_unit}" if wind_speed is not None else "风速：N/A",
                f"风向：{wind_dir}",
                f"降雨量：{rainfall} mm" if rainfall is not None else "降雨量：N/A",
                f"天气描述：{weather_desc}",
                "天气预警：" + ("无" if not alerts else "; ".join([a.get('title','') for a in alerts])),
                "台风预警：" + ("无" if not typhoon_warning else typhoon_warning.get('warning_level','')),
            ]
            text = "\n".join(lines)
            return {
                "status": "success",
                "message": "获取天气数据成功",
                "output_format": "text",
                "data": {"text": text}
            }

    except Exception as e:
        return {"status": "failed", "message": f"发生错误: {str(e)}", "data": {}}
