import cv2
import os
import json
from typing import Any, Dict

def execute(**kwargs) -> Dict[str, Any]:
    """
    使用OpenCV Haar级联分类器检测图片中的人脸，并在输出目录中保存带有标注的图片，
    返回检测到的人脸边界框列表（JSON字符串格式）。

    Args:
        image_path (str): 输入图片的文件路径。
        output_dir (str, optional): 输出标注图片的保存目录，默认为"./output"。
        cascade_file (str, optional): OpenCV级联分类器xml文件路径，默认为内置haarcascade_frontalface_default.xml。
        min_face_size (int, optional): 最小人脸尺寸（像素），默认30。
        confidence_threshold (float, optional): 置信度阈值（Haar级联忽略此参数），默认0.5。

    Returns:
        Dict[str, Any]: 包含键"status", "message", "output_format"和"data"的字典。
                        成功时status为"success"，message为提示信息，output_format为"image"，data包含"image_path"。
                        失败时status为"failed"，message为错误信息。
    """
    try:
        # 验证必填参数
        if 'image_path' not in kwargs:
            return {"status": "failed", "message": "Missing required parameter: image_path"}
        
        image_path = kwargs['image_path']
        output_dir = kwargs.get('output_dir', './output')
        cascade_file = kwargs.get('cascade_file', '内置 haarcascade_frontalface_default.xml')
        min_face_size = kwargs.get('min_face_size', 30)
        # confidence_threshold 在 Haar 级联中未使用，保留用于兼容性
        
        # 检查输入图像是否存在
        if not os.path.isfile(image_path):
            return {"status": "failed", "message": f"Image file not found: {image_path}"}
        
        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)
        
        # 确定级联分类器路径
        default_cascade = '内置 haarcascade_frontalface_default.xml'
        if cascade_file == default_cascade or cascade_file == '':
            cascade_path = os.path.join(cv2.data.haarcascades, 'haarcascade_frontalface_default.xml')
        else:
            cascade_path = cascade_file
            if not os.path.isfile(cascade_path):
                return {"status": "failed", "message": f"Cascade file not found: {cascade_path}"}
        
        # 加载级联分类器
        face_cascade = cv2.CascadeClassifier(cascade_path)
        if face_cascade.empty():
            return {"status": "failed", "message": "Failed to load cascade classifier."}
        
        # 读取图像
        img = cv2.imread(image_path)
        if img is None:
            return {"status": "failed", "message": "Failed to load image."}
        
        # 灰度化
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 人脸检测
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(min_face_size, min_face_size)
        )
        
        # 在图像上绘制矩形
        for (x, y, w, h) in faces:
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
        
        # 保存标注后的图像
        base_name = os.path.basename(image_path)
        name, ext = os.path.splitext(base_name)
        output_filename = f"{name}_detected{ext}"
        output_path = os.path.join(output_dir, output_filename)
        cv2.imwrite(output_path, img)
        
        # 构建符合新规范的返回结构
        return {
            "status": "success",
            "message": f"Detected {len(faces)} face(s). Output saved to {output_path}",
            "output_format": "image",
            "data": {
                "image_path": output_path
            }
        }
        
    except Exception as e:
        return {"status": "failed", "message": str(e)}