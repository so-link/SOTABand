import math
from typing import Any, Dict

def execute(**kwargs) -> Dict[str, Any]:
    """
    计算圆的面积和以该半径为半径的球的体积。

    Args:
        radius (float): 圆的半径，必须为非负数值。

    Returns:
        dict: 包含以下键的字典：
            - status (str): "success" 或 "failed"
            - message (str): 状态消息
            - data (dict): 成功时包含 {"area": <面积值>, "volume": <体积值>}，失败时为空字典 {}
    """
    try:
        # 检查必需参数是否存在
        if 'radius' not in kwargs:
            return {
                "status": "failed",
                "message": "缺少必需参数: radius",
                "data": {}
            }

        radius = kwargs['radius']

        # 尝试转换为浮点数
        try:
            r = float(radius)
        except (TypeError, ValueError):
            return {
                "status": "failed",
                "message": "半径必须为数值类型",
                "data": {}
            }

        # 检查非负条件
        if r < 0:
            return {
                "status": "failed",
                "message": "半径必须为非负数值",
                "data": {}
            }

        # 计算圆的面积和球的体积
        area = math.pi * r * r
        volume = (4.0 / 3.0) * math.pi * (r ** 3)
        return {
            "status": "success",
            "message": "计算成功",
            "data": {"area": area, "volume": volume}
        }

    except Exception as e:
        return {
            "status": "failed",
            "message": f"发生未知错误: {str(e)}",
            "data": {}
        }