TIMESERIES_MODEL_NAMES = {
    "Autoformer", "Crossformer", "DLinear", "ETSformer", "FEDformer", "FiLM", "Informer",
    "KANAD", "LightTS", "MICN", "MSGNet", "MambaSingleLayer", "MultiPatchFormer",
    "Nonstationary_Transformer", "PatchTST", "Pyraformer", "Reformer", "SegRNN", "TiDE",
    "TimeFilter", "TimeMixer", "TimesNet", "Transformer", "WPMixer", "iTransformer",
    "SegRNNClsPool", "SegRNNDeepHead", "SegRNNDiffFusion", "SegRNNDiffTimeMixerGate",
    "SegRNNHybridHead", "SegRNNiTransformerFusion", "SegRNNiTransformerGate", "SegRNNMambaFusion",
    "SegRNNNumericQualityAware", "SegRNNResidualAux", "SegRNNRichFusion", "SegRNNTimeMixerFusion",
    "SegRNNTimeMixerGate", "SegRNNTrendResidualFusion", "SegRNNDualScale", "SegRNNDualScaleV2",
    "TimeMixerClsPool", "TimeMixeriTransformerFusion", "TransformerClsPool",
}

TIMESERIES_DEFAULT_MODEL_CONFIG = {
    "d_model": 128, "n_heads": 4, "e_layers": 3, "d_layers": 3, "d_ff": 256,
    "dropout": 0.15, "activation": "gelu", "patch_len": 16, "stride": 8,
    "top_k": 3, "num_kernels": 6, "moving_avg": 25, "seg_len": 32,
    "p_hidden_dims": [128, 128], "p_hidden_layers": 2, "embed": "fixed", "freq": "h",
    "factor": 1, "distil": True, "individual": False, "channel_independence": 0,
    "down_sampling_layers": 0, "down_sampling_window": 1, "down_sampling_method": "avg",
    "use_norm": 1, "decomp_method": "moving_avg", "alpha": 0.1, "top_p": 0.5,
    "pos": 1, "node_dim": 10, "gcn_depth": 2, "propalpha": 0.3,
    "conv_channel": 32, "skip_channel": 32, "subgraph_size": 5, "ratio": 0.5,
    "expand": 2, "d_conv": 4,
}

# These upstream implementations explicitly do not implement classification.
UNSUPPORTED_CLASSIFICATION_MODEL_NAMES = {
    "KANAD", "MultiPatchFormer", "TiDE", "WPMixer",
}
MAMBA_MODEL_NAMES = {"MambaSingleLayer", "SegRNNMambaFusion"}

MULTISCALE_MODEL_NAMES = frozenset({
    "TimeMixer", "TimeMixerClsPool", "TimeMixeriTransformerFusion",
    "SegRNNDiffTimeMixerGate", "SegRNNTimeMixerFusion", "SegRNNTimeMixerGate",
})


def model_architecture_defaults(model_name: str) -> dict:
    if model_name in MULTISCALE_MODEL_NAMES:
        return {"down_sampling_layers": 2, "down_sampling_window": 2}
    return {}

TIMESERIES_DEFAULT_TRAINING_CONFIG = {
    "epochs": 140, "batch_size": 32, "learning_rate": 0.00025, "optimizer": "adamw",
    "seed": 42, "weight_decay": 0.00005, "pos_weight_scale": 0.7, "use_ema": True,
    "ema_momentum": 0.99, "label_smoothing": 0.015, "grad_clip_norm": 4.0,
    "early_stop_patience": 35, "early_stop_start_epoch": 25, "reduce_lr_patience": 4,
    "reduce_lr_factor": 0.5, "min_lr": 0.000005, "selection_monitor": "auc",
    "lr_monitor": "loss", "threshold_metric": "f1",
}

EVALUATION_METRICS = (
    "auc", "auprc", "f1", "accuracy", "balanced_accuracy", "precision", "recall",
    "specificity", "sensitivity", "npv", "ppv", "mcc", "loss", "threshold",
)

RAW_PROCESSING_PARAMETER_DEFS = (
    ("configs", "窗口配置（LOOKBACK:INTERVAL）", "[\"256:30\"]", "例如 [\"180:60\"]；同时设置窗口长度和预测间隔"),
    ("min_segment_seconds", "最短监护记录", 600, "短于此时长的记录会被丢弃"),
    ("spo2_threshold", "低氧 SpO2 阈值", 94.0, "SpO2 小于或等于此值判定为低氧"),
    ("hypo_operator", "低氧比较方式", "le", "le 表示 <=，lt 表示 <"),
    ("hypoxemia_duration_seconds", "连续低氧时长", 20, "连续达到阈值的最短秒数"),
    ("min_hypo_duration_seconds", "窗口内最短低氧时长", 20, "低氧窗口保留条件"),
    ("max_non_hypo_ratio", "非低氧/低氧最大比例", 2.0, "控制类别平衡；1.0 表示 1:1"),
    ("nan_ratio_threshold", "缺失值比例上限", 0.1, "超过此比例的记录会被丢弃，范围 0-1"),
    ("pi_threshold", "PI 质量阈值", 0.3, "低于此 PI 的信号质量较差"),
    ("low_pi_ratio_threshold", "低 PI 比例上限", 0.3, "低质量 PI 占比上限，范围 0-1"),
    ("drop_spo2_na_before_segment", "分段前删除 SpO2 缺失行", False, "布尔值"),
    ("spo2_na_break_segment", "SpO2 缺失是否切断分段", True, "布尔值"),
    ("crop_seconds", "边缘裁剪时长", 30, "记录两端裁剪秒数"),
    ("edge_trim_mode", "边缘裁剪方式", "zhang", "当前支持 zhang"),
    ("fill_method", "缺失值填充方式", "interpolate", "插值方式"),
    ("apply_physiologic_bounds", "应用生理范围裁剪", True, "布尔值"),
    ("preprocess_hypo_operator", "预处理低氧比较方式", "lt", "le 或 lt"),
    ("window_hypo_operator", "窗口标签比较方式", "le", "le 表示 <=，lt 表示 <"),
    ("seed", "随机种子", 42, "用于可复现采样"),
)

RAW_PROCESSING_PARAMETER_LIMITS = {
    "configs": {"description": "格式为 LOOKBACK:INTERVAL，例如 256:30"},
    "min_segment_seconds": {"min": 0, "max": 86400},
    "spo2_threshold": {"min": 0, "max": 100, "step": 0.1},
    "hypoxemia_duration_seconds": {"min": 0, "max": 86400},
    "min_hypo_duration_seconds": {"min": 0, "max": 86400},
    "max_non_hypo_ratio": {"min": 0.01, "max": 100, "step": 0.01},
    "nan_ratio_threshold": {"min": 0, "max": 1, "step": 0.01},
    "pi_threshold": {"min": 0, "max": 100, "step": 0.01},
    "low_pi_ratio_threshold": {"min": 0, "max": 1, "step": 0.01},
    "crop_seconds": {"min": 0, "max": 86400},
    "seed": {"min": 0, "max": 2147483647},
}

