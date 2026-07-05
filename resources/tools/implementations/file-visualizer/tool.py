import os
import mimetypes
from typing import Any, Dict, List, Optional, Union

import pandas as pd
from PIL import Image, UnidentifiedImageError


def execute(**kwargs: Any) -> Dict[str, Any]:
    """
    根据输入文件的类型，自动选择合适的可视化方式：
    - CSV 文件 → 返回表格数据，用于界面渲染为交互式表格
    - 图片文件（PNG, JPG, GIF, BMP 等）→ 返回图片路径，用于界面直接绘制图片

    Args:
        file_path (str): 必填，待可视化的文件路径（绝对路径或相对路径）。
        max_rows (int, optional): CSV 模式下返回的最大行数，默认 1000。

    Returns:
        dict: 包含以下字段的字典
            - status (str): 执行状态，'success' 或 'failed'
            - message (str): 结果说明
            - output_format (str): 输出类型，'table' 或 'image'（失败时为空字符串）
            - data (dict): 输出数据，格式取决于 output_format
    """
    # ---------------------------
    # 1. 参数校验
    # ---------------------------
    file_path = kwargs.get('file_path')
    if not file_path or not isinstance(file_path, str):
        return {
            "status": "failed",
            "message": "缺少必填参数 file_path 或参数类型不正确",
            "output_format": "",
            "data": {}
        }

    max_rows = kwargs.get('max_rows', 1000)
    if not isinstance(max_rows, (int, float)):
        try:
            max_rows = int(max_rows)
        except (ValueError, TypeError):
            return {
                "status": "failed",
                "message": "max_rows 参数必须为整数",
                "output_format": "",
                "data": {}
            }
    max_rows = int(max_rows)

    # ---------------------------
    # 2. 检查文件是否存在
    # ---------------------------
    if not os.path.exists(file_path):
        return {
            "status": "failed",
            "message": "文件不存在",
            "output_format": "",
            "data": {}
        }

    # ---------------------------
    # 3. 判断文件类型
    # ---------------------------
    mime_type, _ = mimetypes.guess_type(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    image_exts = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}

    try:
        # ---------- CSV 处理 ----------
        if mime_type == 'text/csv' or ext == '.csv':
            try:
                df = pd.read_csv(file_path)
            except Exception as e:
                return {
                    "status": "failed",
                    "message": f"CSV 格式错误: {e}",
                    "output_format": "",
                    "data": {}
                }

            # 截取行数
            if len(df) > max_rows:
                df = df.head(max_rows)

            # 转换列名和行数据（保持 None 用于 JSON 的 null）
            columns: List[str] = df.columns.tolist()
            rows: List[List[Any]] = df.astype(object).where(pd.notnull(df), None).values.tolist()

            return {
                "status": "success",
                "message": "表格数据准备完成",
                "output_format": "table",
                "data": {
                    "columns": columns,
                    "rows": rows
                }
            }

        # ---------- 图片处理 ----------
        if mime_type and mime_type.startswith('image/') or ext in image_exts:
            # 尝试用 Pillow 打开验证
            try:
                with Image.open(file_path) as img:
                    img.verify()          # 验证图片完整性
                # 重新打开以消耗加载器（verify 后需要）
                with Image.open(file_path) as img:
                    img.load()
            except (UnidentifiedImageError, Exception):
                return {
                    "status": "failed",
                    "message": "无效的图像文件",
                    "output_format": "",
                    "data": {}
                }

            return {
                "status": "success",
                "message": "图片可渲染",
                "output_format": "image",
                "data": {
                    "image_path": file_path
                }
            }

        # ---------- 不支持的类型 ----------
        return {
            "status": "failed",
            "message": "不支持的文件类型",
            "output_format": "",
            "data": {}
        }

    except Exception as e:
        return {
            "status": "failed",
            "message": f"处理文件时发生未知错误: {e}",
            "output_format": "",
            "data": {}
        }