# === SOTABand 工具标准模板 ===
import base64
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from PIL import Image, ImageFilter, ImageOps, ImageStat


# ── 运行目录（由平台注入，禁止写死绝对路径或按 __file__ 层级反推）──
# SOTABAND_ROOT      系统根：代码 + 内置资源，视为只读
# SOTABAND_USER_HOME 当前用户目录：本工具的一切产物写这里
# TOOL_DIR           本工具实现目录
def _env_path(name):
    v = os.environ.get(name, "").strip()
    return Path(v) if v else None


def _find_up(path, marker):
    """兜底：向上找 marker 所在目录。只按特征文件定位，不假定固定层级。"""
    p = path
    for _ in range(12):
        if (p / marker).exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return None


_tool_dir = _env_path("TOOL_DIR") or (Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd())
_PROJECT_ROOT = _env_path("SOTABAND_ROOT") or _find_up(_tool_dir, "core") or Path.cwd()
# 用户目录：env 缺失时按「SOTABAND_HOME 根 + uid」还原，绝不退回系统根
_USER_HOME = _env_path("SOTABAND_USER_HOME") or (
    (_env_path("SOTABAND_HOME") or _PROJECT_ROOT.parent / "SB-USER")
    / (os.environ.get("SOTABAND_USER_ID", "").strip() or "0")
)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 数据目录（产物一律落用户目录，系统根随 git 分发不可写脏）──
_DATA_DIR = _USER_HOME / "data"
_DOWNLOADS_DIR = _DATA_DIR / "downloads"


def _out_dir(*parts) -> Path:
    """本工具产物目录：USER_HOME/data/<工具或子目录>/<...>，自动创建。"""
    d = _DATA_DIR.joinpath(*parts) if parts else _DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── API 调用辅助 ──
def _call_api(api_name: str, **params) -> dict:
    """调用系统 API"""
    from core.api import get_api
    api = get_api(api_name)
    return api.call(**params)


# ── 工具调用辅助 ──
def _call_tool(tool_name: str, **params) -> dict:
    """调用已注册的工具"""
    import subprocess as _sp
    # 资源根读取顺序：私有优先（用户「复制为副本」后的修改生效），系统公共层兜底
    _roots = [_USER_HOME, _env_path("SOTABAND_HOME") or (_PROJECT_ROOT.parent / "SB-USER")
              / (os.environ.get("SOTABAND_SYSTEM_UID", "100").strip() or "100")]
    tool_dir = next((r / "tools" / "implementations" / tool_name for r in _roots
                     if (r / "tools" / "implementations" / tool_name / "tool.py").exists()),
                    _roots[0] / "tools" / "implementations" / tool_name)
    tool_file = tool_dir / "tool.py"
    if not tool_file.exists():
        return {"status": "failed", "message": f"Tool '{tool_name}' not found"}
    venv_py = tool_dir / ".venv" / "bin" / "python"
    py_exe = str(venv_py) if venv_py.exists() else sys.executable
    script = f"import json, sys; sys.path.insert(0, {str(_PROJECT_ROOT)!r}); exec(open({str(tool_file)!r}).read()); print(json.dumps(execute(**{params!r}), default=str, ensure_ascii=False))"
    proc = _sp.run([py_exe, "-c", script], capture_output=True, text=True, timeout=30)
    try:
        return json.loads(proc.stdout.strip())
    except Exception:
        return {"status": "failed", "message": proc.stderr[:500]}


# ── 文件路径辅助 ──
def _resolve_path(path: str) -> str:
    """将相对/绝对路径转为绝对路径（基于 _PROJECT_ROOT）"""
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(_PROJECT_ROOT / p)


# === 头部结束，以下由 LLM 生成 ===

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}


def _as_float(value, default: float) -> float:
    try:
        v = float(value)
        return v if v == v else default  # 过滤 NaN
    except (TypeError, ValueError):
        return default


