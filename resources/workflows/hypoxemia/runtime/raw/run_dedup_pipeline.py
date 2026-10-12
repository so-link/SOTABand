import argparse
import gc
import json
import os
import pickle
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


FEATURES = ["Time", "SpO2", "HR", "PR", "RR", "PI"]
NUMERIC_FEATURES = ["SpO2", "HR", "PR", "RR", "PI"]
PHYSIOLOGIC_BOUNDS = {
    "SpO2": (50.0, 100.0),
    "HR": (20.0, 250.0),
    "PR": (20.0, 250.0),
    "RR": (3.0, 80.0),
    "PI": (0.0, 20.0),
}
DEFAULT_CONFIGS = [
    (60, 30),
    (120, 30),
    (180, 30),
    (180, 60),
    (180, 90),
    (180, 120),
    (180, 180),
    (256, 30),
    (512, 30),
]


COLUMN_VARIANTS = {
    "Time": ["time", "时间"],
    "SpO2": ["spo2", "血氧饱和度", "血氧"],
    "HR": ["hr(bpm)", "hr", "心率"],
    "PR": ["pr(bpm)", "pr", "脉率"],
    "RR": ["rr(rpm)", "rr", "呼吸频率", "呼吸"],
    "PI": ["pi(%)", "pi", "灌注指数", "灌注"],
}


