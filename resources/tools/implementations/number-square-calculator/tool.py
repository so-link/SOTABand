import math
from typing import Any, Union


def execute(**kwargs) -> dict[str, Any]:
    """
    计算输入数字的平方值。

    Args:
        number (float | int): 需要计算平方的输入数字，必填参数。

    Returns:
        dict[str, Any]: 包含以下键的字典：
            - status (str): "success" 表示成功，"failed" 表示失败。
            - message (str): 操作结果描述。
            - data (dict): 成功时包含计算结果，失败时为空字典。
                - input (float | int): 原始输入数字。
                - square (float | int): 输入数字的平方值。
    """
    try:
        # 验证必填参数
        if "number" not in kwargs or kwargs["number"] is None:
            return {
                "status": "failed",
                "message": "缺少必填参数 'number'",
                "data": {}
            }

        raw_value = kwargs["number"]

        # 类型校验与转换
        if not isinstance(raw_value, (int, float)):
            try:
                raw_value = float(raw_value)
            except (ValueError, TypeError):
                return {
                    "status": "failed",
                    "message": f"参数 'number' 类型无效，期望 float 或 int，实际为 {type(raw_value).__name__}",
                    "data": {}
                }

        # 计算平方
        result = math.pow(raw_value, 2)

        # 若原输入是整数且平方仍为整数，返回整数形式
        if isinstance(kwargs["number"], int) or (isinstance(raw_value, float) and raw_value == int(raw_value) and result == int(result)):
            result = int(result)
            raw_value = int(raw_value)

        return {
            "status": "success",
            "message": f"数字 {raw_value} 的平方计算完成",
            "data": {
                "input": raw_value,
                "square": result
            }
        }

    except OverflowError:
        return {
            "status": "failed",
            "message": "数值过大导致溢出，无法计算平方",
            "data": {}
        }
    except Exception as e:
        return {
            "status": "failed",
            "message": f"计算过程中发生错误: {str(e)}",
            "data": {}
        }