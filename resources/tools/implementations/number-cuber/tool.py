import typing
from typing import Any

def execute(**kwargs) -> dict[str, Any]:
    """
    计算一个数字的三次方 (number^3)。

    Args:
        number (int 或 float): 需要计算三次方的数字。必填。

    Returns:
        dict: 包含以下键的字典：
            - status (str): "success" 表示成功, "failed" 表示失败。
            - message (str): 操作结果的描述信息。
            - data (dict): 成功时包含键 "result"，值为计算结果；失败时为空字典。
    """
    # 校验必填参数
    if "number" not in kwargs:
        return {"status": "failed", "message": "缺少必填参数: number", "data": {}}

    raw = kwargs["number"]
    
    try:
        # 检查是否为 int 或 float，若不是则尝试转换（容错字符串输入）
        if not isinstance(raw, (int, float)):
            # 如果传入的是可以解析为数值的字符串，则尝试转换
            try:
                num = float(raw)
            except (ValueError, TypeError):
                return {"status": "failed", "message": "无效的数字: 必须是 int 或 float 类型，或可转换为数值的字符串", "data": {}}
        else:
            num = raw

        result = num ** 3
        return {
            "status": "success",
            "message": "三次方计算成功",
            "data": {"result": result}
        }
    except Exception as e:
        return {"status": "failed", "message": f"计算时发生错误: {str(e)}", "data": {}}