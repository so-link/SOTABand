import cv2
import os
from typing import Any, Dict

def execute(**kwargs) -> Dict[str, Any]:
    """
    Perform face detection on an input image, draw bounding boxes around faces,
    save the result, and return the output file path, face count, and face locations.

    Args:
        image_path (str): Local file path to the input image.

    Returns:
        Dict[str, Any]: A dictionary containing:
            - "string": output path (success) or error message (failure)
            - "face_count": number of detected faces (0 on failure)
            - "faces": list of face locations (each as {"x", "y", "width", "height"}) (empty on failure)
    """
    def get_output_path(input_path: str) -> str:
        base, ext = os.path.splitext(input_path)
        return f"{base}_detected{ext}"

    # Validate input
    image_path = kwargs.get("image_path")
    if not image_path or not isinstance(image_path, str):
        error_msg = "Error: Required parameter 'image_path' (string) is missing or invalid."
        return {"string": error_msg, "face_count": 0, "faces": []}
    if not os.path.isfile(image_path):
        error_msg = f"Error: File not found at '{image_path}'."
        return {"string": error_msg, "face_count": 0, "faces": []}

    try:
        # Read image
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"OpenCV could not read the image from '{image_path}'.")

        # Convert to grayscale for face detection
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Load the Haar cascade classifier for frontal face
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)
        if face_cascade.empty():
            raise RuntimeError("Failed to load face cascade classifier. Check OpenCV installation.")

        # Detect faces
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(30, 30)
        )

        # Prepare face count and locations
        face_count = len(faces)
        face_locations = []
        for (x, y, w, h) in faces:
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
            face_locations.append({"x": int(x), "y": int(y), "width": int(w), "height": int(h)})

        # Save the output image
        output_path = get_output_path(image_path)
        success = cv2.imwrite(output_path, img)
        if not success:
            raise RuntimeError(f"Failed to write output image to '{output_path}'.")

        return {
            "string": output_path,
            "face_count": face_count,
            "faces": face_locations
        }

    except Exception as e:
        # Graceful error handling
        return {"string": f"Error during face detection: {str(e)}", "face_count": 0, "faces": []}