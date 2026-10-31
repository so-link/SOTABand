# === SOTABand 工具标准模板 ===
import os, sys, json, time
from pathlib import Path
from typing import Any
import requests

# ── 项目根路径 ──
_tool_dir = os.environ.get("TOOL_DIR", "")
if _tool_dir:
    _PROJECT_ROOT = Path(_tool_dir).resolve().parent.parent.parent.parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 数据目录 ──
_DATA_DIR = _PROJECT_ROOT / "data"
_DOWNLOADS_DIR = _DATA_DIR / "downloads"

# ── API 调用辅助 ──
def _call_api(api_name: str, **params) -> dict:
    """调用系统 API"""
    from core.api import get_api
    api = get_api(api_name)
    return api.call(**params)

# ── LLM 调用辅助（统一走系统配置的 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL） ──
def _llm_chat(messages: list, **kwargs) -> str:
    """同步调用系统统一大模型客户端，返回完整文本。

    跟随全局配置（config/settings.py 的 PROVIDER_PRESETS）自动选择服务商：
    DeepSeek / OpenAI / Kimi / 智谱 / 通义 / 硅基流动 / MiniMax / MiMo / 豆包 等。
    禁止在本工具内直连任何具体服务商端点或硬编码模型名。
    """
    import asyncio
    from core.llm.client import create_llm_client
    client = create_llm_client()
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(client.chat(messages, **kwargs))
        loop.run_until_complete(client.aclose())
        return result
    finally:
        loop.close()

# ── 工具调用辅助 ──
def _call_tool(tool_name: str, **params) -> dict:
    """调用已注册的工具（通过 registry.json 查找工具 ID 对应的实现目录）"""
    import subprocess as _sp
    reg_path = _PROJECT_ROOT / "resources" / "tools" / "registry.json"
    tool_id = tool_name
    if reg_path.exists():
        try:
            tools = json.loads(reg_path.read_text(encoding="utf-8"))
            for t in tools:
                if t.get("id") == tool_name or t.get("name") == tool_name:
                    tool_id = t["id"]
                    break
        except Exception:
            pass
    tool_dir = _PROJECT_ROOT / "resources" / "tools" / "implementations" / tool_id
    tool_file = tool_dir / "tool.py"
    if not tool_file.exists():
        return {"status": "failed", "message": f"Tool '{tool_name}' (id={tool_id}) not found"}
    venv_py = tool_dir / ".venv" / "bin" / "python"
    py_exe = str(venv_py) if venv_py.exists() else sys.executable
    script = f"import json, sys; sys.path.insert(0, {str(_PROJECT_ROOT)!r}); exec(open({str(tool_file)!r}, encoding='utf-8').read()); print(json.dumps(execute(**{params!r}), default=str, ensure_ascii=False))"
    proc = _sp.run([py_exe, "-c", script], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=30)
    try:
        return json.loads(proc.stdout.strip())
    except:
        return {"status": "failed", "message": proc.stderr[:500]}

# ── 文件路径辅助 ──
def _resolve_path(path: str) -> str:
    """将相对/绝对路径转为绝对路径（基于 _PROJECT_ROOT）"""
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(_PROJECT_ROOT / p)

# === 头部结束，以下由 LLM 生成 ===

