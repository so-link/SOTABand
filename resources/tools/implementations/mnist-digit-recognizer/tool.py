import os
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import transforms
from PIL import Image
from typing import Any, Dict


class MNISTNet(nn.Module):
    """A simple convolutional neural network for MNIST digit recognition."""
    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv2d(1, 32, kernel_size=3, stride=1, padding=1)
        self.conv2 = nn.Conv2d(32, 64, kernel_size=3, stride=1, padding=1)
        self.pool = nn.MaxPool2d(2, 2)
        self.dropout1 = nn.Dropout2d(0.25)
        self.dropout2 = nn.Dropout2d(0.5)
        self.fc1 = nn.Linear(64 * 7 * 7, 128)
        self.fc2 = nn.Linear(128, 10)

    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))
        x = self.pool(F.relu(self.conv2(x)))
        x = self.dropout1(x)
        x = torch.flatten(x, 1)
        x = F.relu(self.fc1(x))
        x = self.dropout2(x)
        x = self.fc2(x)
        return F.log_softmax(x, dim=1)


def execute(**kwargs) -> Dict[str, Any]:
    """
    Recognizes a handwritten digit from an image using a pre-trained MNIST model.

    Args:
        image_path (str): Path to the input image file (PNG, JPG, etc.).
        model_path (str, optional): Path to the pre-trained model weights file.
            Defaults to "./mnist_model.pth".

    Returns:
        dict: A dictionary containing:
            - status (str): "success" or "failed"
            - message (str): Status description
            - data (dict): Recognized digit information (digit and confidence)
                           On failure, data is empty.
    """
    result: Dict[str, Any] = {"status": "failed", "message": "", "data": {}}

    try:
        # Validate required parameters
        image_path = kwargs.get("image_path")
        if not image_path:
            raise ValueError("Missing required parameter: image_path")

        model_path = kwargs.get("model_path", "./mnist_model.pth")

        # Check if image exists
        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"Image file not found: {image_path}")

        # Check if model exists
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"Model file not found: {model_path}")

        # Load and preprocess the image
        transform = transforms.Compose([
            transforms.Grayscale(num_output_channels=1),
            transforms.Resize((28, 28)),
            transforms.ToTensor(),
            transforms.Normalize((0.1307,), (0.3081,))
        ])

        try:
            img = Image.open(image_path)
        except Exception as e:
            raise IOError(f"Failed to open image: {e}")

        img_tensor = transform(img).unsqueeze(0)  # add batch dimension

        # Load model
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = MNISTNet().to(device)
        try:
            model.load_state_dict(torch.load(model_path, map_location=device))
        except Exception as e:
            raise RuntimeError(f"Failed to load model weights: {e}")

        model.eval()

        # Inference
        with torch.no_grad():
            img_tensor = img_tensor.to(device)
            output = model(img_tensor)
            probabilities = torch.exp(output)  # convert log probabilities to probabilities
            predicted_class = torch.argmax(probabilities, dim=1).item()
            confidence = probabilities[0, predicted_class].item()

        result["status"] = "success"
        result["message"] = "Digit recognized successfully"
        result["data"] = {
            "digit": predicted_class,
            "confidence": round(confidence, 4)
        }

    except Exception as e:
        result["status"] = "failed"
        result["message"] = str(e)
        result["data"] = {}

    return result