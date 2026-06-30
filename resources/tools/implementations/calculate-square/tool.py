from typing import Any

def execute(**kwargs) -> dict[str, Any]:
    """
    计算给定数字的平方。

    Args:
        number (float, required): 要计算平方的数字。

    Returns:
        dict: 包含以下键的字典：
            - status (str): 'success' 或 'failed'。
            - message (str): 操作结果描述。
            - data (dict): 成功时包含 'number' 和 'square'；失败时为空字典。
    """
    try:
        # 验证必需参数
        number = kwargs.get("number")
        if number is None:
            raise ValueError("缺少必需参数: number")

        # 转换为浮点数（允许传入字符串数字）
        number = float(number)

        square = number ** 2

        return {
            "status": "success",
            "message": f"{number} 的平方是 {square}",
            "data": {
                "number": number,
                "square": square
            }
        }
    except Exception as e:
        return {
            "status": "failed",
            "message": f"计算失败: {str(e)}",
            "data": {}
        }