def execute(**kwargs) -> dict[str, Any]:
    """
    YOLOv8n 视频流目标检测 + 回调通知（支持长运行实时流模式）。

    两种模式（mode 参数切换）：
      - "once"（默认）：有界处理，处理完返回统计摘要（兼容单图/短视频验证）。
      - "stream"：接入实时流，长时间运行；结果逐帧通过 callback_url 推送；
                  默认后台常驻（daemon=True），execute 立即返回"已启动"句柄，
                  不会阻塞调用方。配合 stop_stream_id 可随时停止。

    输入参数：
        stream_url (str): 视频流地址。RTSP / RTMP / HTTP 直播流、本地视频文件，兼容单图。
        callback_url (str): 检测结果回调地址；每处理一帧 POST 该帧检测 JSON 给下一层服务。
        mode (str, 可选): "once" | "stream"，默认 "once"
        model_name (str, 可选): 模型标识或本地路径，默认 yolov8n.pt
        conf_threshold (float, 可选): 置信度阈值，默认 0.25
        sample_interval (int, 可选): 帧维度跳帧，每隔多少帧处理一次，默认 1
        frame_interval_sec (float, 可选): 时间抽样间隔（秒），默认 0=逐帧；>0 表示每隔 N 秒抽一帧
        max_frames (int, 可选): 最多处理帧数；once 默认 30，stream 默认 0=不限
        duration (float, 可选): 最长运行秒数，默认 0=不限
        reconnect (bool, 可选): 流读取失败时自动重连，默认 True（实时流推荐）
        daemon (bool, 可选): stream 模式下是否后台常驻，默认 True
        save_annotated (bool, 可选): 是否保存标注帧图，默认 False
        stream_id (str, 可选): 流标识，用于停止控制（不传自动生成）
        stop_stream_id (str, 可选): 传入则仅写入停止信号并退出（停止某条流）
    输出格式：text
    """
    try:
        # 停止控制：传入 stop_stream_id 时仅写入停止标记
        stop_id = kwargs.get("stop_stream_id")
        if stop_id:
            marker = _DATA_DIR / "detections" / "streams" / f"{stop_id}.stop"
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(str(time.time()), encoding="utf-8")
                return {"status": "success", "output_format": "text",
                        "message": f"已发送停止信号: {stop_id}", "data": {"stream_id": stop_id}}
            except Exception as e:
                return {"status": "failed", "output_format": "text",
                        "message": f"停止信号写入失败: {e}", "data": {}}

        mode = str(kwargs.get("mode", "once")).lower()
        if mode == "stream":
            return _run_stream(kwargs)
        return _run_once(kwargs)
    except Exception as e:
        import traceback
        return {"status": "failed", "output_format": "text",
                "message": f"工具执行失败: {str(e)}\n\nTraceback:\n{traceback.format_exc()}",
                "data": {}}


def _run_once(kwargs) -> dict[str, Any]:
    """有界处理模式：处理完返回统计摘要（原单图/短视频逻辑）。"""
    stream_url = kwargs.get("stream_url") or kwargs.get("image_path", "")
    if not stream_url:
        return {"status": "failed", "output_format": "text",
                "message": "参数 stream_url 不能为空", "data": {}}
    callback_url = kwargs.get("callback_url", "")
    if not callback_url:
        return {"status": "failed", "output_format": "text",
                "message": "参数 callback_url 不能为空", "data": {}}

    model_name = kwargs.get("model_name", "yolov8n.pt")
    try:
        conf = float(kwargs.get("conf_threshold", 0.25))
    except (TypeError, ValueError):
        conf = 0.25
    try:
        sample_interval = max(1, int(kwargs.get("sample_interval", 1)))
    except (TypeError, ValueError):
        sample_interval = 1
    try:
        max_frames = max(1, int(kwargs.get("max_frames", 30)))
    except (TypeError, ValueError):
        max_frames = 30
    try:
        duration = float(kwargs.get("duration", 0))
    except (TypeError, ValueError):
        duration = 0
    save_annotated = bool(kwargs.get("save_annotated", False))

    import cv2
    from ultralytics import YOLO

    cap = cv2.VideoCapture(stream_url)
    if not cap.isOpened():
        return {"status": "failed", "output_format": "text",
                "message": f"无法打开视频流: {stream_url}", "data": {}}

    model = YOLO(model_name)
    out_dir = _DATA_DIR / "detections" / "streams"
    out_dir.mkdir(parents=True, exist_ok=True)

    frames_processed = 0
    callbacks_sent = 0
    callbacks_failed = 0
    detections_total = 0
    start = time.time()
    frame_id = 0

    while frames_processed < max_frames:
        if duration and (time.time() - start) >= duration:
            break
        ok, frame = cap.read()
        if not ok:
            break
        frame_id += 1
        if (frame_id - 1) % sample_interval != 0:
            continue

        results = model(frame, conf=conf, verbose=False)
        result = results[0]
        detections = []
        for box in result.boxes:
            cls_id = int(box.cls[0])
            name = model.names.get(cls_id, str(cls_id))
            score = float(box.conf[0])
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
            detections.append({
                "class_id": cls_id, "class_name": name,
                "confidence": round(score, 4),
                "bbox_xyxy": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            })

        payload = {"source": stream_url, "frame_id": frame_id,
                   "timestamp": int(time.time() * 1000), "detections": detections}
        if save_annotated:
            annotated = result.plot()
            out_path = out_dir / f"frame_{frame_id}_{int(time.time() * 1000)}.jpg"
            try:
                cv2.imwrite(str(out_path), annotated)
                payload["annotated_image_path"] = str(out_path.resolve())
            except Exception:
                pass

        try:
            resp = requests.post(callback_url, json=payload, timeout=10)
            if resp.ok:
                callbacks_sent += 1
            else:
                callbacks_failed += 1
        except Exception:
            callbacks_failed += 1

        detections_total += len(detections)
        frames_processed += 1

    cap.release()
    msg = (f"视频流处理完成：处理 {frames_processed} 帧，"
           f"发送回调 {callbacks_sent} 次，失败 {callbacks_failed} 次，"
           f"累计检测目标 {detections_total} 个")
    return {"status": "success", "output_format": "text", "message": msg,
            "data": {"stream_url": stream_url, "frames_processed": frames_processed,
                     "callbacks_sent": callbacks_sent, "callbacks_failed": callbacks_failed,
                     "detections_total": detections_total, "model": model_name,
                     "conf_threshold": conf}}