@dataclass
class ShardWriter:
    output_dir: Path
    prefix: str
    max_records: int
    shard_index: int = 0
    buffer: Optional[List[dict]] = None

    def __post_init__(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.buffer = []

    def add(self, record: dict) -> None:
        self.buffer.append(record)
        if len(self.buffer) >= self.max_records:
            self.flush()

    def flush(self) -> Optional[Path]:
        if not self.buffer:
            return None
        path = self.output_dir / f"{self.prefix}_{self.shard_index:05d}.pkl"
        with path.open("wb") as f:
            pickle.dump(self.buffer, f, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"[shard] wrote {len(self.buffer)} records -> {path}")
        self.buffer = []
        self.shard_index += 1
        gc.collect()
        return path


def normalize_col(name: str) -> str:
    return str(name).strip().lower().replace(" ", "")


def is_primary_timeseries_file(path: Path) -> bool:
    name = path.name.lower()
    return "-1s-" in name or name.startswith("parameterdata-1s-")


def match_columns(columns: Sequence[str]) -> Optional[Dict[str, str]]:
    normalized = {col: normalize_col(col) for col in columns}
    matched: Dict[str, str] = {}
    used = set()
    for std_name, variants in COLUMN_VARIANTS.items():
        found = None
        for col, norm in normalized.items():
            if col in used:
                continue
            checks = [normalize_col(v) for v in variants]
            if any(v == norm or v in norm for v in checks):
                found = col
                break
        if found is None:
            return None
        matched[std_name] = found
        used.add(found)
    return matched


def read_csv_with_fallback(path: Path) -> dict:
    encodings = ["utf-8-sig", "utf-8", "gb18030", "gbk", "latin1"]
    last_error = None
    for encoding in encodings:
        try:
            header = pd.read_csv(path, encoding=encoding, nrows=0)
            matched = match_columns(header.columns)
            if matched is None:
                return {
                    "status": "column_mismatch",
                    "path": str(path),
                    "encoding": encoding,
                    "engine": "c",
                    "error_message": None,
                    "columns": [str(col) for col in header.columns],
                    "matched_columns": None,
                    "df": header,
                }
            usecols = list(matched.values())
            df = pd.read_csv(path, encoding=encoding, usecols=usecols, low_memory=False)
            return {
                "status": "ok",
                "path": str(path),
                "encoding": encoding,
                "engine": "c",
                "error_message": None,
                "columns": [str(col) for col in header.columns],
                "matched_columns": matched,
                "df": df,
            }
        except Exception as exc:
            last_error = exc
        try:
            header = pd.read_csv(path, encoding=encoding, nrows=0, engine="python")
            matched = match_columns(header.columns)
            if matched is None:
                return {
                    "status": "column_mismatch",
                    "path": str(path),
                    "encoding": encoding,
                    "engine": "python",
                    "error_message": None,
                    "columns": [str(col) for col in header.columns],
                    "matched_columns": None,
                    "df": header,
                }
            usecols = list(matched.values())
            df = pd.read_csv(path, encoding=encoding, usecols=usecols, engine="python", on_bad_lines="skip")
            print(f"[warn] tolerant read used for {path} with encoding={encoding}; malformed lines were skipped")
            return {
                "status": "tolerant_ok",
                "path": str(path),
                "encoding": encoding,
                "engine": "python",
                "error_message": None,
                "columns": [str(col) for col in header.columns],
                "matched_columns": matched,
                "df": df,
            }
        except Exception as exc:
            last_error = exc
    print(f"[warn] failed to read {path}: {last_error}")
    return {
        "status": "read_failed",
        "path": str(path),
        "encoding": None,
        "engine": None,
        "error_message": repr(last_error) if last_error is not None else "unknown_read_error",
        "columns": None,
        "matched_columns": None,
        "df": None,
    }


def find_parameter_folders(root: Path) -> List[Path]:
    folders = []
    for dirpath, dirnames, _ in os.walk(root):
        if "ParameterData" in dirnames:
            folders.append(Path(dirpath))
            dirnames[:] = []
    return sorted(folders)


def is_machine_id(part: str) -> bool:
    return "___" in str(part)


def normalize_machine_id(part: str) -> str:
    text = str(part)
    if "___" not in text:
        return text
    return "___" + text.split("___", 1)[1]


def build_folder_meta(patient_folder: Path, input_root: Path) -> dict:
    try:
        rel = patient_folder.relative_to(input_root)
        parts = rel.parts
    except ValueError:
        rel = patient_folder
        parts = patient_folder.parts
    normalized_parts = list(parts)
    if normalized_parts and normalized_parts[0].lower() == "extracted":
        normalized_parts = normalized_parts[1:]
    if len(normalized_parts) >= 2 and normalized_parts[0] == normalized_parts[1]:
        normalized_parts = normalized_parts[1:]

    machine_idx = next((idx for idx, part in enumerate(normalized_parts) if is_machine_id(part)), None)
    if machine_idx is not None:
        batch_folder = normalized_parts[0] if machine_idx > 0 else "unknown_batch"
        raw_machine_id = normalized_parts[machine_idx]
        machine_id = normalize_machine_id(raw_machine_id)
    else:
        batch_folder = normalized_parts[0] if len(normalized_parts) >= 1 else "unknown_batch"
        raw_machine_id = normalized_parts[1] if len(normalized_parts) >= 2 else "unknown_machine"
        machine_id = normalize_machine_id(raw_machine_id)

    return {
        "relative_patient_folder": str(rel),
        "batch_folder": batch_folder,
        "raw_machine_id": raw_machine_id,
        "machine_id": machine_id,
        "ventilator_id": machine_id,
        "patient_folder": str(patient_folder),
    }


def clear_matching_files(path: Path, pattern: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for old_file in path.glob(pattern):
        if old_file.is_file():
            old_file.unlink()


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value}")


def unique_preserve_order(values: Sequence[object]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value)
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


def make_counter_dict() -> dict:
    return defaultdict(int)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def group_folders_by_machine(patient_folders: Sequence[Path], input_root: Path) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = {}
    for patient_folder in patient_folders:
        folder_meta = build_folder_meta(patient_folder, input_root)
        grouped.setdefault(folder_meta["machine_id"], []).append(folder_meta)
    for folder_metas in grouped.values():
        folder_metas.sort(key=lambda item: item["relative_patient_folder"])
    return dict(sorted(grouped.items(), key=lambda item: item[0]))


def load_machine_csvs(folder_metas: Sequence[dict]) -> Tuple[pd.DataFrame, dict]:
    frames = []
    stats = {
        "parameter_folder_count": len(folder_metas),
        "csv_total": 0,
        "csv_used": 0,
        "csv_skipped": 0,
        "csv_tolerant_used": 0,
        "csv_column_mismatch": 0,
        "csv_read_failed": 0,
        "rows_before_time_filter": 0,
        "rows_after_time_filter": 0,
        "duplicate_time_rows_removed": 0,
        "source_files": [],
        "source_patient_folders": [meta["patient_folder"] for meta in folder_metas],
        "relative_patient_folders": [meta["relative_patient_folder"] for meta in folder_metas],
        "batch_folders": sorted({meta["batch_folder"] for meta in folder_metas}),
        "raw_machine_ids": sorted({meta["raw_machine_id"] for meta in folder_metas}),
        "skipped_files": [],
        "tolerant_read_files": [],
    }
    source_order = 0
    for folder_meta in folder_metas:
        parameter_dir = Path(folder_meta["patient_folder"]) / "ParameterData"
        csv_files = sorted(p for p in parameter_dir.glob("*.csv") if p.is_file())
        stats["csv_total"] += len(csv_files)
        for csv_path in csv_files:
            read_result = read_csv_with_fallback(csv_path)
            raw = read_result["df"]
            common_item = {
                "machine_id": folder_meta["machine_id"],
                "batch_folder": folder_meta["batch_folder"],
                "raw_machine_id": folder_meta["raw_machine_id"],
                "patient_folder": folder_meta["patient_folder"],
                "relative_patient_folder": folder_meta["relative_patient_folder"],
                "csv_path": str(csv_path),
                "encoding": read_result["encoding"],
                "engine": read_result["engine"],
            }
            if read_result["status"] == "read_failed":
                stats["csv_skipped"] += 1
                stats["csv_read_failed"] += 1
                stats["skipped_files"].append(
                    {
                        **common_item,
                        "skip_reason": "read_failed",
                        "error_message": read_result["error_message"],
                        "columns": "",
                    }
                )
                source_order += 1
                continue
            if read_result["status"] == "column_mismatch":
                stats["csv_skipped"] += 1
                stats["csv_column_mismatch"] += 1
                stats["skipped_files"].append(
                    {
                        **common_item,
                        "skip_reason": "column_mismatch",
                        "error_message": None,
                        "columns": "|".join(read_result["columns"] or []),
                    }
                )
                source_order += 1
                continue
            matched = read_result["matched_columns"]
            if matched is None:
                stats["csv_skipped"] += 1
                stats["csv_column_mismatch"] += 1
                stats["skipped_files"].append(
                    {
                        **common_item,
                        "skip_reason": "column_mismatch",
                        "error_message": None,
                        "columns": "|".join(read_result["columns"] or []),
                    }
                )
                source_order += 1
                continue
            if read_result["status"] == "tolerant_ok":
                stats["csv_tolerant_used"] += 1
                stats["tolerant_read_files"].append(
                    {
                        **common_item,
                        "tolerant_reason": "python_engine_on_bad_lines_skip",
                        "matched_columns": json.dumps(matched, ensure_ascii=False),
                    }
                )

            df = raw.rename(columns={src: dst for dst, src in matched.items()})[FEATURES].copy()
            df.replace(["--", "", " "], np.nan, inplace=True)
            df["Time"] = pd.to_datetime(df["Time"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
            stats["rows_before_time_filter"] += len(df)
            df = df.dropna(subset=["Time"])
            stats["rows_after_time_filter"] += len(df)
            for col in NUMERIC_FEATURES:
                df[col] = pd.to_numeric(df[col], errors="coerce", downcast="float")
            df["_source_order"] = source_order
            df["_row_order"] = np.arange(len(df), dtype=np.int64)
            df["_source_file"] = str(csv_path)
            df["_valid_count"] = df[NUMERIC_FEATURES].notna().sum(axis=1)
            df["_spo2_present"] = df["SpO2"].notna().astype(np.int8)
            df["_pi_present"] = df["PI"].notna().astype(np.int8)
            df["_is_primary_1s"] = int(is_primary_timeseries_file(csv_path))
            frames.append(df)
            stats["csv_used"] += 1
            stats["source_files"].append(str(csv_path))
            source_order += 1

    if not frames:
        return pd.DataFrame(columns=FEATURES), stats

    merged = pd.concat(frames, ignore_index=True)
    before = len(merged)
    merged = merged.sort_values(
        ["Time", "_spo2_present", "_pi_present", "_valid_count", "_is_primary_1s", "_source_order", "_row_order"]
    )
    merged = merged.drop_duplicates(subset=["Time"], keep="last")
    stats["duplicate_time_rows_removed"] = before - len(merged)
    merged = merged.sort_values("Time").reset_index(drop=True)
    return merged, stats


def hypo_mask(spo2: pd.Series, threshold: float, operator: str) -> pd.Series:
    if operator == "lt":
        return spo2 < threshold
    return spo2 <= threshold


def split_continuous_segments(
    df: pd.DataFrame,
    min_duration_seconds: int,
    drop_spo2_na_before_segment: bool,
    spo2_na_break_segment: bool,
) -> Tuple[List[dict], dict]:
    if df.empty:
        return [], {
            "rows_after_dedup": 0,
            "spo2_nonnull_rows_after_dedup": 0,
            "rows_after_spo2_policy": 0,
            "candidate_segments_before_length_filter": 0,
            "dropped_segments_too_short": 0,
            "kept_segments": 0,
            "drop_reason_counts": {},
            "drop_records": [],
        }
    df = df.sort_values("Time").reset_index(drop=True)
    stats = {
        "rows_after_dedup": int(len(df)),
        "spo2_nonnull_rows_after_dedup": int(df["SpO2"].notna().sum()),
        "rows_after_spo2_policy": 0,
        "candidate_segments_before_length_filter": 0,
        "dropped_segments_too_short": 0,
        "kept_segments": 0,
        "drop_reason_counts": make_counter_dict(),
        "drop_records": [],
    }

    work = df.copy()
    if drop_spo2_na_before_segment:
        work = work[work["SpO2"].notna()].copy()
    stats["rows_after_spo2_policy"] = int(len(work))
    if work.empty:
        stats["drop_reason_counts"] = dict(stats["drop_reason_counts"])
        return [], stats

    time_diff = work["Time"].diff().dt.total_seconds()
    if drop_spo2_na_before_segment or not spo2_na_break_segment:
        new_group = time_diff.ne(1).fillna(True)
        group_ids = new_group.cumsum()
        work = work.assign(_segment_id=group_ids)
        grouped = work.groupby("_segment_id", sort=True)
    else:
        valid_spo2 = work["SpO2"].notna()
        new_group = valid_spo2 & ((~valid_spo2.shift(fill_value=False)) | (time_diff != 1))
        group_ids = new_group.cumsum()
        work = work.assign(_segment_id=np.where(valid_spo2, group_ids, 0))
        grouped = work[work["_segment_id"] > 0].groupby("_segment_id", sort=True)

    segments: List[dict] = []
    for candidate_idx, (_, seg) in enumerate(grouped, start=1):
        stats["candidate_segments_before_length_filter"] += 1
        duration = (seg["Time"].iloc[-1] - seg["Time"].iloc[0]).total_seconds()
        source_files = unique_preserve_order(seg["_source_file"].tolist()) if "_source_file" in seg.columns else []
        candidate_meta = {
            "candidate_segment_index": candidate_idx,
            "start_time": seg["Time"].iloc[0].strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": seg["Time"].iloc[-1].strftime("%Y-%m-%d %H:%M:%S"),
            "duration_seconds": float(duration),
            "rows": int(len(seg)),
            "source_file_count": len(source_files),
            "source_files_in_segment": source_files,
        }
        if duration > min_duration_seconds:
            stats["kept_segments"] += 1
            segments.append({"df": seg.copy(), "meta": candidate_meta})
            continue
        stats["dropped_segments_too_short"] += 1
        stats["drop_reason_counts"]["segment_too_short"] += 1
        stats["drop_records"].append(
            {
                "drop_stage": "raw_segment",
                "drop_reason": "segment_too_short",
                **candidate_meta,
            }
        )
    stats["drop_reason_counts"] = dict(stats["drop_reason_counts"])
    return segments, stats


def format_times_for_pickle(df: pd.DataFrame) -> np.ndarray:
    out = df.copy()
    out["Time"] = out["Time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return out[FEATURES].to_numpy(dtype=object)


def raw_extract(args: argparse.Namespace) -> None:
    input_root = Path(args.input_root).resolve()
    output_root = Path(args.output_root).resolve()
    raw_dir = output_root / "raw_segments_shards"
    report_dir = output_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    clear_matching_files(raw_dir, "raw_segments_*.pkl")
    writer = ShardWriter(raw_dir, "raw_segments", args.shard_size)

    patient_folders = find_parameter_folders(input_root)
    folders_by_machine = group_folders_by_machine(patient_folders, input_root)
    print(
        f"[raw] found {len(patient_folders)} folders containing ParameterData "
        f"for {len(folders_by_machine)} machines"
    )
    total_segments = 0
    machine_reports = []
    raw_drop_records = []
    skipped_files = []
    tolerant_read_files = []

    for machine_idx, (machine_id, folder_metas) in enumerate(folders_by_machine.items(), start=1):
        merged, stats = load_machine_csvs(folder_metas)
        segments, segment_stats = split_continuous_segments(
            merged,
            args.min_segment_seconds,
            drop_spo2_na_before_segment=args.drop_spo2_na_before_segment,
            spo2_na_break_segment=args.spo2_na_break_segment,
        )
        print(
            f"[raw] machine {machine_idx}/{len(folders_by_machine)} {machine_id} "
            f"folders={len(folder_metas)} "
            f"csv_used={stats['csv_used']} rows={len(merged)} dup_removed={stats['duplicate_time_rows_removed']} "
            f"segments={len(segments)}"
        )

        first_meta = folder_metas[0] if folder_metas else {}
        for local_seg_idx, segment_item in enumerate(segments, start=1):
            seg_df = segment_item["df"]
            segment_meta = segment_item["meta"]
            group_id = f"{machine_id}::seg{local_seg_idx:04d}"
            arr = format_times_for_pickle(seg_df[FEATURES].copy())
            spo2 = pd.to_numeric(seg_df["SpO2"], errors="coerce")
            has_any_hypo = int(hypo_mask(spo2, args.spo2_threshold, args.hypo_operator).any())
            info = {
                "relative_patient_folder": first_meta.get("relative_patient_folder", ""),
                "relative_patient_folders": stats["relative_patient_folders"],
                "batch_folder": first_meta.get("batch_folder", "unknown_batch"),
                "batch_folders": stats["batch_folders"],
                "raw_machine_ids": stats["raw_machine_ids"],
                "machine_id": machine_id,
                "ventilator_id": machine_id,
                "patient_folder": first_meta.get("patient_folder", ""),
                "patient_folders": stats["source_patient_folders"],
                "group_id": group_id,
                "raw_segment_id": group_id,
                "raw_segment_candidate_index": int(segment_meta["candidate_segment_index"]),
                "patient_id": local_seg_idx,
                "time_steps": int(arr.shape[0]),
                "features": FEATURES,
                "start_time": str(arr[0, 0]),
                "end_time": str(arr[-1, 0]),
                "has_hypoxemia_raw_any": has_any_hypo,
                "source_files": stats["source_files"],
                "source_file_count": len(stats["source_files"]),
                "source_files_in_segment": segment_meta["source_files_in_segment"],
                "source_file_count_in_segment": int(segment_meta["source_file_count"]),
                "raw_segment_rows": int(segment_meta["rows"]),
                "raw_segment_duration_seconds": float(segment_meta["duration_seconds"]),
                "dedup_scope": "machine",
                "dedup_duplicate_time_rows_removed_in_machine": stats["duplicate_time_rows_removed"],
            }
            writer.add({"data": arr, "info": info})
            total_segments += 1
        for drop_record in segment_stats["drop_records"]:
            raw_drop_records.append({"machine_id": machine_id, **drop_record})
        skipped_files.extend(stats["skipped_files"])
        tolerant_read_files.extend(stats["tolerant_read_files"])

        machine_reports.append(
            {
                "machine_id": machine_id,
                "ventilator_id": machine_id,
                "parameter_folder_count": stats["parameter_folder_count"],
                "csv_total": stats["csv_total"],
                "csv_used": stats["csv_used"],
                "csv_skipped": stats["csv_skipped"],
                "csv_tolerant_used": stats["csv_tolerant_used"],
                "csv_column_mismatch": stats["csv_column_mismatch"],
                "csv_read_failed": stats["csv_read_failed"],
                "rows_before_time_filter": stats["rows_before_time_filter"],
                "rows_after_time_filter": stats["rows_after_time_filter"],
                "time_parse_nat_rows": stats["rows_before_time_filter"] - stats["rows_after_time_filter"],
                "duplicate_time_rows_removed": stats["duplicate_time_rows_removed"],
                "rows_after_dedup": len(merged),
                "spo2_nonnull_rows_after_dedup": segment_stats["spo2_nonnull_rows_after_dedup"],
                "rows_after_spo2_policy": segment_stats["rows_after_spo2_policy"],
                "candidate_segments_before_length_filter": segment_stats["candidate_segments_before_length_filter"],
                "dropped_segments_too_short": segment_stats["dropped_segments_too_short"],
                "segments": len(segments),
                "kept_segments": segment_stats["kept_segments"],
                "drop_spo2_na_before_segment": bool(args.drop_spo2_na_before_segment),
                "spo2_na_break_segment": bool(args.spo2_na_break_segment),
                "batch_folders": "|".join(stats["batch_folders"]),
                "raw_machine_ids": "|".join(stats["raw_machine_ids"]),
                "relative_patient_folders": "|".join(stats["relative_patient_folders"]),
                "source_file_count": len(stats["source_files"]),
            }
        )
        del merged, segments
        gc.collect()

    writer.flush()
    summary = {
        "input_root": str(input_root),
        "output_root": str(output_root),
        "parameter_folders": len(patient_folders),
        "machines": len(folders_by_machine),
        "raw_segments": total_segments,
        "dedup_scope": "machine",
        "drop_spo2_na_before_segment": bool(args.drop_spo2_na_before_segment),
        "spo2_na_break_segment": bool(args.spo2_na_break_segment),
        "segmentation_policy_explainer": {
            "current_default": "drop_spo2_na_before_segment=false, spo2_na_break_segment=true",
            "alternatives": [
                "false/false: keep SpO2 NaN rows as ordinary missing values",
                "true/false: drop SpO2 NaN rows before segmentation, then segment by remaining timestamps",
                "true/true: same as true/false in current implementation because dropped rows cannot break segments twice",
            ],
        },
        "total_duplicate_time_rows_removed": int(sum(r["duplicate_time_rows_removed"] for r in machine_reports)),
        "total_time_parse_nat_rows": int(sum(r["time_parse_nat_rows"] for r in machine_reports)),
        "total_spo2_nonnull_rows_after_dedup": int(sum(r["spo2_nonnull_rows_after_dedup"] for r in machine_reports)),
        "total_candidate_segments_before_length_filter": int(sum(r["candidate_segments_before_length_filter"] for r in machine_reports)),
        "total_dropped_segments_too_short": int(sum(r["dropped_segments_too_short"] for r in machine_reports)),
        "total_csv_skipped": int(sum(r["csv_skipped"] for r in machine_reports)),
        "total_csv_tolerant_used": int(sum(r["csv_tolerant_used"] for r in machine_reports)),
        "total_csv_column_mismatch": int(sum(r["csv_column_mismatch"] for r in machine_reports)),
        "total_csv_read_failed": int(sum(r["csv_read_failed"] for r in machine_reports)),
    }
    write_json(report_dir / "raw_extract_summary.json", summary)
    write_csv(report_dir / "raw_extract_machine_report.csv", machine_reports)
    write_csv(report_dir / "raw_extract_drop_records.csv", raw_drop_records)
    write_csv(report_dir / "raw_extract_skipped_files.csv", skipped_files)
    write_csv(report_dir / "raw_extract_tolerant_read_files.csv", tolerant_read_files)
    print(f"[raw] done: {summary}")


def iter_shard_records(shard_dir: Path, pattern: str) -> Iterable[dict]:
    for path in sorted(shard_dir.glob(pattern)):
        with path.open("rb") as f:
            records = pickle.load(f)
        for record in records:
            yield record
        del records
        gc.collect()


def crop_time_start_end(df: pd.DataFrame, crop_seconds: int) -> pd.DataFrame:
    if df.empty:
        return df
    start = df["Time"].iloc[0] + timedelta(seconds=crop_seconds)
    end = df["Time"].iloc[-1] - timedelta(seconds=crop_seconds)
    if start >= end:
        return df.iloc[0:0].copy()
    return df[(df["Time"] >= start) & (df["Time"] <= end)].copy()


def trim_edges_like_zhang(df: pd.DataFrame, spo2_threshold: float) -> pd.DataFrame:
    if df.empty:
        return df
    spo2 = pd.to_numeric(df["SpO2"], errors="coerce")
    valid_idx = np.flatnonzero(spo2 >= spo2_threshold)
    if len(valid_idx) == 0:
        return df.iloc[0:0].copy()
    start_idx = int(valid_idx[0])
    end_idx = int(valid_idx[-1])
    return df.iloc[start_idx : end_idx + 1].copy()


def label_hypoxemia(df: pd.DataFrame, threshold: float, duration_seconds: int, operator: str) -> int:
    if df.empty:
        return 0
    is_hypo = hypo_mask(df["SpO2"], threshold, operator).fillna(False)
    time_diff = df["Time"].diff().dt.total_seconds()
    new_group = is_hypo & ((~is_hypo.shift(fill_value=False)) | (time_diff != 1))
    group_ids = new_group.cumsum()
    work = df.assign(_hypo=is_hypo, _hypo_group=np.where(is_hypo, group_ids, 0))
    for _, seg in work[work["_hypo_group"] > 0].groupby("_hypo_group", sort=True):
        duration = (seg["Time"].iloc[-1] - seg["Time"].iloc[0]).total_seconds()
        if duration >= duration_seconds:
            return 1
    return 0


def apply_physiologic_bounds(df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
    clipped = df.copy()
    clip_counts: dict[str, int] = {}
    observed_counts: dict[str, int] = {}
    clip_ratios: dict[str, float] = {}
    total_clipped = 0
    total_observed = 0
    for feature_name, (lower, upper) in PHYSIOLOGIC_BOUNDS.items():
        series = pd.to_numeric(clipped[feature_name], errors="coerce")
        observed = int(series.notna().sum())
        mask = series.notna() & ((series < lower) | (series > upper))
        count = int(mask.sum())
        if count:
            clipped.loc[mask, feature_name] = np.nan
        observed_counts[feature_name] = observed
        clip_counts[feature_name] = count
        clip_ratios[feature_name] = float(count / observed) if observed > 0 else 0.0
        total_clipped += count
        total_observed += observed
    return clipped, {
        "counts": clip_counts,
        "observed_counts": observed_counts,
        "ratios": clip_ratios,
        "total_count": int(total_clipped),
        "total_observed": int(total_observed),
        "total_ratio": float(total_clipped / total_observed) if total_observed > 0 else 0.0,
    }


def compute_segment_quality_score(
    *,
    nan_ratio_before_fill: float,
    low_pi_ratio: float,
    physiologic_clip_ratio: float,
    remaining_nan_ratio_after_fill: float,
) -> Tuple[float, dict]:
    nan_penalty = min(1.0, nan_ratio_before_fill / 0.10)
    pi_penalty = min(1.0, low_pi_ratio / 0.30)
    clip_penalty = min(1.0, physiologic_clip_ratio / 0.02)
    remain_penalty = min(1.0, remaining_nan_ratio_after_fill / 0.02)
    score = 1.0 - (
        0.35 * nan_penalty
        + 0.30 * pi_penalty
        + 0.20 * clip_penalty
        + 0.15 * remain_penalty
    )
    score = float(np.clip(score, 0.0, 1.0))
    return score, {
        "nan_penalty": float(nan_penalty),
        "pi_penalty": float(pi_penalty),
        "clip_penalty": float(clip_penalty),
        "remaining_nan_penalty": float(remain_penalty),
    }


def finalize_preprocess_drop(record: dict, df: pd.DataFrame, reason: str, extra: Optional[dict] = None) -> dict:
    info = dict(record["info"])
    raw_segment_id = info.get("raw_segment_id", info.get("group_id", "unknown_raw_segment"))
    payload = {
        "status": "dropped",
        "reason": reason,
        "machine_id": info.get("machine_id", ""),
        "raw_segment_id": raw_segment_id,
        "preprocessed_segment_id": f"{raw_segment_id}::pre",
        "raw_start_time": info.get("start_time", ""),
        "raw_end_time": info.get("end_time", ""),
        "raw_time_steps": int(info.get("time_steps", 0) or 0),
        "source_file_count_in_segment": int(info.get("source_file_count_in_segment", 0) or 0),
        "source_files_in_segment": "|".join(info.get("source_files_in_segment", [])),
    }
    if not df.empty and "Time" in df.columns:
        payload.update(
            {
                "current_start_time": df["Time"].iloc[0].strftime("%Y-%m-%d %H:%M:%S"),
                "current_end_time": df["Time"].iloc[-1].strftime("%Y-%m-%d %H:%M:%S"),
                "current_duration_seconds": float((df["Time"].iloc[-1] - df["Time"].iloc[0]).total_seconds()),
                "current_rows": int(len(df)),
            }
        )
    if extra:
        payload.update(extra)
    return payload


def preprocess_record(record: dict, args: argparse.Namespace) -> dict:
    df = pd.DataFrame(record["data"], columns=FEATURES)
    df["Time"] = pd.to_datetime(df["Time"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    df = df.dropna(subset=["Time"]).sort_values("Time").reset_index(drop=True)
    if df.empty:
        return finalize_preprocess_drop(record, df, "time_parse_invalid")
    for col in NUMERIC_FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if args.apply_physiologic_bounds:
        df, bound_stats = apply_physiologic_bounds(df)
    else:
        bound_stats = {
            "counts": {name: 0 for name in NUMERIC_FEATURES},
            "observed_counts": {name: int(df[name].notna().sum()) for name in NUMERIC_FEATURES},
            "ratios": {name: 0.0 for name in NUMERIC_FEATURES},
            "total_count": 0,
            "total_observed": int(sum(int(df[name].notna().sum()) for name in NUMERIC_FEATURES)),
            "total_ratio": 0.0,
        }

    if args.edge_trim_mode == "crop":
        df = crop_time_start_end(df, args.crop_seconds)
    elif args.edge_trim_mode == "zhang":
        pass
    elif args.edge_trim_mode == "none":
        pass
    else:
        raise ValueError(f"unsupported edge trim mode: {args.edge_trim_mode}")
    if df.empty:
        return finalize_preprocess_drop(record, df, "edge_trim_empty")

    nan_ratio = float(df[NUMERIC_FEATURES].isna().sum().sum() / df[NUMERIC_FEATURES].size)
    if nan_ratio > args.nan_ratio_threshold:
        return finalize_preprocess_drop(
            record,
            df,
            "nan_ratio_too_high",
            {
                "nan_ratio_before_fill": nan_ratio,
                "physiologic_clip_total_count": int(bound_stats["total_count"]),
                "physiologic_clip_total_ratio": float(bound_stats["total_ratio"]),
                "physiologic_clip_counts": json.dumps(bound_stats["counts"], ensure_ascii=False),
                "physiologic_clip_observed_counts": json.dumps(bound_stats["observed_counts"], ensure_ascii=False),
            },
        )

    if args.fill_method == "interpolate":
        df[NUMERIC_FEATURES] = df[NUMERIC_FEATURES].interpolate(method="linear", limit_direction="both")
    elif args.fill_method == "mean":
        df[NUMERIC_FEATURES] = df[NUMERIC_FEATURES].fillna(df[NUMERIC_FEATURES].mean())
    else:
        raise ValueError(f"unsupported fill method: {args.fill_method}")

    if args.edge_trim_mode == "zhang":
        df = trim_edges_like_zhang(df, args.spo2_threshold).reset_index(drop=True)
        if df.empty:
            return finalize_preprocess_drop(
                record,
                df,
                "edge_trim_empty",
                {
                    "nan_ratio_before_fill": nan_ratio,
                    "physiologic_clip_total_count": int(bound_stats["total_count"]),
                    "physiologic_clip_total_ratio": float(bound_stats["total_ratio"]),
                    "physiologic_clip_counts": json.dumps(bound_stats["counts"], ensure_ascii=False),
                    "physiologic_clip_observed_counts": json.dumps(bound_stats["observed_counts"], ensure_ascii=False),
                },
            )

    pi_valid = df["PI"].dropna()
    if pi_valid.empty:
        return finalize_preprocess_drop(
            record,
            df,
            "pi_quality_bad",
            {
                "nan_ratio_before_fill": nan_ratio,
                "low_pi_ratio": None,
                "pi_valid_count": 0,
                "physiologic_clip_total_count": int(bound_stats["total_count"]),
                "physiologic_clip_total_ratio": float(bound_stats["total_ratio"]),
                "physiologic_clip_counts": json.dumps(bound_stats["counts"], ensure_ascii=False),
                "physiologic_clip_observed_counts": json.dumps(bound_stats["observed_counts"], ensure_ascii=False),
            },
        )
    low_pi_ratio = float((pi_valid < args.pi_threshold).sum() / len(pi_valid))
    if low_pi_ratio > args.low_pi_ratio_threshold:
        return finalize_preprocess_drop(
            record,
            df,
            "pi_quality_bad",
            {
                "nan_ratio_before_fill": nan_ratio,
                "low_pi_ratio": low_pi_ratio,
                "pi_valid_count": int(len(pi_valid)),
                "physiologic_clip_total_count": int(bound_stats["total_count"]),
                "physiologic_clip_total_ratio": float(bound_stats["total_ratio"]),
                "physiologic_clip_counts": json.dumps(bound_stats["counts"], ensure_ascii=False),
                "physiologic_clip_observed_counts": json.dumps(bound_stats["observed_counts"], ensure_ascii=False),
            },
        )

    remaining_nan_ratio_after_fill = float(df[NUMERIC_FEATURES].isna().sum().sum() / df[NUMERIC_FEATURES].size)
    quality_score, quality_components = compute_segment_quality_score(
        nan_ratio_before_fill=nan_ratio,
        low_pi_ratio=low_pi_ratio,
        physiologic_clip_ratio=float(bound_stats["total_ratio"]),
        remaining_nan_ratio_after_fill=remaining_nan_ratio_after_fill,
    )

    has_hypoxemia = label_hypoxemia(
        df,
        threshold=args.spo2_threshold,
        duration_seconds=args.hypoxemia_duration_seconds,
        operator=args.preprocess_hypo_operator,
    )
    arr = format_times_for_pickle(df)
    info = dict(record["info"])
    raw_segment_id = info.get("raw_segment_id", info.get("group_id", "unknown_raw_segment"))
    info.update(
        {
            "raw_segment_id": raw_segment_id,
            "preprocessed_segment_id": f"{raw_segment_id}::pre",
            "start_time": str(arr[0, 0]),
            "end_time": str(arr[-1, 0]),
            "time_steps": int(arr.shape[0]),
            "has_hypoxemia": int(has_hypoxemia),
            "nan_ratio_before_fill": nan_ratio,
            "low_pi_ratio": low_pi_ratio,
            "pi_valid_count": int(len(pi_valid)),
            "remaining_nan_ratio_after_fill": remaining_nan_ratio_after_fill,
            "physiologic_clip_counts": bound_stats["counts"],
            "physiologic_clip_observed_counts": bound_stats["observed_counts"],
            "physiologic_clip_ratios": bound_stats["ratios"],
            "physiologic_clip_total_count": int(bound_stats["total_count"]),
            "physiologic_clip_total_ratio": float(bound_stats["total_ratio"]),
            "segment_quality_score": quality_score,
            "segment_quality_components": quality_components,
            "preprocessing": {
                "crop_seconds": args.crop_seconds,
                "edge_trim_mode": args.edge_trim_mode,
                "nan_ratio_threshold": args.nan_ratio_threshold,
                "fill_method": args.fill_method,
                "apply_physiologic_bounds": bool(args.apply_physiologic_bounds),
                "pi_threshold": args.pi_threshold,
                "low_pi_ratio_threshold": args.low_pi_ratio_threshold,
                "spo2_threshold": args.spo2_threshold,
                "hypoxemia_duration_seconds": args.hypoxemia_duration_seconds,
                "preprocess_hypo_operator": args.preprocess_hypo_operator,
            },
        }
    )
    return {
        "status": "kept",
        "processed": {"data": arr, "info": info},
        "physiologic_clip_total_count": int(bound_stats["total_count"]),
        "physiologic_clip_counts": bound_stats["counts"],
        "physiologic_clip_observed_counts": bound_stats["observed_counts"],
        "segment_quality_score": quality_score,
    }


def preprocess(args: argparse.Namespace) -> None:
    output_root = Path(args.output_root).resolve()
    raw_dir = output_root / "raw_segments_shards"
    pre_dir = output_root / "preprocessed_shards"
    report_dir = output_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    clear_matching_files(pre_dir, "preprocessed_*.pkl")
    writer = ShardWriter(pre_dir, "preprocessed", args.shard_size)

    raw_count = kept = hypo_count = 0
    machine_stats: Dict[str, dict] = {}
    drop_reason_counts = make_counter_dict()
    drop_records = []
    clip_counts_total = defaultdict(int)
    clip_observed_total = defaultdict(int)
    quality_scores: List[float] = []
    for record in iter_shard_records(raw_dir, "raw_segments_*.pkl"):
        raw_count += 1
        machine_id = str(record["info"].get("machine_id", "unknown_machine"))
        machine_entry = machine_stats.setdefault(
            machine_id,
            {
                "machine_id": machine_id,
                "raw_segments": 0,
                "preprocessed_segments": 0,
                "dropped_segments": 0,
                "hypoxemia_segments": 0,
                "non_hypoxemia_segments": 0,
                "raw_time_steps_total": 0,
                "preprocessed_time_steps_total": 0,
                "drop_reason_counts": make_counter_dict(),
                "physiologic_clip_total_count": 0,
                "physiologic_clip_counts": make_counter_dict(),
                "quality_score_sum": 0.0,
                "quality_score_count": 0,
            },
        )
        machine_entry["raw_segments"] += 1
        machine_entry["raw_time_steps_total"] += int(record["info"].get("time_steps", 0) or 0)

        result = preprocess_record(record, args)
        result_info = result.get("processed", {}).get("info", {}) if result.get("status") == "kept" else result
        result_clip_total = int(result.get("physiologic_clip_total_count", result_info.get("physiologic_clip_total_count", 0)) or 0)
        machine_entry["physiologic_clip_total_count"] += result_clip_total
        raw_clip_counts = result.get("physiologic_clip_counts", result_info.get("physiologic_clip_counts", {}))
        if isinstance(raw_clip_counts, str):
            try:
                raw_clip_counts = json.loads(raw_clip_counts)
            except json.JSONDecodeError:
                raw_clip_counts = {}
        if isinstance(raw_clip_counts, dict):
            for feature_name, value in raw_clip_counts.items():
                amount = int(value or 0)
                machine_entry["physiologic_clip_counts"][feature_name] += amount
                clip_counts_total[feature_name] += amount
        raw_observed_counts = result.get("physiologic_clip_observed_counts", result_info.get("physiologic_clip_observed_counts", {}))
        if isinstance(raw_observed_counts, str):
            try:
                raw_observed_counts = json.loads(raw_observed_counts)
            except json.JSONDecodeError:
                raw_observed_counts = {}
        if isinstance(raw_observed_counts, dict):
            for feature_name, value in raw_observed_counts.items():
                clip_observed_total[feature_name] += int(value or 0)
        if result["status"] != "kept":
            reason = str(result["reason"])
            machine_entry["dropped_segments"] += 1
            machine_entry["drop_reason_counts"][reason] += 1
            drop_reason_counts[reason] += 1
            drop_records.append(result)
            continue
        processed = result["processed"]
        writer.add(processed)
        kept += 1
        machine_entry["preprocessed_segments"] += 1
        machine_entry["preprocessed_time_steps_total"] += int(processed["info"].get("time_steps", 0) or 0)
        has_hypo = int(processed["info"].get("has_hypoxemia", 0))
        hypo_count += has_hypo
        machine_entry["hypoxemia_segments"] += has_hypo
        machine_entry["non_hypoxemia_segments"] += 1 - has_hypo
        quality_score = float(result.get("segment_quality_score", processed["info"].get("segment_quality_score", 0.0)) or 0.0)
        machine_entry["quality_score_sum"] += quality_score
        machine_entry["quality_score_count"] += 1
        quality_scores.append(quality_score)
        if raw_count % 1000 == 0:
            print(f"[preprocess] seen={raw_count} kept={kept} hypo={hypo_count}")

    writer.flush()
    machine_rows = []
    for machine_id, row in sorted(machine_stats.items()):
        flat = dict(row)
        flat["drop_reason_counts"] = json.dumps(dict(row["drop_reason_counts"]), ensure_ascii=False)
        flat["physiologic_clip_counts"] = json.dumps(dict(row["physiologic_clip_counts"]), ensure_ascii=False)
        flat["mean_segment_quality_score"] = (
            float(row["quality_score_sum"] / row["quality_score_count"]) if row["quality_score_count"] > 0 else None
        )
        machine_rows.append(flat)
    clip_ratios_summary = {}
    for feature_name in NUMERIC_FEATURES:
        observed = int(clip_observed_total[feature_name])
        clipped = int(clip_counts_total[feature_name])
        clip_ratios_summary[feature_name] = float(clipped / observed) if observed > 0 else 0.0
    summary = {
        "raw_segments": raw_count,
        "preprocessed_segments": kept,
        "dropped_segments": raw_count - kept,
        "hypoxemia_segments": hypo_count,
        "non_hypoxemia_segments": kept - hypo_count,
        "drop_reason_counts": dict(drop_reason_counts),
        "physiologic_clip_counts": dict(clip_counts_total),
        "physiologic_clip_observed_counts": {k: int(v) for k, v in clip_observed_total.items()},
        "physiologic_clip_ratios": clip_ratios_summary,
        "mean_segment_quality_score": float(np.mean(quality_scores)) if quality_scores else None,
        "median_segment_quality_score": float(np.median(quality_scores)) if quality_scores else None,
    }
    write_json(report_dir / "preprocess_summary.json", summary)
    write_csv(report_dir / "preprocess_machine_report.csv", machine_rows)
    write_csv(report_dir / "preprocess_drop_records.csv", drop_records)
    print(f"[preprocess] done: {summary}")


def merge_preprocessed(args: argparse.Namespace) -> None:
    output_root = Path(args.output_root).resolve()
    pre_dir = output_root / "preprocessed_shards"
    final_dir = output_root / "final_merged_data"
    final_dir.mkdir(parents=True, exist_ok=True)
    all_data = []
    all_info = []
    for record in iter_shard_records(pre_dir, "preprocessed_*.pkl"):
        all_data.append(record["data"])
        all_info.append(record["info"])
    with (final_dir / "merged_data.pkl").open("wb") as f:
        pickle.dump(all_data, f, protocol=pickle.HIGHEST_PROTOCOL)
    with (final_dir / "merged_info.pkl").open("wb") as f:
        pickle.dump(all_info, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[merge] wrote {len(all_data)} records -> {final_dir}")


def find_hypo_segments(df: pd.DataFrame, threshold: float, operator: str, min_duration: int, min_interval: int) -> List[dict]:
    is_hypo = hypo_mask(df["SpO2"], threshold, operator).fillna(False)
    time_diff = df["Time"].diff().dt.total_seconds()
    new_group = is_hypo & ((~is_hypo.shift(fill_value=False)) | (time_diff != 1))
    group_ids = new_group.cumsum()
    work = df.assign(_hypo=is_hypo, _hypo_group=np.where(is_hypo, group_ids, 0))
    candidates = []
    for _, seg in work[work["_hypo_group"] > 0].groupby("_hypo_group", sort=True):
        duration = (seg["Time"].iloc[-1] - seg["Time"].iloc[0]).total_seconds()
        if duration >= min_duration:
            candidates.append({"start": seg["Time"].iloc[0], "end": seg["Time"].iloc[-1], "duration": duration})
    valid = []
    for seg in candidates:
        if valid and (seg["start"] - valid[-1]["end"]).total_seconds() < min_interval:
            continue
        valid.append(seg)
    return valid


def record_to_df(record: dict) -> pd.DataFrame:
    df = pd.DataFrame(record["data"], columns=FEATURES)
    df["Time"] = pd.to_datetime(df["Time"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    for col in NUMERIC_FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["Time"]).sort_values("Time").reset_index(drop=True)


def extract_hypo_windows(record: dict, lookback: int, interval: int, args: argparse.Namespace) -> Tuple[List[Tuple[np.ndarray, dict]], dict]:
    df = record_to_df(record)
    min_interval = lookback + interval
    segments = find_hypo_segments(df, args.spo2_threshold, args.window_hypo_operator, args.min_hypo_duration_seconds, min_interval)
    windows = []
    stats = {
        "candidate_hypo_segments": int(len(segments)),
        "created_windows": 0,
        "dropped_window_out_of_range": 0,
        "dropped_window_empty": 0,
        "dropped_window_too_short": 0,
    }
    for seg_idx, seg in enumerate(segments, start=1):
        window_end = seg["start"] - timedelta(seconds=interval)
        window_start = window_end - timedelta(seconds=lookback)
        if window_start < df["Time"].iloc[0] or window_end > df["Time"].iloc[-1]:
            stats["dropped_window_out_of_range"] += 1
            continue
        window_df = df[(df["Time"] >= window_start) & (df["Time"] < window_end)].copy()
        if window_df.empty:
            stats["dropped_window_empty"] += 1
            continue
        duration = (window_df["Time"].iloc[-1] - window_df["Time"].iloc[0]).total_seconds()
        if duration < lookback - 1:
            stats["dropped_window_too_short"] += 1
            continue
        info = dict(record["info"])
        preprocessed_segment_id = info.get("preprocessed_segment_id", info.get("raw_segment_id", info.get("group_id", "unknown_raw_segment")))
        info.update(
            {
                "sample_type": "hypoxemia",
                "window_type": f"lookback_{lookback}s_interval_{interval}s",
                "window_id": f"{preprocessed_segment_id}::hypo_window{seg_idx:03d}",
                "window_start": window_start.strftime("%Y-%m-%d %H:%M:%S"),
                "window_end": window_end.strftime("%Y-%m-%d %H:%M:%S"),
                "hypo_seg_start": seg["start"].strftime("%Y-%m-%d %H:%M:%S"),
                "hypo_seg_end": seg["end"].strftime("%Y-%m-%d %H:%M:%S"),
                "hypo_seg_duration": seg["duration"],
                "hypo_seg_index": seg_idx,
                "window_data_points": len(window_df),
                "features": FEATURES,
            }
        )
        windows.append((format_times_for_pickle(window_df), info))
        stats["created_windows"] += 1
    return windows, stats


def extract_non_hypo_window(record: dict, lookback: int, rng: random.Random) -> Tuple[Optional[Tuple[np.ndarray, dict]], Optional[str]]:
    df = record_to_df(record)
    total_duration = (df["Time"].iloc[-1] - df["Time"].iloc[0]).total_seconds()
    if total_duration < lookback:
        return None, "segment_shorter_than_lookback"
    start_delta = rng.uniform(0, total_duration - lookback)
    window_start = df["Time"].iloc[0] + timedelta(seconds=start_delta)
    window_end = window_start + timedelta(seconds=lookback)
    window_df = df[(df["Time"] >= window_start) & (df["Time"] < window_end)].copy()
    if window_df.empty:
        return None, "window_empty"
    duration = (window_df["Time"].iloc[-1] - window_df["Time"].iloc[0]).total_seconds()
    if duration < lookback - 1:
        return None, "window_too_short"
    info = dict(record["info"])
    preprocessed_segment_id = info.get("preprocessed_segment_id", info.get("raw_segment_id", info.get("group_id", "unknown_raw_segment")))
    info.update(
        {
            "sample_type": "non_hypoxemia",
            "window_type": f"random_lookback_{lookback}s",
            "window_id": f"{preprocessed_segment_id}::nonhypo_window001",
            "window_start": window_start.strftime("%Y-%m-%d %H:%M:%S"),
            "window_end": window_end.strftime("%Y-%m-%d %H:%M:%S"),
            "window_data_points": len(window_df),
            "features": FEATURES,
        }
    )
    return (format_times_for_pickle(window_df), info), None


def make_windows_for_config(args: argparse.Namespace, lookback: int, interval: int) -> None:
    output_root = Path(args.output_root).resolve()
    pre_dir = output_root / "preprocessed_shards"
    window_dir = output_root / f"balanced_windows_{lookback}s_{interval}s_all"
    window_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed + lookback * 1000 + interval)

    hypo_windows: List[np.ndarray] = []
    hypo_info: List[dict] = []
    non_hypo_records = 0
    machine_stats: Dict[str, dict] = {}

    def get_machine_row(machine_id: str) -> dict:
        return machine_stats.setdefault(
            machine_id,
            {
                "machine_id": machine_id,
                "preprocessed_segments_seen": 0,
                "preprocessed_hypoxemia_segments": 0,
                "preprocessed_non_hypoxemia_segments": 0,
                "final_positive_windows": 0,
                "final_negative_windows": 0,
                "records_not_meet_hypoxemia_definition": 0,
                "candidate_hypo_segments": 0,
                "dropped_positive_window_out_of_range": 0,
                "dropped_positive_window_empty": 0,
                "dropped_positive_window_too_short": 0,
                "dropped_negative_window_segment_shorter_than_lookback": 0,
                "dropped_negative_window_empty": 0,
                "dropped_negative_window_too_short": 0,
                "selected_negative_records": 0,
            },
        )

    for record in iter_shard_records(pre_dir, "preprocessed_*.pkl"):
        machine_id = str(record["info"].get("machine_id", "unknown_machine"))
        row = get_machine_row(machine_id)
        row["preprocessed_segments_seen"] += 1
        if int(record["info"].get("has_hypoxemia", 0)) == 1:
            row["preprocessed_hypoxemia_segments"] += 1
            extracted_windows, extracted_stats = extract_hypo_windows(record, lookback, interval, args)
            row["candidate_hypo_segments"] += extracted_stats["candidate_hypo_segments"]
            row["dropped_positive_window_out_of_range"] += extracted_stats["dropped_window_out_of_range"]
            row["dropped_positive_window_empty"] += extracted_stats["dropped_window_empty"]
            row["dropped_positive_window_too_short"] += extracted_stats["dropped_window_too_short"]
            if extracted_stats["candidate_hypo_segments"] == 0:
                row["records_not_meet_hypoxemia_definition"] += 1
            for data, info in extracted_windows:
                hypo_windows.append(data)
                hypo_info.append(info)
                row["final_positive_windows"] += 1
        else:
            row["preprocessed_non_hypoxemia_segments"] += 1
            row["records_not_meet_hypoxemia_definition"] += 1
            non_hypo_records += 1

    target_non_hypo = min(int(len(hypo_windows) * args.max_non_hypo_ratio), non_hypo_records)
    select_prob = 0.0 if non_hypo_records == 0 else min(target_non_hypo / non_hypo_records, 1.0)
    non_hypo_windows: List[np.ndarray] = []
    non_hypo_info: List[dict] = []
    for record in iter_shard_records(pre_dir, "preprocessed_*.pkl"):
        if int(record["info"].get("has_hypoxemia", 0)) != 0:
            continue
        machine_id = str(record["info"].get("machine_id", "unknown_machine"))
        row = get_machine_row(machine_id)
        if rng.random() > select_prob:
            continue
        row["selected_negative_records"] += 1
        item, reason = extract_non_hypo_window(record, lookback, rng)
        if item is None:
            if reason == "segment_shorter_than_lookback":
                row["dropped_negative_window_segment_shorter_than_lookback"] += 1
            elif reason == "window_empty":
                row["dropped_negative_window_empty"] += 1
            elif reason == "window_too_short":
                row["dropped_negative_window_too_short"] += 1
            continue
        data, info = item
        info["selection_probability"] = select_prob
        non_hypo_windows.append(data)
        non_hypo_info.append(info)
        row["final_negative_windows"] += 1

    all_data = hypo_windows + non_hypo_windows
    all_info = hypo_info + non_hypo_info
    with (window_dir / "window_features.pkl").open("wb") as f:
        pickle.dump(all_data, f, protocol=pickle.HIGHEST_PROTOCOL)
    with (window_dir / "window_info.pkl").open("wb") as f:
        pickle.dump(all_info, f, protocol=pickle.HIGHEST_PROTOCOL)
    summary = {
        "lookback_seconds": lookback,
        "sampling_hz": 1,
        "feature_order": NUMERIC_FEATURES,
        "interval_seconds": interval,
        "total_windows": len(all_data),
        "hypoxemia_windows": len(hypo_windows),
        "non_hypoxemia_windows": len(non_hypo_windows),
        "non_hypo_records": non_hypo_records,
        "selection_probability": select_prob,
    }
    write_json(window_dir / "window_stats.json", summary)
    write_csv(window_dir / "window_machine_report.csv", [machine_stats[k] for k in sorted(machine_stats)])
    print(f"[windows] {lookback}s/{interval}s -> {summary}")


def parse_configs(values: Sequence[str]) -> List[Tuple[int, int]]:
    if not values:
        return DEFAULT_CONFIGS
    configs = []
    for value in values:
        if ":" not in value:
            raise ValueError(f"invalid config '{value}', expected LOOKBACK:INTERVAL")
        left, right = value.split(":", 1)
        configs.append((int(left), int(right)))
    return configs


def make_windows(args: argparse.Namespace) -> None:
    for lookback, interval in parse_configs(args.configs):
        make_windows_for_config(args, lookback, interval)


def run_all(args: argparse.Namespace) -> None:
    raw_extract(args)
    preprocess(args)
    merge_preprocessed(args)
    make_windows(args)


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--shard-size", type=int, default=500)
    parser.add_argument("--spo2-threshold", type=float, default=94.0)
    parser.add_argument("--hypo-operator", choices=["le", "lt"], default="le", help="le means SpO2 <= threshold; lt means SpO2 < threshold.")
    parser.add_argument("--drop-spo2-na-before-segment", type=parse_bool, default=False, help="Whether to drop rows with missing SpO2 before continuous segmentation.")
    parser.add_argument("--spo2-na-break-segment", type=parse_bool, default=True, help="Whether missing SpO2 should explicitly break continuous segments when rows are retained.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deduplicated SpO2 data extraction and window pipeline.")
    sub = parser.add_subparsers(dest="command", required=True)

    raw = sub.add_parser("raw", help="Merge CSV files by machine, deduplicate by Time, and write raw segment shards.")
    raw.add_argument("--input-root", required=True)
    raw.add_argument("--min-segment-seconds", type=int, default=600)
    add_common_args(raw)
    raw.set_defaults(func=raw_extract)

    pre = sub.add_parser("preprocess", help="Preprocess raw segment shards.")
    add_common_args(pre)
    pre.add_argument("--crop-seconds", type=int, default=30)
    pre.add_argument("--edge-trim-mode", choices=["crop", "none", "zhang"], default="zhang")
    pre.add_argument("--nan-ratio-threshold", type=float, default=0.1)
    pre.add_argument("--fill-method", choices=["interpolate", "mean"], default="interpolate")
    pre.add_argument("--apply-physiologic-bounds", type=parse_bool, default=True)
    pre.add_argument("--pi-threshold", type=float, default=0.3)
    pre.add_argument("--low-pi-ratio-threshold", type=float, default=0.3)
    pre.add_argument("--hypoxemia-duration-seconds", type=int, default=20)
    pre.add_argument("--preprocess-hypo-operator", choices=["le", "lt"], default="lt", help="Matches data_preprocess3.py by default: SpO2 < threshold.")
    pre.set_defaults(func=preprocess)

    merge = sub.add_parser("merge", help="Merge preprocessed shards into final compatible pickle files.")
    add_common_args(merge)
    merge.set_defaults(func=merge_preprocessed)

    win = sub.add_parser("windows", help="Create balanced window datasets from preprocessed shards.")
    add_common_args(win)
    win.add_argument("--configs", nargs="*", default=[], help="Window configs like 180:30. Empty means thesis default configs.")
    win.add_argument("--min-hypo-duration-seconds", type=int, default=20)
    win.add_argument("--max-non-hypo-ratio", type=float, default=2.0)
    win.add_argument("--seed", type=int, default=42)
    win.add_argument("--window-hypo-operator", choices=["le", "lt"], default="le", help="Matches time_slicing2.py by default: SpO2 <= threshold.")
    win.set_defaults(func=make_windows)

    all_cmd = sub.add_parser("all", help="Run raw, preprocess, merge, and windows.")
    all_cmd.add_argument("--input-root", required=True)
    all_cmd.add_argument("--min-segment-seconds", type=int, default=600)
    add_common_args(all_cmd)
    all_cmd.add_argument("--crop-seconds", type=int, default=30)
    all_cmd.add_argument("--edge-trim-mode", choices=["crop", "none", "zhang"], default="zhang")
    all_cmd.add_argument("--nan-ratio-threshold", type=float, default=0.1)
    all_cmd.add_argument("--fill-method", choices=["interpolate", "mean"], default="interpolate")
    all_cmd.add_argument("--apply-physiologic-bounds", type=parse_bool, default=True)
    all_cmd.add_argument("--pi-threshold", type=float, default=0.3)
    all_cmd.add_argument("--low-pi-ratio-threshold", type=float, default=0.3)
    all_cmd.add_argument("--hypoxemia-duration-seconds", type=int, default=20)
    all_cmd.add_argument("--preprocess-hypo-operator", choices=["le", "lt"], default="lt", help="Matches data_preprocess3.py by default: SpO2 < threshold.")
    all_cmd.add_argument("--configs", nargs="*", default=[], help="Window configs like 180:30. Empty means thesis default configs.")
    all_cmd.add_argument("--min-hypo-duration-seconds", type=int, default=20)
    all_cmd.add_argument("--max-non-hypo-ratio", type=float, default=2.0)
    all_cmd.add_argument("--seed", type=int, default=42)
    all_cmd.add_argument("--window-hypo-operator", choices=["le", "lt"], default="le", help="Matches time_slicing2.py by default: SpO2 <= threshold.")
    all_cmd.set_defaults(func=run_all)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
