"""GPU integration experiment for multivariate time-series classification."""

import argparse
import json
import math
import os
import random
import time
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--train-samples", type=int, default=6144)
    parser.add_argument("--val-samples", type=int, default=1536)
    parser.add_argument("--resume")
    return parser.parse_args()


def make_dataset(samples, seed, sequence_length=128, channels=8, classes=6):
    generator = torch.Generator().manual_seed(seed)
    labels = torch.randint(classes, (samples,), generator=generator)
    phase = torch.rand(samples, 1, generator=generator) * (2 * math.pi)
    time_axis = torch.linspace(0, 1, sequence_length).reshape(1, -1)
    frequency = (labels + 1).float().reshape(-1, 1)
    base = 2 * math.pi * frequency * time_axis + phase

    features = torch.empty(samples, sequence_length, channels)
    features[:, :, 0] = torch.sin(base)
    features[:, :, 1] = torch.cos(base)
    features[:, :, 2] = torch.sin(2 * base) * 0.6
    features[:, :, 3] = torch.cos(2 * base) * 0.6
    features[:, :, 4] = torch.sin(base) * torch.cos(2 * math.pi * time_axis)
    features[:, :, 5] = torch.cos(base) * torch.sin(2 * math.pi * time_axis)
    features[:, :, 6] = (labels.float().reshape(-1, 1) / (classes - 1) - 0.5) + 0.2 * torch.sin(base)
    features[:, :, 7] = torch.sin(base + labels.reshape(-1, 1) * 0.25)
    noise = torch.randn(features.shape, generator=generator) * 0.18
    features = features + noise
    return TensorDataset(features, labels)


class TimeSeriesTransformer(nn.Module):
    def __init__(self, channels=8, classes=6, width=96, layers=4, heads=8):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv1d(channels, width, kernel_size=7, stride=2, padding=3),
            nn.BatchNorm1d(width),
            nn.GELU(),
            nn.Conv1d(width, width, kernel_size=5, stride=2, padding=2),
            nn.GELU(),
        )
        self.class_token = nn.Parameter(torch.zeros(1, 1, width))
        self.position = nn.Parameter(torch.randn(1, 33, width) * 0.02)
        block = nn.TransformerEncoderLayer(
            d_model=width,
            nhead=heads,
            dim_feedforward=width * 4,
            dropout=0.1,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(block, num_layers=layers, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(width)
        self.head = nn.Linear(width, classes)

    def forward(self, values):
        tokens = self.stem(values.transpose(1, 2)).transpose(1, 2)
        class_token = self.class_token.expand(values.shape[0], -1, -1)
        tokens = torch.cat((class_token, tokens), dim=1)
        tokens = tokens + self.position[:, : tokens.shape[1]]
        return self.head(self.norm(self.encoder(tokens)[:, 0]))


@torch.inference_mode()
def evaluate(model, loader, device):
    model.eval()
    total_loss = 0.0
    correct = 0
    count = 0
    for values, labels in loader:
        values = values.to(device, non_blocking=True)
        labels = labels.to(device, non_blocking=True)
        with torch.amp.autocast("cuda", dtype=torch.float16):
            logits = model(values)
            loss = nn.functional.cross_entropy(logits, labels)
        total_loss += loss.item() * labels.numel()
        correct += (logits.argmax(dim=1) == labels).sum().item()
        count += labels.numel()
    return total_loss / count, correct / count


def main():
    args = parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required; this experiment has no CPU fallback")

    seed = int(os.environ.get("ACADEMIC_SEED", "42"))
    random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    device = torch.device("cuda")

    train_data = make_dataset(args.train_samples, seed)
    val_data = make_dataset(args.val_samples, seed + 1)
    train_loader = DataLoader(
        train_data, batch_size=args.batch_size, shuffle=True, num_workers=0, pin_memory=True
    )
    val_loader = DataLoader(
        val_data, batch_size=args.batch_size, shuffle=False, num_workers=0, pin_memory=True
    )

    model = TimeSeriesTransformer().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-3, weight_decay=1e-2)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    scaler = torch.amp.GradScaler("cuda")
    output = Path(os.environ.get("ACADEMIC_OUTPUT_DIR", "/output"))
    output.mkdir(parents=True, exist_ok=True)
    start_epoch = 0
    best_accuracy = 0.0
    resumed = False

    if args.resume:
        checkpoint = torch.load(args.resume, map_location=device, weights_only=True)
        model.load_state_dict(checkpoint["model"])
        optimizer.load_state_dict(checkpoint["optimizer"])
        scheduler.load_state_dict(checkpoint["scheduler"])
        scaler.load_state_dict(checkpoint["scaler"])
        start_epoch = checkpoint["epoch"] + 1
        best_accuracy = checkpoint["best_accuracy"]
        resumed = True

    history = []
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    started = time.perf_counter()
    for epoch in range(start_epoch, args.epochs):
        model.train()
        running_loss = 0.0
        seen = 0
        for values, labels in train_loader:
            values = values.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", dtype=torch.float16):
                logits = model(values)
                loss = nn.functional.cross_entropy(logits, labels)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()
            running_loss += loss.item() * labels.numel()
            seen += labels.numel()
        scheduler.step()
        val_loss, accuracy = evaluate(model, val_loader, device)
        best_accuracy = max(best_accuracy, accuracy)
        history.append(
            {
                "epoch": epoch + 1,
                "train_loss": running_loss / seen,
                "val_loss": val_loss,
                "accuracy": accuracy,
                "learning_rate": optimizer.param_groups[0]["lr"],
            }
        )
        print(json.dumps(history[-1]), flush=True)

    torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    val_loss, accuracy = evaluate(model, val_loader, device)
    checkpoint = {
        "epoch": args.epochs - 1,
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict(),
        "scaler": scaler.state_dict(),
        "best_accuracy": max(best_accuracy, accuracy),
    }
    torch.save(checkpoint, output / "last.pt")
    metrics = {
        "accuracy": accuracy,
        "best_accuracy": max(best_accuracy, accuracy),
        "loss": val_loss,
        "parameters": sum(parameter.numel() for parameter in model.parameters()),
        "peak_vram_mib": torch.cuda.max_memory_allocated() / (1024**2),
        "training_seconds": elapsed,
        "samples_per_second": args.train_samples * max(args.epochs - start_epoch, 0) / elapsed,
        "epochs_completed": args.epochs,
        "resumed": resumed,
        "device": torch.cuda.get_device_name(0),
        "pytorch": torch.__version__,
        "history": history,
    }
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics), flush=True)


if __name__ == "__main__":
    main()
