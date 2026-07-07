import os
import pandas as pd
from typing import Any, Dict

def execute(**kwargs) -> Dict[str, Any]:
    """
    从输入的文本文件中自动提取“颜色”、“沉淀时间”、“评估分数”三个字段，
    并以 CSV 格式输出结果文件。

    Args:
        input_file (str): 待清洗的文本文件路径，必需。
        output_file (str, optional): 输出CSV文件路径，默认为'output.csv'。
        delimiter (str, optional): 文件分隔符，默认为制表符'\t'。
        encoding (str, optional): 文件编码，默认为'utf-8'。

    Returns:
        dict: 包含执行状态、消息、输出格式和数据的字典。
              成功时 data 中包含 'file_path' 指向生成的CSV文件。
    """
    # ========== 参数提取与验证 ==========
    input_file = kwargs.get('input_file')
    if not input_file:
        return {
            "status": "failed",
            "message": "缺少必需参数 input_file",
            "output_format": "file",
            "data": {}
        }

    if not os.path.isfile(input_file):
        return {
            "status": "failed",
            "message": "输入文件不存在",
            "output_format": "file",
            "data": {}
        }

    output_file = kwargs.get('output_file', 'output.csv')
    delimiter = kwargs.get('delimiter', '\t')
    encoding = kwargs.get('encoding', 'utf-8')

    # ========== 读取文件 ==========
    try:
        df = pd.read_csv(input_file, delimiter=delimiter, encoding=encoding, dtype=str)
    except Exception as e:
        return {
            "status": "failed",
            "message": f"文件读取失败，请检查分隔符和文件结构: {str(e)}",
            "output_format": "file",
            "data": {}
        }

    # ========== 目标字段智能匹配 ==========
    target_keywords = ['颜色', '沉淀时间', '评估分数']
    column_map = {}
    found_columns = []

    for keyword in target_keywords:
        matched = None
        for col in df.columns:
            # 支持模糊匹配，例如“颜色 (nm)”、“沉淀时间(s)”等
            if keyword in str(col):
                matched = col
                break
        if matched:
            column_map[keyword] = matched
            found_columns.append(keyword)

    # 未找到任何目标字段
    if not found_columns:
        return {
            "status": "failed",
            "message": f"未找到任何目标字段，现有列名: {list(df.columns)}",
            "output_format": "file",
            "data": {}
        }

    # ========== 提取列并生成输出 DataFrame ==========
    extracted_data = {}
    for keyword in target_keywords:
        if keyword in column_map:
            extracted_data[keyword] = df[column_map[keyword]]
        else:
            # 缺失字段填充空值
            extracted_data[keyword] = [None] * len(df)

    result_df = pd.DataFrame(extracted_data)

    # ========== 写入 CSV 文件 ==========
    try:
        result_df.to_csv(output_file, index=False, encoding='utf-8')
    except Exception as e:
        return {
            "status": "failed",
            "message": f"无法写入输出文件: {str(e)}",
            "output_format": "file",
            "data": {}
        }

    return {
        "status": "success",
        "message": f"数据清洗完成，提取 {len(result_df)} 条记录",
        "output_format": "file",
        "data": {
            "file_path": output_file
        }
    }