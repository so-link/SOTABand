"""Real CUDA training fixture, not a paper-reproduction benchmark."""

import argparse
import json
import os
from pathlib import Path

import torch

parser = argparse.ArgumentParser()
parser.add_argument("--resume")
args = parser.parse_args()
assert torch.cuda.is_available(), "A CUDA device is required; no CPU fallback"
torch.manual_seed(int(os.environ.get("ACADEMIC_SEED", "42")))
x = torch.linspace(-1, 1, 1024, device="cuda").reshape(-1, 1)
y = 2 * x + 1
model = torch.nn.Linear(1, 1).cuda()
optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
if args.resume:
    checkpoint = torch.load(args.resume, weights_only=True)
    model.load_state_dict(checkpoint["model"])
    optimizer.load_state_dict(checkpoint["optimizer"])
for _ in range(150):
    optimizer.zero_grad()
    loss = torch.nn.functional.mse_loss(model(x), y)
    loss.backward()
    optimizer.step()
torch.cuda.synchronize()
output = Path(os.environ.get("ACADEMIC_OUTPUT_DIR", "/output"))
output.mkdir(parents=True, exist_ok=True)
torch.save(
    {"model": model.state_dict(), "optimizer": optimizer.state_dict()}, output / "checkpoint.pt"
)
(output / "metrics.json").write_text(
    json.dumps(
        {"mse": loss.item(), "device": torch.cuda.get_device_name(0), "resumed": bool(args.resume)}
    )
)
print(json.dumps({"mse": loss.item(), "cuda": True}))
