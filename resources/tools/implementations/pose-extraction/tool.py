import os
import cv2
import mediapipe as mp
from typing import Any

def execute(**kwargs) -> dict[str, Any]:
    # 1. Validate required parameter
    if "image_path" not in kwargs:
        return {"status": "failed", "message": "缺少必需参数: image_path", "data": {}}

    image_path = kwargs["image_path"]
    conf_threshold = kwargs.get("conf_threshold", 0.5)

    # 2. Check file existence
    if not os.path.isfile(image_path):
        return {"status": "failed", "message": f"文件不存在: {image_path}", "data": {}}

    try:
        # 3. Load image with OpenCV
        image = cv2.imread(image_path)
        if image is None:
            return {"status": "failed", "message": "无法读取图片，可能格式不支持", "data": {}}

        # 4. Convert to RGB and run MediaPipe Pose
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mp_pose = mp.solutions.pose
        with mp_pose.Pose(static_image_mode=True,
                          min_detection_confidence=conf_threshold) as pose:
            results = pose.process(image_rgb)

        if not results.pose_landmarks:
            return {"status": "failed", "message": "未检测到任何姿态", "data": {}}

        # 5. Collect landmark information
        rows = []
        for idx, lm in enumerate(results.pose_landmarks.landmark):
            # Visibility can be used as confidence; include landmarks with acceptable confidence
            if lm.visibility < conf_threshold:
                continue
            name = mp_pose.PoseLandmark(idx).name
            rows.append([name, round(lm.x, 4), round(lm.y, 4), round(lm.z, 4), round(lm.visibility, 4)])

        if not rows:
            return {"status": "failed", "message": "所有关键点置信度均低于阈值", "data": {}}

        # 6. Return successful table result
        columns = ["Landmark", "X", "Y", "Z", "Visibility"]
        return {
            "status": "success",
            "output_format": "table",
            "message": "姿态提取成功",
            "data": {
                "columns": columns,
                "rows": rows
            }
        }

    except Exception as e:
        return {"status": "failed", "message": f"处理过程出错: {str(e)}", "data": {}}