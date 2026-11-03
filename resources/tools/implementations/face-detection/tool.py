import os
from pathlib import Path
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
    人脸检测工具，利用OpenCV Haar级联分类器检测图像中的人脸。

    Args:
        image_path (str): 待检测图片文件的路径，支持常见格式（如 .jpg, .png, .bmp）。

    Returns:
        dict: 返回字典，包含:
            - status (str): "success" 表示检测成功，"failed" 表示失败。
            - message (str): 结果描述信息。
            - data (list): 检测到的人脸列表，每张人脸表示为 [x, y, width, height]。
    """
    # 参数验证
    if 'image_path' not in kwargs:
        return {"status": "failed", "message": "缺少必需参数: image_path", "data": []}
    
    image_path = kwargs['image_path']
    if not isinstance(image_path, str):
        return {"status": "failed", "message": "image_path 必须为字符串", "data": []}
    
    if not os.path.isfile(image_path):
        return {"status": "failed", "message": f"文件未找到: {image_path}", "data": []}
    
    # 尝试导入OpenCV
    try:
        import cv2
    except ImportError:
        return {
            "status": "failed",
            "message": "OpenCV (cv2) 未安装，请执行 'pip install opencv-python' 安装",
            "data": []
        }
    
    try:
        # 读取图像
        img = cv2.imread(image_path)
        if img is None:
            return {
                "status": "failed",
                "message": f"无法加载图像文件: {image_path}，请检查文件格式",
                "data": []
            }
        
        # 灰度转换
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 加载Haar级联分类器
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        if not os.path.isfile(cascade_path):
            # venv 中可能缺失，尝试从工具目录加载
            tool_dir = os.environ.get('TOOL_DIR', '')
            if tool_dir:
                cascade_path = os.path.join(tool_dir, 'haarcascade_frontalface_default.xml')
        if not os.path.isfile(cascade_path):
            return {"status": "failed", "message": "Haar级联文件缺失", "data": []}
        
        face_cascade = cv2.CascadeClassifier(cascade_path)
        
        # 检测人脸
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(30, 30)
        )
        
        face_list = [[int(x), int(y), int(w), int(h)] for (x, y, w, h) in faces]

        # 在原图上绘制人脸框
        output_img = img.copy()
        for (x, y, w, h) in faces:
            cv2.rectangle(output_img, (x, y), (x + w, y + h), (0, 255, 0), 2)

        # 保存标注图片到用户数据目录（不写进输入图片所在目录，避免污染数据源）
        out_dir = _out_dir("face-detection")
        result_path = str(out_dir / f"face_detected_{os.path.basename(image_path)}")
        cv2.imwrite(result_path, output_img)

        return {
            "status": "success",
            "output_format": "image",
            "message": f"检测到 {len(face_list)} 张人脸",
            "data": {"image_path": result_path, "face_count": len(face_list), "faces": face_list}
        }
        
    except Exception as e:
        return {
            "status": "failed",
            "message": f"人脸检测过程中发生错误: {str(e)}",
            "data": []
        }