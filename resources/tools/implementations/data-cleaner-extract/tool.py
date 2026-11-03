import os
import sys
from pathlib import Path

import pandas as pd
from typing import Any, Dict

# ── 运行目录（由平台注入，禁止写死绝对路径或按 __file__ 层级反推）──
# SOTABAND_USER_HOME 当前用户目录：本工具的一切产物写这里
def _env_path(name):
    v = os.environ.get(name, "").strip()
    return Path(v) if v else None

_ROOT = _env_path("SOTABAND_ROOT") or Path.cwd()
# 用户目录：env 缺失时按「SOTABAND_HOME 根 + uid」还原，绝不退回系统根
_USER_HOME = _env_path("SOTABAND_USER_HOME") or (
    (_env_path("SOTABAND_HOME") or _ROOT.parent / "SB-USER")
    / (os.environ.get("SOTABAND_USER_ID", "").strip() or "0")
)

# ── 数据目录（产物一律落用户目录，系统根随 git 分发不可写脏）──
_DATA_DIR = _USER_HOME / "data"

def _out_dir(*parts) -> Path:
    """本工具产物目录：USER_HOME/data/<工具或子目录>/<...>，自动创建。"""
    d = _DATA_DIR.joinpath(*parts) if parts else _DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


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

    output_file = str(kwargs.get('output_file') or '').strip() or 'output.csv'
    # 只给文件名时落用户数据目录：CWD 不可作为产物落点（换机器/换用户会写脏别处）
    if not os.path.dirname(output_file):
        output_file = str(_out_dir('data-cleaner-extract') / output_file)
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