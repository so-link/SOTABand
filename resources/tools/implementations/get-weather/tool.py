from typing import Any
import requests

def execute(**kwargs) -> dict[str, Any]:
    """
    获取指定城市的当前天气信息。

    Args:
        city (str): 需要查询天气的城市名称，必填。支持中文或英文。

    Returns:
        dict: 包含以下键值的字典：
            - status (str): "success" 表示成功, "failed" 表示失败。
            - message (str): 操作结果说明。
            - data (dict): 成功时返回天气数据，失败时为空字典。
    """
    try:
        # 验证 city 参数
        city = kwargs.get("city")
        if not city or not isinstance(city, str) or city.strip() == "":
            return {"status": "failed", "message": "缺少必填参数 city", "data": {}}

        city_name = city.strip()

        # 1. 使用 Open-Meteo Geocoding API 将城市名称转为经纬度
        geo_url = "https://geocoding-api.open-meteo.com/v1/search"
        geo_params = {
            "name": city_name,
            "count": 1,
            "language": "zh",
            "format": "json"
        }
        geo_resp = requests.get(geo_url, params=geo_params, timeout=10)
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()

        if not geo_data.get("results"):
            return {"status": "failed", "message": f"未找到城市: {city_name}", "data": {}}

        location = geo_data["results"][0]
        latitude = location["latitude"]
        longitude = location["longitude"]
        resolved_name = location.get("name", city_name)  # 使用官方名称

        # 2. 使用 Open-Meteo Forecast API 获取当前天气
        weather_url = "https://api.open-meteo.com/v1/forecast"
        weather_params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
            "timezone": "auto",
            "forecast_days": 1
        }
        weather_resp = requests.get(weather_url, params=weather_params, timeout=10)
        weather_resp.raise_for_status()
        weather_data = weather_resp.json()

        current = weather_data.get("current", {})
        temperature = current.get("temperature_2m", "N/A")
        humidity = current.get("relative_humidity_2m", "N/A")
        wind_speed = current.get("wind_speed_10m", "N/A")
        weather_code = current.get("weather_code", 0)

        # 简单的天气码映射（中文描述）
        weather_desc_map = {
            0: "晴",
            1: "少云",
            2: "多云",
            3: "阴",
            45: "有雾",
            48: "雾凇",
            51: "小毛毛雨",
            53: "中毛毛雨",
            55: "大毛毛雨",
            61: "小雨",
            63: "中雨",
            65: "大雨",
            71: "小雪",
            73: "中雪",
            75: "大雪",
            77: "雪粒",
            80: "阵雨",
            81: "中阵雨",
            82: "大阵雨",
            85: "小阵雪",
            86: "大阵雪",
            95: "雷暴",
            96: "冰雹雷暴",
            99: "强冰雹雷暴"
        }
        weather_desc = weather_desc_map.get(weather_code, f"未知({weather_code})")
        wind_desc = f"{wind_speed} km/h" if wind_speed != "N/A" else "未知"

        # 整理返回数据
        result_data = {
            "city": resolved_name,
            "temperature": f"{temperature}°C",
            "weather": weather_desc,
            "humidity": f"{humidity}%" if humidity != "N/A" else "未知",
            "wind": wind_desc,
        }

        return {
            "status": "success",
            "message": f"成功获取{resolved_name}的天气",
            "data": result_data,
        }

    except requests.exceptions.RequestException as e:
        return {
            "status": "failed",
            "message": f"天气服务请求失败: {str(e)}",
            "data": {},
        }
    except Exception as e:
        return {
            "status": "failed",
            "message": f"获取天气失败: {str(e)}",
            "data": {},
        }