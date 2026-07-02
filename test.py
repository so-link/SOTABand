"""
姿态提取工具 - 基于OpenCV轻量级模型
使用OpenCV自带的COCO轻量级姿态估计模型
版本: 0.1.0
"""

import os
import sys
import cv2
import numpy as np
import urllib.request
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

# ==================== 配置区域 ====================
MODEL_DIR = Path(os.path.expanduser("~/.pose_extraction/models"))
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# OpenPose轻量级模型配置（文件更小，下载更快）
PROTO_URL = "https://raw.githubusercontent.com/opencv/opencv_extra/master/testdata/dnn/openpose_pose_coco.prototxt"
WEIGHTS_URL = "http://posefs1.perception.cs.cmu.edu/OpenPose/models/pose/coco/pose_iter_440000.caffemodel"

# 关键点索引
COCO_PAIRS = [
    (1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7),  # 手臂
    (1, 8), (8, 9), (9, 10), (1, 11), (11, 12), (12, 13),  # 躯干和腿
    (1, 0), (0, 14), (14, 16), (0, 15), (15, 17)  # 头部
]

COCO_POINTS = [
    "nose", "neck", "Rsho", "Relb", "Rwri", "Lsho",
    "Lelb", "Lwri", "Rhip", "Rkne", "Rank", "Lhip",
    "Lkne", "Lank", "Reye", "Leye", "Rear", "Lear"
]

# ==================== 模型下载（带重试机制） ====================

def download_with_retry(url: str, dest_path: Path, max_retries: int = 3) -> bool:
    """
    带重试机制的文件下载

    Args:
        url: 下载URL
        dest_path: 目标路径
        max_retries: 最大重试次数

    Returns:
        bool: 下载是否成功
    """
    for attempt in range(max_retries):
        try:
            print(f"尝试下载 ({attempt + 1}/{max_retries}): {url}")

            # 设置超时和请求头
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0'}
            )

            with urllib.request.urlopen(req, timeout=60) as response:
                with open(dest_path, 'wb') as out_file:
                    out_file.write(response.read())

            print(f"✓ 下载成功: {dest_path.name}")
            return True

        except urllib.error.URLError as e:
            print(f"✗ 下载失败 (尝试 {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                print("等待3秒后重试...")
                import time
                time.sleep(3)
            else:
                print("所有重试均失败")
                return False
        except Exception as e:
            print(f"✗ 下载异常: {e}")
            return False

    return False


def download_models() -> bool:
    """
    下载所有需要的模型文件

    Returns:
        bool: 下载是否成功
    """
    proto_path = MODEL_DIR / "pose_deploy.prototxt"
    weights_path = MODEL_DIR / "pose_iter_440000.caffemodel"

    # 检查文件是否已存在
    if proto_path.exists() and weights_path.exists():
        print("✓ 模型文件已存在，跳过下载")
        return True

    # 下载 prototxt 文件（较小）
    if not proto_path.exists():
        print("正在下载模型配置文件...")
        if not download_with_retry(PROTO_URL, proto_path):
            return False

    # 下载权重文件（约200MB）
    if not weights_path.exists():
        print("正在下载模型权重文件（约200MB，请耐心等待）...")
        if not download_with_retry(WEIGHTS_URL, weights_path):
            return False

    return True


# ==================== 模型加载 ====================

def load_pose_model(conf_threshold: float = 0.5) -> Tuple[cv2.dnn_Net, float]:
    """
    加载姿态估计模型

    Args:
        conf_threshold: 置信度阈值

    Returns:
        Tuple[net, threshold]: (模型对象, 置信度阈值)
    """
    # 检查并下载模型
    if not download_models():
        raise RuntimeError("模型下载失败，请检查网络连接或手动下载模型文件")

    proto_path = MODEL_DIR / "pose_deploy.prototxt"
    weights_path = MODEL_DIR / "pose_iter_440000.caffemodel"

    # 加载模型
    try:
        net = cv2.dnn.readNetFromCaffe(str(proto_path), str(weights_path))
        # 如果可用，使用GPU加速
        # net.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
        # net.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA)
        return net, conf_threshold
    except Exception as e:
        raise RuntimeError(f"模型加载失败: {e}")


# ==================== 姿态提取 ====================

def get_keypoints(net: cv2.dnn_Net, image: np.ndarray, threshold: float) -> Tuple[np.ndarray, List[Tuple[int, int, float]]]:
    """
    提取图像中的关键点

    Args:
        net: DNN模型
        image: 输入图像
        threshold: 置信度阈值

    Returns:
        Tuple: (处理后的图像, 关键点列表)
    """
    # 图像预处理
    in_width = 368
    in_height = 368
    inp_blob = cv2.dnn.blobFromImage(image, 1.0 / 255, (in_width, in_height),
                                     (0, 0, 0), swapRB=False, crop=False)
    net.setInput(inp_blob)

    # 前向推理
    output = net.forward()

    # 解析关键点
    h, w = image.shape[:2]
    points = []

    for i in range(len(COCO_POINTS)):
        # 获取置信度图
        prob_map = output[0, i, :, :]
        min_val, prob, min_loc, point = cv2.minMaxLoc(prob_map)

        # 转换到原始图像坐标
        x = int(point[0] * w / in_width)
        y = int(point[1] * h / in_height)

        if prob > threshold:
            points.append((x, y, prob))
        else:
            points.append((0, 0, 0))

    return image, points


def draw_pose(image: np.ndarray, points: List[Tuple[int, int, float]]) -> np.ndarray:
    """
    在图像上绘制姿态骨架

    Args:
        image: 输入图像
        points: 关键点列表 [(x, y, confidence), ...]

    Returns:
        np.ndarray: 绘制了骨架的图像
    """
    img_copy = image.copy()
    h, w = img_copy.shape[:2]

    # 绘制关键点
    for i, (x, y, prob) in enumerate(points):
        if prob > 0:
            # 根据置信度设置颜色和大小
            radius = 3 if prob < 0.5 else 5
            color = (0, 255, 255)  # 黄色
            cv2.circle(img_copy, (x, y), radius, color, -1)

            # 显示置信度
            cv2.putText(img_copy, f"{prob:.2f}", (x-10, y-15),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.3, (255, 255, 255), 1)

    # 绘制骨架连接线
    for pair in COCO_PAIRS:
        idx1, idx2 = pair
        if idx1 < len(points) and idx2 < len(points):
            x1, y1, prob1 = points[idx1]
            x2, y2, prob2 = points[idx2]

            if prob1 > 0 and prob2 > 0:
                # 根据连接部位设置不同颜色
                if idx1 in [0, 14, 15, 16, 17]:  # 头部
                    color = (0, 165, 255)  # 橙色
                elif idx1 in [2, 3, 4, 5, 6, 7]:  # 手臂
                    color = (255, 0, 0)  # 蓝色
                elif idx1 in [8, 9, 10, 11, 12, 13]:  # 腿部
                    color = (0, 0, 255)  # 红色
                else:  # 躯干
                    color = (0, 255, 0)  # 绿色

                cv2.line(img_copy, (x1, y1), (x2, y2), color, 3, cv2.LINE_AA)

    return img_copy


# ==================== 保存结果 ====================

def save_output_image(image: np.ndarray, original_path: str) -> str:
    """
    保存输出图像

    Args:
        image: 处理后的图像
        original_path: 原始图像路径

    Returns:
        str: 输出图像路径
    """
    # 创建输出目录
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)

    # 生成唯一文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    original_name = Path(original_path).stem
    output_path = output_dir / f"pose_{original_name}_{timestamp}.jpg"

    # 保存图像
    cv2.imwrite(str(output_path), image)
    return str(output_path.absolute())