def _run_stream(kwargs) -> dict[str, Any]:
    """实时流长运行模式：后台常驻 worker 逐帧检测并回调，execute 立即返回句柄。"""
    stream_url = kwargs.get("stream_url") or kwargs.get("image_path", "")
    if not stream_url:
        return {"status": "failed", "output_format": "text",
                "message": "参数 stream_url 不能为空", "data": {}}
    callback_url = kwargs.get("callback_url", "")
    if not callback_url:
        return {"status": "failed", "output_format": "text",
                "message": "参数 callback_url 不能为空", "data": {}}

    model_name = kwargs.get("model_name", "yolov8n.pt")
    try:
        conf = float(kwargs.get("conf_threshold", 0.25))
    except (TypeError, ValueError):
        conf = 0.25
    try:
        sample_interval = max(1, int(kwargs.get("sample_interval", 1)))
    except (TypeError, ValueError):
        sample_interval = 1
    try:
        frame_interval_sec = float(kwargs.get("frame_interval_sec", 0))
    except (TypeError, ValueError):
        frame_interval_sec = 0
    try:
        max_frames = int(kwargs.get("max_frames", 0))  # 0=不限
    except (TypeError, ValueError):
        max_frames = 0
    try:
        duration = float(kwargs.get("duration", 0))
    except (TypeError, ValueError):
        duration = 0
    reconnect = bool(kwargs.get("reconnect", True))
    save_annotated = bool(kwargs.get("save_annotated", False))
    daemon = bool(kwargs.get("daemon", True))

    out_dir = _DATA_DIR / "detections" / "streams"
    out_dir.mkdir(parents=True, exist_ok=True)
    stream_id = kwargs.get("stream_id") or f"stream_{int(time.time() * 1000)}"
    marker = out_dir / f"{stream_id}.stop"
    if marker.exists():
        marker.unlink()  # 清除历史停止信号

    log_path = out_dir / f"{stream_id}.log"
    params = dict(stream_url=stream_url, callback_url=callback_url,
                  model_name=model_name, conf_threshold=conf,
                  sample_interval=sample_interval, frame_interval_sec=frame_interval_sec,
                  max_frames=max_frames, duration=duration, reconnect=reconnect,
                  save_annotated=save_annotated, stream_id=stream_id,
                  marker_path=str(marker), log_path=str(log_path))

    if not daemon:
        _stream_worker(**params)
        return {"status": "success", "output_format": "text",
                "message": "实时流处理结束（前台模式）", "data": {"stream_id": stream_id}}

    # 后台常驻：启动独立 worker 进程，execute 立即返回
    import subprocess as _sp
    pf = out_dir / f"{stream_id}.params.json"
    pf.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")
    tool_file = Path(os.environ.get("TOOL_DIR", "")) / "tool.py"
    if not tool_file.exists():
        tool_file = Path(__file__).resolve()
    log_path = out_dir / f"{stream_id}.log"
    # 预创建空日志文件，保证调用方立刻能看到产物路径
    try:
        open(str(log_path), "ab").close()
    except OSError:
        pass
    # 子进程的 stdio 一律给 DEVNULL，由子进程自己把日志写进 .log：
    #   1) 避免污染常驻 Worker 与 Agent 之间的 JSON 行协议管道；
    #   2) 规避 Windows 上「父进程把文件对象传给 Popen」导致子进程 stdio 句柄失效
    #      （Fatal Python error: init_sys_streams: can't initialize sys standard streams）。
    _popen_kw = {}
    if os.name == "nt":
        # 彻底脱离父控制台，避免 Ctrl+C 误伤常驻 worker
        _popen_kw["creationflags"] = _sp.DETACHED_PROCESS | _sp.CREATE_NEW_PROCESS_GROUP
    else:
        _popen_kw["start_new_session"] = True
    try:
        _sp.Popen([sys.executable, str(tool_file), "--worker",
                   "--params-file", str(pf)],
                  stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                  env={**os.environ, "TOOL_DIR": os.environ.get("TOOL_DIR", ""),
                       "PYTHONUNBUFFERED": "1"},
                  **_popen_kw)
    except Exception as e:
        return {"status": "failed", "output_format": "text",
                "message": f"后台 worker 启动失败: {e}", "data": {}}
    return {"status": "running", "output_format": "text",
            "message": (f"实时流处理已启动（后台常驻），结果将通过 callback_url 持续推送。"
                        f"停止请调用 execute(stop_stream_id='{stream_id}')"),
            "data": {"stream_id": stream_id, "stop_stream_id": stream_id,
                     "marker": str(marker), "log": str(log_path)}}