def _quality_check(image: Image.Image, cfg: dict) -> tuple[bool, str, dict]:
    """按规则判定图像质量。返回 (是否保留, 剔除原因, 统计信息)。"""
    w, h = image.size
    stats = {"width": w, "height": h}

    # 1) 尺寸下限：小于阈值的图对训练无意义
    if w < cfg["min_size"] or h < cfg["min_size"]:
        return False, f"尺寸过小 {w}x{h} < {cfg['min_size']}", stats

    # 2) 灰度统计：亮度异常 / 低信息量（纯色或接近纯色）
    gray = image.convert("L")
    stat = ImageStat.Stat(gray)
    mean, stddev = stat.mean[0], stat.stddev[0]
    stats["brightness"] = round(mean, 2)
    stats["contrast"] = round(stddev, 2)
    if mean < cfg["min_brightness"]:
        return False, f"画面过暗 亮度均值 {mean:.1f} < {cfg['min_brightness']}", stats
    if mean > cfg["max_brightness"]:
        return False, f"画面过亮 亮度均值 {mean:.1f} > {cfg['max_brightness']}", stats
    if stddev < cfg["min_contrast"]:
        return False, f"信息量过低 灰度标准差 {stddev:.1f} < {cfg['min_contrast']}", stats

    # 3) 模糊检测：Laplacian 方差。用 PIL FIND_EDGES 近似，避免引入 numpy 依赖。
    #    边缘图灰度均值越低说明高频分量越少，画面越模糊。
    edges = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0]
    stats["edge_score"] = round(edges, 2)
    if edges < cfg["min_edge"]:
        return False, f"画面模糊 边缘强度 {edges:.1f} < {cfg['min_edge']}", stats

    return True, "", stats