# ==================== 主函数 ====================

def execute(image_path: str, conf_threshold: float = 0.5) -> Dict[str, Any]:
    """
    执行姿态提取主函数

    Args:
        image_path: 输入图像路径
        conf_threshold: 置信度阈值 (0-1)

    Returns:
        Dict: 执行结果
    """
    # 参数校验
    if not isinstance(conf_threshold, (int, float)):
        return {
            "status": "failed",
            "message": "conf_threshold 必须是数值类型",
            "output_format": "image",
            "data": {}
        }

    if conf_threshold < 0 or conf_threshold > 1:
        return {
            "status": "failed",
            "message": "conf_threshold 必须在 0-1 范围内",
            "output_format": "image",
            "data": {}
        }

    try:
        # 1. 检查输入图像
        input_path = Path(image_path)
        if not input_path.exists():
            return {
                "status": "failed",
                "message": f"图像文件不存在: {image_path}",
                "output_format": "image",
                "data": {}
            }

        supported_formats = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp']
        if not input_path.suffix.lower() in supported_formats:
            return {
                "status": "failed",
                "message": f"不支持的图像格式: {input_path.suffix}，支持的格式: {supported_formats}",
                "output_format": "image",
                "data": {}
            }

        # 2. 读取图像
        image = cv2.imread(str(input_path))
        if image is None:
            return {
                "status": "failed",
                "message": f"无法读取图像: {image_path}",
                "output_format": "image",
                "data": {}
            }

        print(f"图像尺寸: {image.shape[1]}x{image.shape[0]}")

        # 3. 加载模型
        print("正在加载模型...")
        net, threshold = load_pose_model(conf_threshold)

        # 4. 提取关键点
        print("正在提取姿态...")
        _, keypoints = get_keypoints(net, image, threshold)

        # 检测有效关键点数量
        valid_points = [p for p in keypoints if p[2] > 0]
        print(f"检测到 {len(valid_points)} 个有效关键点")

        # 显示各部位检测情况
        if len(valid_points) > 0:
            print("检测到的关键点:")
            for i, (x, y, prob) in enumerate(keypoints):
                if prob > 0:
                    print(f"  - {COCO_POINTS[i]}: ({x}, {y}) 置信度: {prob:.3f}")

        # 5. 绘制姿态
        result_image = draw_pose(image, keypoints)

        # 6. 保存结果
        output_path = save_output_image(result_image, image_path)

        # 7. 构建返回结果
        message = "姿态提取成功"
        if len(valid_points) == 0:
            message = "未检测到有效关键点，请检查图像是否包含人物或降低置信度阈值"

        return {
            "status": "success",
            "message": message,
            "output_format": "image",
            "data": {
                "image_path": output_path,
                "keypoints_count": len(valid_points)
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "message": f"执行异常: {str(e)}",
            "output_format": "image",
            "data": {}
        }


# ==================== 命令行接口 ====================

def main():
    """命令行入口"""
    if len(sys.argv) < 2:
        print("使用方法: python pose_extraction.py <image_path> [conf_threshold]")
        print("示例: python pose_extraction.py person.jpg 0.5")
        print("说明: conf_threshold 为置信度阈值，范围 0-1，默认 0.5")
        sys.exit(1)

    image_path = sys.argv[1]
    conf_threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5

    print(f"输入图像: {image_path}")
    print(f"置信度阈值: {conf_threshold}")
    print("-" * 50)

    result = execute(image_path, conf_threshold)

    print("-" * 50)
    print(f"状态: {result['status']}")
    print(f"消息: {result['message']}")
    if result['status'] == 'success':
        print(f"输出路径: {result['data']['image_path']}")
        print(f"关键点数量: {result['data']['keypoints_count']}")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()