def _redirect_to_log(log_path) -> bool:
    """把当前进程 stdout/stderr 重定向到日志文件（幂等）。

    子进程由父进程以 DEVNULL 启动，自身负责落盘日志；
    这样既不依赖 Windows 的句柄继承，也避免污染 Agent 的 JSON 协议管道。
    """
    if not log_path:
        return False
    try:
        if getattr(sys, "_sotaband_log_redirected", False):
            return True
        import io as _io
        _fp = open(str(log_path), "ab", buffering=0)
        _so = _io.TextIOWrapper(_fp, encoding="utf-8", errors="replace",
                                line_buffering=True)
        sys.stdout = _so
        sys.stderr = _so
        sys._sotaband_log_redirected = True
        return True
    except Exception:
        return False


def _stream_worker(stream_url, callback_url, model_name="yolov8n.pt",
                   conf_threshold=0.25, sample_interval=1, frame_interval_sec=0.0,
                   max_frames=0, duration=0.0, reconnect=True, save_annotated=False,
                   stream_id="", marker_path=None, log_path=None):
    """长运行 worker：逐帧/定时抽样检测，断流重连，信号/标记停止。"""
    import signal as _signal
    stop = {"flag": False}
    def _on_term(signum, frame):
        stop["flag"] = True
    try:
        _signal.signal(_signal.SIGTERM, _on_term)
    except Exception:
        pass

    import cv2
    from ultralytics import YOLO

    marker = Path(marker_path) if marker_path else None
    out_dir = _DATA_DIR / "detections" / "streams"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 日志直写文件：前台模式（daemon=False）下本函数运行在常驻 Worker 进程内，
    # 其 sys.stdout 是与 Agent 之间的 JSON 行协议管道，绝不能改写，只能写文件。
    _log_fp = None
    try:
        if log_path:
            _log_fp = open(str(log_path), "ab", buffering=0)
    except Exception:
        _log_fp = None

    def _log(msg: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        if _log_fp is not None:
            try:
                _log_fp.write((line + "\n").encode("utf-8"))
                _log_fp.flush()
                return
            except Exception:
                pass
        print(line, flush=True)

    def _open():
        return cv2.VideoCapture(stream_url)

    _log(f"worker 就绪 stream={stream_id} url={stream_url} model={model_name} conf={conf_threshold}")
    if not str(callback_url).startswith(("http://", "https://")):
        _log(f"警告: callback_url 格式异常({callback_url!r})，回调大概率失败，但不影响检测")
    cap = _open()
    _log(f"视频流打开结果: opened={bool(cap and cap.isOpened())}")
    model = YOLO(model_name)
    _log("模型加载完成，开始检测")

    start = time.time()
    frame_id = 0
    last_ts = 0.0
    frames_processed = 0
    callbacks_sent = 0
    callbacks_failed = 0
    detections_total = 0
    reconnects = 0
    # ── 回调熔断状态 ──
    # 回调地址无效/不可达时，只降频重试并告警，检测主循环照常运行、不被网络超时拖慢
    _cb_fail_streak = 0
    _cb_paused_until = 0.0

    def _should_stop():
        if stop["flag"]:
            return True
        if marker and marker.exists():
            return True
        if duration and (time.time() - start) >= duration:
            return True
        if max_frames and frames_processed >= max_frames:
            return True
        return False

    while not _should_stop():
        if cap is None or not cap.isOpened():
            if reconnect:
                time.sleep(2)
                cap = _open()
                reconnects += 1
                if reconnects <= 3 or reconnects % 10 == 0:
                    _log(f"视频流未打开，重连中（第 {reconnects} 次）")
                continue
            _log("视频流未打开且未启用重连，退出")
            break
        ok, frame = cap.read()
        if not ok:
            if reconnect:
                try:
                    cap.release()
                except Exception:
                    pass
                time.sleep(2)
                cap = _open()
                reconnects += 1
                if reconnects <= 3 or reconnects % 10 == 0:
                    _log(f"读取帧失败，重连中（第 {reconnects} 次）")
                continue
            _log("读取帧失败且未启用重连，退出")
            break
        frame_id += 1
        now = time.time()
        if frame_interval_sec > 0 and (now - last_ts) < frame_interval_sec:
            continue  # 时间未到，丢弃本帧
        last_ts = now
        if (frame_id - 1) % max(1, sample_interval) != 0:
            continue

        results = model(frame, conf=conf_threshold, verbose=False)
        result = results[0]
        detections = []
        for box in result.boxes:
            cls_id = int(box.cls[0])
            name = model.names.get(cls_id, str(cls_id))
            score = float(box.conf[0])
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
            detections.append({
                "class_id": cls_id, "class_name": name,
                "confidence": round(score, 4),
                "bbox_xyxy": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            })

        payload = {"source": stream_url, "frame_id": frame_id,
                   "timestamp": int(time.time() * 1000), "detections": detections,
                   "stream_id": stream_id}
        if save_annotated:
            annotated = result.plot()
            out_path = out_dir / f"{stream_id}_frame_{frame_id}_{int(time.time() * 1000)}.jpg"
            try:
                cv2.imwrite(str(out_path), annotated)
                payload["annotated_image_path"] = str(out_path.resolve())
            except Exception:
                pass

        # ── 回调发送：任何失败都只计数 + 告警，绝不中断检测主循环 ──
        if time.time() >= _cb_paused_until:
            try:
                resp = requests.post(callback_url, json=payload, timeout=3)
                if resp.ok:
                    callbacks_sent += 1
                    _cb_fail_streak = 0
                else:
                    callbacks_failed += 1
                    _cb_fail_streak += 1
                    if _cb_fail_streak <= 3 or _cb_fail_streak % 50 == 0:
                        _log(f"回调返回异常状态 {resp.status_code}（累计失败 {callbacks_failed}，不影响检测）")
            except Exception as e:
                callbacks_failed += 1
                _cb_fail_streak += 1
                if _cb_fail_streak <= 3 or _cb_fail_streak % 50 == 0:
                    _log(f"回调失败（不影响检测）: {type(e).__name__}: {e}｜累计失败 {callbacks_failed}")
                if _cb_fail_streak >= 3:
                    # 熔断：回调地址不可用时暂停回调若干秒，保证检测不被网络超时拖慢
                    _cb_paused_until = time.time() + 5
                    if _cb_fail_streak == 3:
                        _log("回调连续失败，暂停回调 5 秒后重试（检测继续进行）")

        detections_total += len(detections)
        frames_processed += 1
        if frames_processed % 10 == 0:
            _log(f"已处理 {frames_processed} 帧 | 目标 {detections_total} | 回调成功 {callbacks_sent} 失败 {callbacks_failed} | 重连 {reconnects}")

    try:
        cap.release()
    except Exception:
        pass
    if marker and marker.exists():
        try:
            marker.unlink()
        except OSError:
            pass
    _log(f"worker 结束 stream={stream_id} frames={frames_processed} sent={callbacks_sent} failed={callbacks_failed} det={detections_total} reconnects={reconnects}")
    try:
        with open(str(out_dir / f"{stream_id}.done"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} stream={stream_id} "
                    f"frames={frames_processed} sent={callbacks_sent} "
                    f"failed={callbacks_failed} det={detections_total} "
                    f"reconnects={reconnects}\n")
    except Exception:
        pass


if __name__ == "__main__":
    import sys as _sys
    import argparse as _argparse
    if len(_sys.argv) > 1 and _sys.argv[1] == "--worker":
        # "--worker" 只是"以 worker 模式启动"的标识，未注册到 argparse。
        # 必须先把它从 argv 中摘掉，否则 parse_args 会报
        # "unrecognized arguments: --worker" 并直接退出（流 worker 秒退，检测循环从未执行）。
        _sys.argv = [_sys.argv[0]] + _sys.argv[2:]
        _ap = _argparse.ArgumentParser()
        _ap.add_argument("--params-file", required=True)
        _a = _ap.parse_args()
        try:
            _pw = json.loads(Path(_a.params_file).read_text(encoding="utf-8"))
        except Exception as _e:
            print(f"worker params load failed: {_e}", file=sys.stderr, flush=True)
            _sys.exit(1)
        # 子进程自行把 stdout/stderr 落盘到 .log（父进程给的是 DEVNULL）
        _redirect_to_log(_pw.get("log_path"))
        try:
            print(f"[worker] 启动 stream={_pw.get('stream_id')} url={_pw.get('stream_url')}", flush=True)
            _stream_worker(**_pw)
        finally:
            try:
                os.unlink(_a.params_file)
            except OSError:
                pass
