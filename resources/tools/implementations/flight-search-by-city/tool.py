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

# 中文城市名到机场IATA代码列表的映射（内置）
_CITY_TO_IATA = {
    "北京": ["PEK", "PKX"],
    "上海": ["PVG", "SHA"],
    "广州": ["CAN"],
    "深圳": ["SZX"],
    "成都": ["CTU", "TFU"],
    "重庆": ["CKG"],
    "昆明": ["KMG"],
    "西安": ["XIY"],
    "杭州": ["HGH"],
    "南京": ["NKG"],
    "武汉": ["WUH"],
    "长沙": ["CSX"],
    "厦门": ["XMN"],
    "青岛": ["TAO"],
    "大连": ["DLC"],
    "天津": ["TSN"],
    "三亚": ["SYX"],
    "海口": ["HAK"],
    "贵阳": ["KWE"],
    "哈尔滨": ["HRB"],
    "沈阳": ["SHE"],
    "乌鲁木齐": ["URC"],
    "拉萨": ["LXA"],
    "郑州": ["CGO"],
    "济南": ["TNA"],
    "太原": ["TYN"],
    "石家庄": ["SJW"],
    "呼和浩特": ["HET"],
    "兰州": ["LHW"],
    "西宁": ["XNN"],
    "银川": ["INC"],
    "福州": ["FOC"],
    "南宁": ["NNG"],
    "南昌": ["KHN"],
    "合肥": ["HFE"],
    "长春": ["CGQ"],
    "宁波": ["NGB"],
    "温州": ["WNZ"],
    "珠海": ["ZUH"],
    "桂林": ["KWL"],
    "宜昌": ["YIH"],
    "丽江": ["LJG"],
    "西双版纳": ["JHG"],
    "烟台": ["YNT"],
    "威海": ["WEH"],
    "汕头": ["SWA"],
    "湛江": ["ZHA"],
    "北海": ["BHY"],
    "大理": ["DLU"],
    "牡丹江": ["MDG"],
    "包头": ["BAV"],
    "鄂尔多斯": ["DSN"],
    "延吉": ["YNJ"],
    "张家界": ["DYG"],
    "黄山市": ["TXN"],
    "武夷山": ["WUS"],
    "遵义": ["ZYI"],
    "宜宾": ["YBP"],
    "绵阳": ["MIG"],
    "泸州": ["LZO"],
    "南充": ["NAO"],
    "达州": ["DAX"],
    "万州": ["WXN"],
    "黔江": ["JIQ"],
    "迪庆": ["DIG"],
    "香格里拉": ["DIG"],
    "德宏": ["LUM"],
    "芒市": ["LUM"],
    "腾冲": ["TCZ"],
    "保山": ["BSD"],
    "昭通": ["ZAT"],
    "文山": ["WNH"],
    "临沧": ["LNJ"],
    "普洱": ["SYM"],
    "澜沧": ["JMJ"],
    "沧源": ["CWJ"],
    "怒江": ["NJS"],
    "六盘水": ["LPF"],
    "铜仁": ["TEN"],
    "兴义": ["ACX"],
    "安顺": ["AVA"],
    "毕节": ["BFJ"],
}

# Aviationstack API 配置
_AVIATION_STACK_KEY = "7aa3e2da4ba27c8a7d386ef8820cddad"
_BASE_URL = "https://api.aviationstack.com/v1/flights"

def _fetch_flights(iata: str, direction: str, limit: int = 100) -> list:
    """
    调用 Aviationstack API 查询指定机场的航班。
    direction: 'dep' 表示出发，'arr' 表示到达。
    返回航班数据列表。
    """
    params = {
        "access_key": _AVIATION_STACK_KEY,
        "limit": limit,
    }
    if direction == "dep":
        params["dep_iata"] = iata
    else:
        params["arr_iata"] = iata

    try:
        resp = requests.get(_BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        # 检查错误
        if payload.get("error"):
            raise Exception(payload["error"].get("message", "API error"))
        return payload.get("data", [])
    except requests.RequestException as e:
        raise Exception(f"航班信息查询失败: {str(e)}")
    except Exception as e:
        raise

def execute(**kwargs) -> dict[str, Any]:
    """
    根据城市名和数量查询航班，输出表格。
    参数:
        city: 城市名（支持中文）
        n: 期望返回的最大航班数（正整数）
    """
    city = kwargs.get("city", "").strip()
    try:
        n = int(kwargs.get("n", 0))
    except (ValueError, TypeError):
        return {"status": "failed", "message": "参数 n 必须为正整数"}

    # 参数校验
    if not city:
        return {"status": "failed", "message": "城市名不能为空"}
    if n <= 0:
        return {"status": "failed", "message": "参数 n 必须为正整数"}

    # 中文城市名转换
    iata_list = _CITY_TO_IATA.get(city)
    if not iata_list:
        return {"status": "failed", "message": "不支持的城市名，请检查输入"}

    # 查询与该城市相关的所有航班（出发和到达）
    seen = set()  # 用于去重，以 (flight_iata, dep_iata, arr_iata, scheduled_dep) 为特征
    all_flights = []

    for code in iata_list:
        # 查询出发航班
        try:
            dep_flights = _fetch_flights(code, "dep")
            for flight in dep_flights:
                key = (
                    flight.get("flight", {}).get("iata"),
                    flight.get("departure", {}).get("iata"),
                    flight.get("arrival", {}).get("iata"),
                    flight.get("departure", {}).get("scheduled"),
                )
                if key not in seen:
                    seen.add(key)
                    all_flights.append(flight)
        except Exception:
            # 单个IATA代码查失败不中断整体，继续下一个
            pass

        # 查询到达航班
        try:
            arr_flights = _fetch_flights(code, "arr")
            for flight in arr_flights:
                key = (
                    flight.get("flight", {}).get("iata"),
                    flight.get("departure", {}).get("iata"),
                    flight.get("arrival", {}).get("iata"),
                    flight.get("departure", {}).get("scheduled"),
                )
                if key not in seen:
                    seen.add(key)
                    all_flights.append(flight)
        except Exception:
            pass

    # 按计划出发时间排序（可选，保证一致性）
    all_flights.sort(key=lambda f: f.get("departure", {}).get("scheduled") or "")

    # 限制数量
    flights = all_flights[:n]

    # 构建表格
    columns = ["航班号", "航空公司", "出发机场", "到达机场", "计划出发时间", "计划到达时间"]
    rows = []
    for f in flights:
        flight_number = f.get("flight", {}).get("iata", "")
        airline_name = ""
        airlines = f.get("airline", {})
        if isinstance(airlines, dict):
            airline_name = airlines.get("name", "")
        elif isinstance(airlines, list) and len(airlines) > 0:
            airline_name = airlines[0].get("name", "")
        departure_airport = f.get("departure", {}).get("airport", "")
        arrival_airport = f.get("arrival", {}).get("airport", "")
        scheduled_dep = f.get("departure", {}).get("scheduled", "")
        scheduled_arr = f.get("arrival", {}).get("scheduled", "")
        rows.append([flight_number, airline_name, departure_airport, arrival_airport, scheduled_dep, scheduled_arr])

    if not rows:
        return {
            "status": "success",
            "message": "未找到相关航班信息",
            "output_format": "table",
            "data": {
                "columns": columns,
                "rows": [],
            }
        }

    return {
        "status": "success",
        "message": f"成功查询到 {len(rows)} 个航班",
        "output_format": "table",
        "data": {
            "columns": columns,
            "rows": rows,
        }
    }