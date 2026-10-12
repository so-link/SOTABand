"""Run the built-in raw-data pipeline with optional ZIP extraction.

The built-in pipeline intentionally only understands directories containing
``ParameterData/*.csv``. This wrapper keeps that contract while accepting a
ZIP file or a directory containing ZIP archives.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def _safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            name = member.filename.replace("\\", "/")
            if not name or name.startswith("/") or ":" in name.split("/")[0]:
                raise ValueError(f"unsafe ZIP member path: {member.filename}")
            target = (destination / name).resolve()
            if target != destination and destination not in target.parents:
                raise ValueError(f"unsafe ZIP member path: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _copy_without_archives(source: Path, destination: Path) -> None:
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if item.is_symlink() or item.suffix.lower() == ".zip":
            continue
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _prepare_input(source: Path, staging: Path) -> Path:
    staging.mkdir(parents=True, exist_ok=True)
    if source.is_file():
        _safe_extract(source, staging / source.stem)
        return staging

    archives = sorted(item for item in source.rglob("*") if item.is_file() and item.suffix.lower() == ".zip")
    if not archives:
        return source

    _copy_without_archives(source, staging)
    for archive in archives:
        relative_parent = archive.parent.relative_to(source)
        _safe_extract(archive, staging / relative_parent / archive.stem)
    return staging


def _archive_size_bytes(source: Path) -> int:
    archives = [source] if source.is_file() and source.suffix.lower() == ".zip" else (
        [item for item in source.rglob("*") if item.is_file() and item.suffix.lower() == ".zip"]
        if source.is_dir() else []
    )
    total = 0
    for archive_path in archives:
        with zipfile.ZipFile(archive_path) as archive:
            total += sum(member.file_size for member in archive.infolist())
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--staging-root", default="")
    parser.add_argument("--configs", nargs="*", default=["256:30"])
    parser.add_argument("--min-segment-seconds", type=int, default=None)
    parser.add_argument("--spo2-threshold", type=float, default=None)
    parser.add_argument("--hypo-operator", default=None)
    parser.add_argument("--drop-spo2-na-before-segment", default=None)
    parser.add_argument("--spo2-na-break-segment", default=None)
    parser.add_argument("--crop-seconds", type=int, default=None)
    parser.add_argument("--edge-trim-mode", default=None)
    parser.add_argument("--nan-ratio-threshold", type=float, default=None)
    parser.add_argument("--fill-method", default=None)
    parser.add_argument("--apply-physiologic-bounds", default=None)
    parser.add_argument("--pi-threshold", type=float, default=None)
    parser.add_argument("--low-pi-ratio-threshold", type=float, default=None)
    parser.add_argument("--hypoxemia-duration-seconds", type=int, default=None)
    parser.add_argument("--preprocess-hypo-operator", default=None)
    parser.add_argument("--window-hypo-operator", default=None)
    parser.add_argument("--min-hypo-duration-seconds", type=int, default=None)
    parser.add_argument("--max-non-hypo-ratio", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    source = Path(args.source_root).expanduser().resolve()
    output = Path(args.output_root).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"raw source does not exist: {source}")

    archives = [source] if source.is_file() and source.suffix.lower() == ".zip" else (
        [item for item in source.rglob("*") if item.is_file() and item.suffix.lower() == ".zip"]
        if source.is_dir() else []
    )
    if archives:
        staging_parent = Path(args.staging_root).expanduser().resolve() if args.staging_root else None
        if staging_parent:
            staging_parent.mkdir(parents=True, exist_ok=True)
            staging_context = tempfile.TemporaryDirectory(prefix="sotaband-raw-", dir=staging_parent)
        else:
            staging_context = tempfile.TemporaryDirectory(prefix="sotaband-raw-")
        with staging_context as temporary:
            required = _archive_size_bytes(source)
            free = shutil.disk_usage(temporary).free
            if required > free:
                raise OSError(
                    f"insufficient staging disk space: need about {required / 1024**3:.1f} GB, "
                    f"available {free / 1024**3:.1f} GB; set --staging-root to a larger drive"
                )
            input_root = _prepare_input(source, Path(temporary))
            pipeline_args = _pipeline_args(args)
            subprocess.run([
                sys.executable,
                args.pipeline, "all", "--input-root", str(input_root),
                "--output-root", str(output), "--configs", *args.configs,
                *pipeline_args,
            ], check=True)
    else:
        pipeline_args = _pipeline_args(args)
        subprocess.run([
            sys.executable, args.pipeline, "all",
            "--input-root", str(source), "--output-root", str(output),
            "--configs", *args.configs,
            *pipeline_args,
        ], check=True)
    # A successful subprocess is not sufficient: the built-in pipeline can
    # leave tiny placeholder pickle files when no records were discovered.
    # Fail the durable task instead of exposing a misleading zero-window
    # dataset to the next workflow step.
    for config in args.configs:
        lookback, interval = config.split(":", 1)
        window_dir = output / f"balanced_windows_{int(lookback)}s_{int(interval)}s_all"
        stats_path = window_dir / "window_stats.json"
        feature_path = window_dir / "window_features.pkl"
        info_path = window_dir / "window_info.pkl"
        if not stats_path.is_file() or not feature_path.is_file() or not info_path.is_file():
            raise RuntimeError(f"pipeline did not produce a complete window dataset: {window_dir}")
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            total_windows = int(stats.get("total_windows", 0))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid window statistics: {stats_path}") from exc
        if total_windows <= 0 or feature_path.stat().st_size <= 16 or info_path.stat().st_size <= 16:
            raise RuntimeError(f"pipeline produced an empty window dataset: {window_dir}")
    return 0


def _pipeline_args(args: argparse.Namespace) -> list[str]:
    """Forward optional raw pipeline settings while omitting unspecified values."""
    names = (
        "min_segment_seconds", "spo2_threshold", "hypo_operator",
        "drop_spo2_na_before_segment", "spo2_na_break_segment", "crop_seconds",
        "edge_trim_mode", "nan_ratio_threshold", "fill_method",
        "apply_physiologic_bounds", "pi_threshold", "low_pi_ratio_threshold",
        "hypoxemia_duration_seconds", "preprocess_hypo_operator",
        "window_hypo_operator", "min_hypo_duration_seconds", "max_non_hypo_ratio", "seed",
    )
    result: list[str] = []
    for name in names:
        value = getattr(args, name, None)
        if value is not None:
            result.extend([f"--{name.replace('_', '-')}", str(value).lower() if isinstance(value, bool) else str(value)])
    return result


if __name__ == "__main__":
    raise SystemExit(main())
