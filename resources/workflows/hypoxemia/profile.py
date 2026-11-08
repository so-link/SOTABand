"""Low-oxygen domain metadata kept outside the platform Agent."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HypoxemiaProfile:
    workflow_id: str = "hypoxemia-prediction"
    tool_id: str = "timeseries-classification-runner"
    feature_order: tuple[str, ...] = ("SpO2", "HR", "PR", "RR", "PI")
    sequence_length: int = 256
    sampling_hz: int = 1
    domain_markers: tuple[str, ...] = field(
        default=(
            "\u4f4e\u6c27", "\u4f4e\u6c27\u8840\u75c7", "\u4f4e\u6c27\u9884\u6d4b",
            "\u4f4e\u6c27\u68c0\u6d4b", "\u8840\u6c27", "\u4e94\u901a\u9053", "\u751f\u7406\u4fe1\u53f7",
            "低氧",
            "低氧血症",
            "低氧预测",
            "低氧检测",
            "血氧",
            "五通道",
            "生理信号",
            "spo2",
            "hypoxemia",
            "hypoxaemia",
            "256x5",
            "segrnn",
        )
    )