def execute(**kwargs) -> dict[str, Any]:
    try:
        dataset_name = str(kwargs.get("dataset") or "").strip()
        output_dataset = str(kwargs.get("output_dataset") or "").strip()
        if not dataset_name or not output_dataset:
            return {"status": "failed",
                    "message": "缺少必要参数 dataset, output_dataset"}

        # ── 清洗规则（保守默认值；阈值均可覆盖）──
        cfg = {
            "min_size": int(_as_float(kwargs.get("min_size"), 64)),
            "min_brightness": _as_float(kwargs.get("min_brightness"), 35.0),
            "max_brightness": _as_float(kwargs.get("max_brightness"), 215.0),
            "min_contrast": _as_float(kwargs.get("min_contrast"), 20.0),
            "min_edge": _as_float(kwargs.get("min_edge"), 6.0),
            # 精确去重：按文件字节 MD5 判定。dHash 近似去重在同场景不同图上
            # 误判率过高（实测误杀 8/86），不作为剔除依据。
            "dedupe": str(kwargs.get("dedupe", "true")).strip().lower() not in ("false", "0", "no"),
            "normalize_exif": str(kwargs.get("normalize_exif", "true")).strip().lower() not in ("false", "0", "no"),
            "max_edge": int(_as_float(kwargs.get("max_edge"), 0)),  # 0=不缩放
        }
        cfg["min_size"] = max(16, min(cfg["min_size"], 4096))

        # ── 1. 获取源数据集 ──
        ds_resp = _call_api("api-data-get", name=dataset_name)
        dataset_info = (ds_resp or {}).get("dataset")
        if not dataset_info:
            return {"status": "failed", "message": "数据集不存在或无法访问"}
        data_path_src = dataset_info.get("data_path") or dataset_info.get("path")
        if not data_path_src or not Path(data_path_src).is_dir():
            return {"status": "failed", "message": "数据集不存在或无法访问"}
        src_dir = Path(data_path_src)

        # 只扫描数据集根目录（标注工具的 labeled/ 可视化子目录不参与训练）
        img_files = sorted(
            f for f in src_dir.iterdir()
            if f.is_file() and f.suffix.lower() in IMG_EXTS
        )
        if not img_files:
            return {"status": "failed", "message": "数据集中没有找到图片文件"}

        # ── 2. 建立产物目录 ──
        # 数据集目录(dataset_dir)与留档目录(rejected_dir)必须分离：
        # 只把通过清洗的图注册为数据集，坏图留在数据集目录之外，
        # 否则前端预览会把被剔除的图也列进"清洗后数据集"，看起来像没洗。
        timestamp = time.strftime("%Y%m%d%H%M%S")
        target_dir = _DOWNLOADS_DIR / f"cleaned_{timestamp}"
        dataset_dir = target_dir / "dataset"
        images_dir = dataset_dir / "images"
        labels_dir = dataset_dir / "labels"
        rejected_dir = target_dir / "rejected"
        for d in (images_dir, labels_dir, rejected_dir):
            d.mkdir(parents=True, exist_ok=True)

        kept: list[dict] = []
        rejected: list[dict] = []
        seen_md5: dict[str, str] = {}
        total_size = 0
        collected_formats: set[str] = set()

        # ── 3. 逐张检查 ──
        for src_path in img_files:
            name = src_path.name
            try:
                with Image.open(src_path) as raw:
                    if cfg["normalize_exif"]:
                        image = ImageOps.exif_transpose(raw)
                    else:
                        image = raw.copy()
                    if image.mode not in ("RGB", "L"):
                        image = image.convert("RGB")
                    image.load()

                ok, reason, qstats = _quality_check(image, cfg)
                if not ok:
                    rejected.append({"file": name, "reason": reason, "stats": qstats})
                    (rejected_dir / name).write_bytes(src_path.read_bytes())
                    continue

                # 精确去重：按文件字节 MD5。近似哈希在同场景不同图上误判率高，
                # 只把内容完全一致的重复文件剔除，避免误删合法训练样本。
                if cfg["dedupe"]:
                    digest = hashlib.md5(src_path.read_bytes()).hexdigest()
                    dup_of = seen_md5.get(digest)
                    if dup_of:
                        rejected.append({"file": name, "reason": f"重复文件，与 {dup_of} 内容一致",
                                         "stats": qstats})
                        (rejected_dir / name).write_bytes(src_path.read_bytes())
                        continue
                    seen_md5[digest] = name

                # 可选等比缩放（最长边上限），保护原始分辨率默认不缩放
                w, h = image.size
                if cfg["max_edge"] > 0 and max(w, h) > cfg["max_edge"]:
                    scale = cfg["max_edge"] / max(w, h)
                    image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

                # 统一编号命名，保证 YOLO 训练时图像与标签一一对应
                idx = len(kept) + 1
                out_name = f"{idx:06d}.jpg"
                out_path = images_dir / out_name
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.save(out_path, format="JPEG", quality=95)

                # 同步搬运已有的 YOLO 标签（若存在）：支持同名 .txt 与标框工具产物
                label_src = src_dir / f"{src_path.stem}.txt"
                if label_src.is_file():
                    (labels_dir / f"{idx:06d}.txt").write_bytes(label_src.read_bytes())

                kept.append({
                    "source": name,
                    "image": f"images/{out_name}",
                    "label": f"labels/{idx:06d}.txt" if label_src.is_file() else "",
                    "stats": qstats,
                })
                collected_formats.add("jpg")
                if label_src.is_file():
                    collected_formats.add("txt")
            except Exception as e:
                rejected.append({"file": name, "reason": f"读取/处理失败: {e!s}", "stats": {}})
                try:
                    (rejected_dir / name).write_bytes(src_path.read_bytes())
                except Exception:
                    pass

        if not kept:
            return {"status": "failed",
                    "message": f"全部 {len(img_files)} 张图片均未通过清洗，详见报告"}

        # ── 4. 写清洗报告（全部剔除决定可追溯）──
        report = {
            "source_dataset": dataset_name,
            "output_dataset": output_dataset,
            "rules": cfg,
            "total": len(img_files),
            "kept": len(kept),
            "rejected": len(rejected),
            "kept_items": kept,
            "rejected_items": rejected,
        }
        # 报告放数据集目录内：用户点开清洗后数据集即可看到每张的丢弃原因；
        # 被剔除的原图则留在数据集目录外的 rejected/，不污染预览与训练集。
        report_path = dataset_dir / "clean_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        # ── 5. 注册为训练数据集（只注册 dataset_dir，不含 rejected/）──
        total_size = sum(f.stat().st_size for f in dataset_dir.rglob("*") if f.is_file())
        dataset_id = f"{output_dataset}_{timestamp}"
        raw_md = (f"图像清洗数据集，源数据集：{dataset_name}，"
                  f"保留 {len(kept)}/{len(img_files)} 张，剔除 {len(rejected)} 张（原因见 clean_report.json），"
                  f"规则：min_size={cfg['min_size']} dedupe={cfg['dedupe']}")
        reg_resp = _call_api(
            "api-data-register",
            id=dataset_id,
            name=output_dataset,
            raw_md=raw_md,
            data_path=str(dataset_dir),
            file_count=len(kept),
            total_size=total_size,
            formats=sorted(collected_formats),
        ) or {}
        if reg_resp.get("status") == "failed":
            return {"status": "failed",
                    "message": f"数据集注册失败: {reg_resp.get('message', '')}"}

        return {
            "status": "success",
            "message": (f"清洗完成：{len(img_files)} 张输入，保留 {len(kept)} 张，"
                        f"剔除 {len(rejected)} 张"),
            "output_format": "image",
            "data": {
                "image_path": str(images_dir / kept[0]["image"].split("/", 1)[1]),
                "dataset_id": reg_resp.get("dataset_id", dataset_id),
                "data_path": str(dataset_dir),
                "rejected_path": str(rejected_dir),
                "report_path": str(report_path),
                "total": len(img_files),
                "kept": len(kept),
                "rejected": len(rejected),
                "label_count": sum(1 for k in kept if k["label"]),
            },
        }
    except Exception as e:
        return {"status": "failed", "message": f"执行异常: {e!s}"}
