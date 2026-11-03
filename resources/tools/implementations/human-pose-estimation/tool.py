"""Human Pose Estimation Tool — mediapipe 0.10.x Task API"""

import os
import cv2
import numpy as np
from typing import Any
from pathlib import Path

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

# 模型：优先用工具目录内随 git 分发的内置副本；缺失时下载到用户目录缓存，
# 不往工具目录写（内置资源只读，复制到系统目录后写进去就是污染）
_tool_dir = _env_path("TOOL_DIR") or (Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd())
_BUNDLED_MODEL = _tool_dir / "models" / "pose_landmarker_lite.task"
MODEL_PATH = _BUNDLED_MODEL if _BUNDLED_MODEL.exists() else _USER_HOME / "models" / "pose_landmarker_lite.task"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"


def _ensure_model() -> str:
    """确保模型文件存在，不存在则下载到用户目录缓存"""
    if not MODEL_PATH.exists():
        import urllib.request
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return str(MODEL_PATH)


def execute(**kwargs) -> dict[str, Any]:
    if "image_path" not in kwargs:
        return {"status": "failed", "message": "缺少必需参数: image_path", "data": {}}

    image_path = kwargs["image_path"]
    if not os.path.isfile(image_path):
        return {"status": "failed", "message": f"图像文件不存在: {image_path}", "data": {}}

    image = cv2.imread(image_path)
    if image is None:
        return {"status": "failed", "message": f"无法读取图像: {image_path}", "data": {}}

    try:
        import mediapipe as mp
        from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions

        model_path = _ensure_model()
        options = PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=model_path),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_poses=10,
        )

        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)

        with PoseLandmarker.create_from_options(options) as landmarker:
            result = landmarker.detect(mp_image)

        if not result.pose_landmarks:
            return {"status": "failed", "message": "未检测到人体关节", "data": {}}

        joint_names = [
            ("NOSE", 0), ("LEFT_EYE_INNER", 1), ("LEFT_EYE", 2), ("LEFT_EYE_OUTER", 3),
            ("RIGHT_EYE_INNER", 4), ("RIGHT_EYE", 5), ("RIGHT_EYE_OUTER", 6),
            ("LEFT_EAR", 7), ("RIGHT_EAR", 8), ("MOUTH_LEFT", 9), ("MOUTH_RIGHT", 10),
            ("LEFT_SHOULDER", 11), ("RIGHT_SHOULDER", 12),
            ("LEFT_ELBOW", 13), ("RIGHT_ELBOW", 14),
            ("LEFT_WRIST", 15), ("RIGHT_WRIST", 16),
            ("LEFT_PINKY", 17), ("RIGHT_PINKY", 18),
            ("LEFT_INDEX", 19), ("RIGHT_INDEX", 20),
            ("LEFT_THUMB", 21), ("RIGHT_THUMB", 22),
            ("LEFT_HIP", 23), ("RIGHT_HIP", 24),
            ("LEFT_KNEE", 25), ("RIGHT_KNEE", 26),
            ("LEFT_ANKLE", 27), ("RIGHT_ANKLE", 28),
            ("LEFT_HEEL", 29), ("RIGHT_HEEL", 30),
            ("LEFT_FOOT_INDEX", 31), ("RIGHT_FOOT_INDEX", 32),
        ]

        # 骨架连线（MediaPipe Pose 标准连接关系）
        CONNECTIONS = [
            (0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8),
            (9, 10), (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21),
            (17, 19), (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
            (11, 23), (12, 24), (23, 24), (23, 25), (24, 26), (25, 27), (26, 28),
            (27, 29), (28, 30), (29, 31), (30, 32)
        ]

        # 肢体分组：不同的肢体使用不同颜色的连线
        LIMB_GROUPS = {
            "head": [(0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8), (9, 10)],
            "torso": [(11, 12), (11, 23), (12, 24), (23, 24)],
            "left_arm": [(11, 13), (13, 15)],
            "right_arm": [(12, 14), (14, 16)],
            "left_hand": [(15, 17), (15, 19), (15, 21), (17, 19)],
            "right_hand": [(16, 18), (16, 20), (16, 22), (18, 20)],
            "left_leg": [(23, 25), (25, 27), (27, 29), (29, 31)],
            "right_leg": [(24, 26), (26, 28), (28, 30), (30, 32)],
        }
        LIMB_COLORS = {
            "head": (255, 0, 0),        # 蓝色
            "torso": (0, 255, 0),       # 绿色
            "left_arm": (0, 255, 255),  # 黄色
            "right_arm": (0, 165, 255), # 橙色
            "left_hand": (255, 0, 255), # 品红
            "right_hand": (128, 0, 128),# 紫色
            "left_leg": (255, 255, 0),  # 青色
            "right_leg": (0, 128, 128), # 橄榄色
        }

        # 建立连接对到颜色的快速查找表
        conn_color_map = {}
        for group, conns in LIMB_GROUPS.items():
            color = LIMB_COLORS[group]
            for conn in conns:
                conn_color_map[tuple(sorted(conn))] = color

        num_people = len(result.pose_landmarks)
        all_rows = []
        output_img = image.copy()
        h, w = output_img.shape[:2]

        for person_id, landmarks in enumerate(result.pose_landmarks):
            # 收集关节数据
            for name, idx in joint_names:
                lm = landmarks[idx]
                all_rows.append([
                    person_id,
                    name,
                    round(lm.x, 4),
                    round(lm.y, 4),
                    round(lm.visibility, 4),
                ])

            # 绘制关节点 —— 红色粗点
            for name, idx in joint_names:
                lm = landmarks[idx]
                cx, cy = int(lm.x * w), int(lm.y * h)
                cv2.circle(output_img, (cx, cy), 5, (0, 0, 255), -1)

            # 绘制骨架连线 —— 不同肢体不同颜色，线条加粗（更醒目）
            for p1, p2 in CONNECTIONS:
                lm1 = landmarks[p1]
                lm2 = landmarks[p2]
                x1, y1 = int(lm1.x * w), int(lm1.y * h)
                x2, y2 = int(lm2.x * w), int(lm2.y * h)
                conn_key = tuple(sorted((p1, p2)))
                line_color = conn_color_map.get(conn_key, (0, 0, 0))  # 默认黑色
                cv2.line(output_img, (x1, y1), (x2, y2), line_color, 2)  # 原为4，改为30更粗更醒目

        # 保存结果图像到用户数据目录（不写进输入图片所在目录，避免污染数据源）
        result_dir = _out_dir("human-pose-estimation")
        result_path = str(result_dir / f"pose_{os.path.basename(image_path)}")
        cv2.imwrite(result_path, output_img)

        return {
            "status": "success",
            "output_format": "image",
            "message": f"检测到 {num_people} 个人, {len(all_rows)} 个关节",
            "data": {
                "image_path": result_path,
                "columns": ["person_id", "joint", "x", "y", "confidence"],
                "rows": all_rows,
            },
        }

    except ImportError as e:
        return {"status": "failed", "message": f"缺少依赖: {e}", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"检测异常: {str(e)[:200]}", "data": {}}