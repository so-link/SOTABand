import os
from typing import Any, Dict

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
            return {"status": "failed", "message": "Haar级联文件缺失", "data": []}
        
        face_cascade = cv2.CascadeClassifier(cascade_path)
        
        # 检测人脸
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(30, 30)
        )
        
        # 转换为列表格式
        face_list = [[int(x), int(y), int(w), int(h)] for (x, y, w, h) in faces]
        
        return {
            "status": "success",
            "message": f"检测到 {len(face_list)} 张人脸",
            "data": face_list
        }
        
    except Exception as e:
        return {
            "status": "failed",
            "message": f"人脸检测过程中发生错误: {str(e)}",
            "data": []
        }