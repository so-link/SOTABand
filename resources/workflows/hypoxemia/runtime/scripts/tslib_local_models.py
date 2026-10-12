from __future__ import annotations

import sys
from pathlib import Path

import importlib

import torch
import torch.nn as nn
import torch.nn.functional as F

TSLIB_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "Time-Series-Library"
if TSLIB_ROOT.exists():
    tslib_root_text = str(TSLIB_ROOT)
    if tslib_root_text not in sys.path:
        sys.path.insert(0, tslib_root_text)

from layers.ClassificationHeads import masked_stats_features
from models._classification_hybrid_utils import DualBackboneFusionClassifier
 

def _load_model_class(name: str):
    module = importlib.import_module(f"models.{name}")
    return getattr(module, "Model")


class MLPClassificationHead(nn.Module):
    def __init__(self, in_dim: int, num_class: int, d_ff: int, dropout: float, depth: int = 1) -> None:
        super().__init__()
        hidden_dim = max(d_ff, in_dim // 2)
        layers: list[nn.Module] = [nn.LayerNorm(in_dim)]
        current_dim = in_dim
        for _ in range(max(depth - 1, 0)):
            layers.extend(
                [
                    nn.Linear(current_dim, hidden_dim),
                    nn.GELU(),
                    nn.Dropout(dropout),
                ]
            )
            current_dim = hidden_dim
        layers.append(nn.Linear(current_dim, num_class))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class SegRNNFeatureExtractor(nn.Module):
    def __init__(self, configs):
        super().__init__()
        self.base = _load_model_class("SegRNN")(configs)
        self.seg_len = self.base.seg_len
        self.seg_num_x = self.base.seg_num_x
        self.enc_in = self.base.enc_in
        self.d_model = self.base.d_model
        self.valueEmbedding = self.base.valueEmbedding
        self.rnn = self.base.rnn
        self.cls_channel_emb = nn.Parameter(torch.randn(self.enc_in, self.d_model))
        self.cls_token_norm = nn.LayerNorm(self.d_model)
        self.feature_scorer = nn.Sequential(
            nn.LayerNorm(self.d_model),
            nn.Linear(self.d_model, 1, bias=False),
        )
        self.out_dim = self.d_model * 4

    def forward_features(self, x_enc, x_mark_enc=None):
        batch_size = x_enc.size(0)
        seq_last = x_enc[:, -1:, :].detach()
        x = (x_enc - seq_last).permute(0, 2, 1)

        tokens = self.valueEmbedding(x.reshape(-1, self.seg_num_x, self.seg_len))
        tokens, _ = self.rnn(tokens)
        tokens = tokens.reshape(batch_size, self.enc_in, self.seg_num_x, self.d_model)

        channel_bias = self.cls_channel_emb.unsqueeze(1).expand(self.enc_in, self.seg_num_x, self.d_model)
        tokens = self.cls_token_norm(tokens + channel_bias.unsqueeze(0))
        tokens = tokens.reshape(batch_size, self.enc_in * self.seg_num_x, self.d_model)

        token_mask = None
        if x_mark_enc is not None:
            if x_mark_enc.dim() == 3 and x_mark_enc.size(-1) == 1:
                x_mark_enc = x_mark_enc.squeeze(-1)
            seg_mask = x_mark_enc.reshape(batch_size, self.seg_num_x, self.seg_len).amax(dim=-1)
            token_mask = seg_mask.unsqueeze(1).expand(batch_size, self.enc_in, self.seg_num_x).reshape(
                batch_size, self.enc_in * self.seg_num_x
            )

        return masked_stats_features(tokens, token_mask, self.feature_scorer)


class TransformerFeatureExtractor(nn.Module):
    def __init__(self, configs):
        super().__init__()
        self.base = _load_model_class("Transformer")(configs)
        self.enc_embedding = self.base.enc_embedding
        self.encoder = self.base.encoder
        self.feature_scorer = nn.Sequential(
            nn.LayerNorm(configs.d_model),
            nn.Linear(configs.d_model, 1, bias=False),
        )
        self.out_dim = configs.d_model * 4

    def forward_features(self, x_enc, x_mark_enc=None):
        enc_out = self.enc_embedding(x_enc, None)
        enc_out, _ = self.encoder(enc_out, attn_mask=None)
        return masked_stats_features(enc_out, x_mark_enc, self.feature_scorer)


class TimeMixerFeatureExtractor(nn.Module):
    def __init__(self, configs):
        super().__init__()
        self.base = _load_model_class("TimeMixer")(configs)
        self.layer = self.base.layer
        self.enc_embedding = self.base.enc_embedding
        self.pdm_blocks = self.base.pdm_blocks
        self.feature_scorer = nn.Sequential(
            nn.LayerNorm(configs.d_model),
            nn.Linear(configs.d_model, 1, bias=False),
        )
        self.out_dim = configs.d_model * 4

    def forward_features(self, x_enc, x_mark_enc=None):
        x_multi, _ = self.base._Model__multi_scale_process_inputs(x_enc, None)
        enc_out_list = []
        for x in x_multi:
            enc_out_list.append(self.enc_embedding(x, None))
        for i in range(self.layer):
            enc_out_list = self.pdm_blocks[i](enc_out_list)
        return masked_stats_features(enc_out_list[0], x_mark_enc, self.feature_scorer)


class iTransformerFeatureExtractor(nn.Module):
    def __init__(self, configs):
        super().__init__()
        self.base = _load_model_class("iTransformer")(configs)
        self.enc_embedding = self.base.enc_embedding
        self.encoder = self.base.encoder
        self.feature_scorer = nn.Sequential(
            nn.LayerNorm(configs.d_model),
            nn.Linear(configs.d_model, 1, bias=False),
        )
        self.out_dim = configs.d_model * 4

    def forward_features(self, x_enc, x_mark_enc=None):
        enc_out = self.enc_embedding(x_enc, None)
        enc_out, _ = self.encoder(enc_out, attn_mask=None)
        return masked_stats_features(enc_out, None, self.feature_scorer)


class MambaFeatureExtractor(nn.Module):
    def __init__(self, configs):
        super().__init__()
        self.base = _load_model_class("MambaSingleLayer")(configs)
        self.embedding = self.base.embedding
        self.mamba = self.base.mamba
        self.feature_scorer = nn.Sequential(
            nn.LayerNorm(configs.d_model),
            nn.Linear(configs.d_model, 1, bias=False),
        )
        self.out_dim = configs.d_model * 4

    def forward_features(self, x_enc, x_mark_enc=None):
        mamba_in = self.embedding(x_enc)
        mamba_out = self.mamba(mamba_in)
        return masked_stats_features(mamba_out, x_mark_enc, self.feature_scorer)


class FeatureClassifierBase(nn.Module):
    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        raise NotImplementedError


class SegRNNClsPoolModel(FeatureClassifierBase):
    def __init__(self, configs):
        super().__init__()
        self.backbone = SegRNNFeatureExtractor(configs)
        self.head = MLPClassificationHead(self.backbone.out_dim, configs.num_class, configs.d_ff, configs.dropout, depth=1)

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        return self.head(self.backbone.forward_features(x_enc, x_mark_enc))


class SegRNNDeepHeadModel(FeatureClassifierBase):
    def __init__(self, configs):
        super().__init__()
        self.backbone = SegRNNFeatureExtractor(configs)
        self.head = MLPClassificationHead(self.backbone.out_dim, configs.num_class, configs.d_ff, configs.dropout, depth=3)

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        return self.head(self.backbone.forward_features(x_enc, x_mark_enc))


class SegRNNHybridHeadModel(FeatureClassifierBase):
    def __init__(self, configs):
        super().__init__()
        self.backbone = SegRNNFeatureExtractor(configs)
        self.raw_project = nn.Sequential(
            nn.LayerNorm(configs.enc_in * 4),
            nn.Linear(configs.enc_in * 4, configs.d_model),
            nn.GELU(),
            nn.Dropout(configs.dropout),
        )
        self.head = MLPClassificationHead(self.backbone.out_dim + configs.d_model, configs.num_class, configs.d_ff, configs.dropout, depth=2)

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        pooled = self.backbone.forward_features(x_enc, x_mark_enc)
        raw_stats = torch.cat([x_enc.mean(dim=1), x_enc.amax(dim=1), x_enc.amin(dim=1), x_enc[:, -1, :]], dim=-1)
        aux = self.raw_project(raw_stats)
        return self.head(torch.cat([pooled, aux], dim=-1))


class TransformerClsPoolModel(FeatureClassifierBase):
    def __init__(self, configs):
        super().__init__()
        self.backbone = TransformerFeatureExtractor(configs)
        self.head = MLPClassificationHead(self.backbone.out_dim, configs.num_class, configs.d_ff, configs.dropout, depth=1)

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        return self.head(self.backbone.forward_features(x_enc, x_mark_enc))


class TimeMixerClsPoolModel(FeatureClassifierBase):
    def __init__(self, configs):
        super().__init__()
        self.backbone = TimeMixerFeatureExtractor(configs)
        self.head = MLPClassificationHead(self.backbone.out_dim, configs.num_class, configs.d_ff, configs.dropout, depth=1)

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        return self.head(self.backbone.forward_features(x_enc, x_mark_enc))


class ResidualAuxClassifier(nn.Module):
    def __init__(self, feature_dim: int, aux_dim: int, configs):
        super().__init__()
        self.aux_project = nn.Sequential(
            nn.LayerNorm(aux_dim),
            nn.Linear(aux_dim, configs.d_model),
            nn.GELU(),
            nn.Dropout(configs.dropout),
        )
        self.head = MLPClassificationHead(feature_dim + configs.d_model, configs.num_class, configs.d_ff, configs.dropout, depth=2)

    def forward(self, feature: torch.Tensor, aux: torch.Tensor) -> torch.Tensor:
        return self.head(torch.cat([feature, self.aux_project(aux)], dim=-1))


class StatsProjection(nn.Module):
    def __init__(self, input_dim: int, output_dim: int, dropout: float):
        super().__init__()
        self.net = nn.Sequential(
            nn.LayerNorm(input_dim),
            nn.Linear(input_dim, output_dim),
            nn.GELU(),
            nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def temporal_presence_mask(x_mark_enc: torch.Tensor | None, x_enc: torch.Tensor) -> torch.Tensor:
    if x_mark_enc is None:
        return torch.ones((x_enc.size(0), x_enc.size(1)), device=x_enc.device, dtype=x_enc.dtype)
    if x_mark_enc.dim() == 3:
        return x_mark_enc.squeeze(-1).float()
    return x_mark_enc.float()


def numeric_quality_stats(x_enc: torch.Tensor, x_mark_enc: torch.Tensor | None) -> torch.Tensor:
    time_mask = temporal_presence_mask(x_mark_enc, x_enc)
    recent_steps = max(1, x_enc.size(1) // 4)
    diffs = x_enc[:, 1:, :] - x_enc[:, :-1, :]
    diff_abs_mean = diffs.abs().mean(dim=(1, 2), keepdim=False).unsqueeze(-1)
    diff_std = diffs.std(dim=(1, 2), keepdim=False).unsqueeze(-1)
    seq_std = x_enc.std(dim=1).mean(dim=-1, keepdim=True)
    seq_amp = (x_enc.amax(dim=1) - x_enc.amin(dim=1)).mean(dim=-1, keepdim=True)
    coverage = time_mask.mean(dim=1, keepdim=True)
    recent_coverage = time_mask[:, -recent_steps:].mean(dim=1, keepdim=True)
    recent_std = x_enc[:, -recent_steps:, :].std(dim=1).mean(dim=-1, keepdim=True)
    quality = torch.cat(
        [
            coverage,
            recent_coverage,
            seq_std,
            recent_std,
            seq_amp,
            diff_abs_mean,
            diff_std,
        ],
        dim=-1,
    )
    return quality


def first_order_diff_sequence(x_enc: torch.Tensor) -> torch.Tensor:
    diffs = x_enc[:, 1:, :] - x_enc[:, :-1, :]
    pad = torch.zeros_like(x_enc[:, :1, :])
    return torch.cat([pad, diffs], dim=1)


def moving_average_sequence(x_enc: torch.Tensor, kernel_size: int = 9) -> torch.Tensor:
    if kernel_size <= 1:
        return x_enc
    pad = kernel_size // 2
    x = x_enc.permute(0, 2, 1)
    x = F.pad(x, (pad, pad), mode="replicate")
    trend = F.avg_pool1d(x, kernel_size=kernel_size, stride=1)
    return trend.permute(0, 2, 1)


class GatedBranchFusion(nn.Module):
    def __init__(self, branch_dims: list[int], configs, context_dim: int = 0):
        super().__init__()
        self.branch_count = len(branch_dims)
        self.projections = nn.ModuleList(
            [
                nn.Sequential(
                    nn.LayerNorm(branch_dim),
                    nn.Linear(branch_dim, configs.d_model),
                    nn.GELU(),
                    nn.Dropout(configs.dropout),
                )
                for branch_dim in branch_dims
            ]
        )
        gate_in_dim = configs.d_model * self.branch_count + context_dim
        hidden_dim = max(configs.d_model, gate_in_dim // 2)
        self.gate = nn.Sequential(
            nn.LayerNorm(gate_in_dim),
            nn.Linear(gate_in_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(configs.dropout),
            nn.Linear(hidden_dim, self.branch_count),
        )
        self.classifier = MLPClassificationHead(
            configs.d_model * self.branch_count,
            configs.num_class,
            configs.d_ff,
            configs.dropout,
            depth=2,
        )

    def forward(self, branches: list[torch.Tensor], context: torch.Tensor | None = None) -> torch.Tensor:
        projected = [proj(branch) for proj, branch in zip(self.projections, branches, strict=True)]
        gate_input = torch.cat(projected + ([context] if context is not None else []), dim=-1)
        gate_logits = self.gate(gate_input)
        gate_weights = torch.softmax(gate_logits, dim=-1)
        gated = [feature * gate_weights[:, idx : idx + 1] for idx, feature in enumerate(projected)]
        return self.classifier(torch.cat(gated, dim=-1))


class SegRNNResidualAuxModel(FeatureClassifierBase):
    def __init__(self, configs):
        super().__init__()
        self.backbone = SegRNNFeatureExtractor(configs)
        self.classifier = ResidualAuxClassifier(self.backbone.out_dim, configs.enc_in * 4, configs)

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        feature = self.backbone.forward_features(x_enc, x_mark_enc)
        residual = x_enc - x_enc.mean(dim=1, keepdim=True)
        aux = torch.cat([residual.mean(dim=1), residual.std(dim=1), x_enc.amax(dim=1), x_enc.amin(dim=1)], dim=-1)
        return self.classifier(feature, aux)


class SegRNNDualScaleModel(FeatureClassifierBase):
    def __init__(self, configs):
        super().__init__()
        self.backbone = SegRNNFeatureExtractor(configs)
        self.fusion = DualBackboneFusionClassifier(
            branch_dims=[self.backbone.out_dim, self.backbone.out_dim],
            num_class=configs.num_class,
            d_model=configs.d_model,
            d_ff=configs.d_ff,
            dropout=configs.dropout,
        )

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        fine_feature = self.backbone.forward_features(x_enc, x_mark_enc)
        coarse = F.avg_pool1d(x_enc.permute(0, 2, 1), kernel_size=2, stride=2, ceil_mode=True)
        coarse = F.interpolate(coarse, size=x_enc.size(1), mode="linear", align_corners=False).permute(0, 2, 1)
        coarse_mask = None
        if x_mark_enc is not None:
            coarse_mask = x_mark_enc
        coarse_feature = self.backbone.forward_features(coarse, coarse_mask)
        return self.fusion([fine_feature, coarse_feature])


class SegRNNRichFusionModel(FeatureClassifierBase):
    def __init__(self, configs):
        super().__init__()
        self.backbone = SegRNNFeatureExtractor(configs)
        self.stats_project = StatsProjection(configs.enc_in * 8, configs.d_model, configs.dropout)
        self.fusion = DualBackboneFusionClassifier(
            branch_dims=[
                self.backbone.out_dim,
                self.backbone.out_dim,
                self.backbone.out_dim,
                configs.d_model,
            ],
            num_class=configs.num_class,
            d_model=configs.d_model,
            d_ff=configs.d_ff,
            dropout=configs.dropout,
        )

    def _recent_branch(
        self,
        x_enc: torch.Tensor,
        x_mark_enc: torch.Tensor | None,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        recent_x = x_enc.clone()
        recent_mask = None if x_mark_enc is None else x_mark_enc.clone()
        cutoff = max(1, x_enc.size(1) // 2)
        recent_x[:, :-cutoff, :] = 0.0
        if recent_mask is not None:
            recent_mask[:, :-cutoff] = 0.0
        return recent_x, recent_mask

    def _stats_branch(self, x_enc: torch.Tensor) -> torch.Tensor:
        seq_mean = x_enc.mean(dim=1)
        seq_std = x_enc.std(dim=1)
        seq_max = x_enc.amax(dim=1)
        seq_last = x_enc[:, -1, :]
        residual = x_enc - seq_mean.unsqueeze(1)
        diffs = x_enc[:, 1:, :] - x_enc[:, :-1, :]
        diff_mean = diffs.mean(dim=1)
        diff_std = diffs.std(dim=1)
        residual_std = residual.std(dim=1)
        recent_mean = x_enc[:, -max(1, x_enc.size(1) // 4) :, :].mean(dim=1)
        stats = torch.cat(
            [
                seq_mean,
                seq_std,
                seq_max,
                seq_last,
                diff_mean,
                diff_std,
                residual_std,
                recent_mean,
            ],
            dim=-1,
        )
        return self.stats_project(stats)

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        fine_feature = self.backbone.forward_features(x_enc, x_mark_enc)

        coarse = F.avg_pool1d(x_enc.permute(0, 2, 1), kernel_size=2, stride=2, ceil_mode=True)
        coarse = F.interpolate(coarse, size=x_enc.size(1), mode="linear", align_corners=False).permute(0, 2, 1)
        coarse_feature = self.backbone.forward_features(coarse, x_mark_enc)

        recent_x, recent_mask = self._recent_branch(x_enc, x_mark_enc)
        recent_feature = self.backbone.forward_features(recent_x, recent_mask)

        stats_feature = self._stats_branch(x_enc)
        return self.fusion([fine_feature, coarse_feature, recent_feature, stats_feature])


class SegRNNDualScaleV2Model(FeatureClassifierBase):
    def __init__(self, configs):
        super().__init__()
        self.backbone = SegRNNFeatureExtractor(configs)
        self.stats_project = StatsProjection(7, configs.d_model, configs.dropout)
        self.fusion = GatedBranchFusion(
            [self.backbone.out_dim, self.backbone.out_dim, self.backbone.out_dim],
            configs,
            context_dim=configs.d_model,
        )

    def _coarse_branch(self, x_enc: torch.Tensor) -> torch.Tensor:
        coarse = F.avg_pool1d(x_enc.permute(0, 2, 1), kernel_size=4, stride=4, ceil_mode=True)
        return F.interpolate(coarse, size=x_enc.size(1), mode="linear", align_corners=False).permute(0, 2, 1)

    def _recent_branch(
        self,
        x_enc: torch.Tensor,
        x_mark_enc: torch.Tensor | None,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        recent_steps = max(1, x_enc.size(1) // 4)
        recent_x = torch.zeros_like(x_enc)
        recent_x[:, -recent_steps:, :] = x_enc[:, -recent_steps:, :]
        recent_mask = None if x_mark_enc is None else torch.zeros_like(x_mark_enc)
        if recent_mask is not None:
            recent_mask[:, -recent_steps:, ...] = x_mark_enc[:, -recent_steps:, ...]
        return recent_x, recent_mask

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        fine_feature = self.backbone.forward_features(x_enc, x_mark_enc)
        coarse_feature = self.backbone.forward_features(self._coarse_branch(x_enc), x_mark_enc)
        recent_x, recent_mask = self._recent_branch(x_enc, x_mark_enc)
        recent_feature = self.backbone.forward_features(recent_x, recent_mask)
        quality_context = self.stats_project(numeric_quality_stats(x_enc, x_mark_enc))
        return self.fusion([fine_feature, coarse_feature, recent_feature], quality_context)


class DualBackboneModelBase(nn.Module):
    def __init__(self, configs, left: nn.Module, right: nn.Module):
        super().__init__()
        self.left = left
        self.right = right
        self.fusion = DualBackboneFusionClassifier(
            branch_dims=[left.out_dim, right.out_dim],
            num_class=configs.num_class,
            d_model=configs.d_model,
            d_ff=configs.d_ff,
            dropout=configs.dropout,
        )

    def combine(self, left_feature: torch.Tensor, right_feature: torch.Tensor) -> torch.Tensor:
        return self.fusion([left_feature, right_feature])


class GatedDualBackboneModelBase(nn.Module):
    def __init__(self, configs, left: nn.Module, right: nn.Module, context_dim: int = 7):
        super().__init__()
        self.left = left
        self.right = right
        self.context_project = StatsProjection(context_dim, configs.d_model, configs.dropout)
        self.fusion = GatedBranchFusion(
            [left.out_dim, right.out_dim],
            configs,
            context_dim=configs.d_model,
        )

    def combine(self, x_enc: torch.Tensor, x_mark_enc: torch.Tensor | None) -> torch.Tensor:
        left_feature = self.left.forward_features(x_enc, x_mark_enc)
        right_feature = self.right.forward_features(x_enc, x_mark_enc)
        context = self.context_project(numeric_quality_stats(x_enc, x_mark_enc))
        return self.fusion([left_feature, right_feature], context)


class SegRNNTimeMixerFusionModel(DualBackboneModelBase):
    def __init__(self, configs):
        super().__init__(configs, SegRNNFeatureExtractor(configs), TimeMixerFeatureExtractor(configs))

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        return self.combine(self.left.forward_features(x_enc, x_mark_enc), self.right.forward_features(x_enc, x_mark_enc))


class SegRNNiTransformerFusionModel(DualBackboneModelBase):
    def __init__(self, configs):
        super().__init__(configs, SegRNNFeatureExtractor(configs), iTransformerFeatureExtractor(configs))

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        return self.combine(self.left.forward_features(x_enc, x_mark_enc), self.right.forward_features(x_enc, x_mark_enc))


class SegRNNMambaFusionModel(DualBackboneModelBase):
    def __init__(self, configs):
        super().__init__(configs, SegRNNFeatureExtractor(configs), MambaFeatureExtractor(configs))

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        return self.combine(self.left.forward_features(x_enc, x_mark_enc), self.right.forward_features(x_enc, x_mark_enc))


class SegRNNTimeMixerGateModel(GatedDualBackboneModelBase):
    def __init__(self, configs):
        super().__init__(configs, SegRNNFeatureExtractor(configs), TimeMixerFeatureExtractor(configs))

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        return self.combine(x_enc, x_mark_enc)


class SegRNNiTransformerGateModel(GatedDualBackboneModelBase):
    def __init__(self, configs):
        super().__init__(configs, SegRNNFeatureExtractor(configs), iTransformerFeatureExtractor(configs))

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        return self.combine(x_enc, x_mark_enc)


class TimeMixeriTransformerFusionModel(DualBackboneModelBase):
    def __init__(self, configs):
        super().__init__(configs, TimeMixerFeatureExtractor(configs), iTransformerFeatureExtractor(configs))

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        return self.combine(self.left.forward_features(x_enc, x_mark_enc), self.right.forward_features(x_enc, x_mark_enc))


class SegRNNNumericQualityAwareModel(FeatureClassifierBase):
    def __init__(self, configs):
        super().__init__()
        self.backbone = SegRNNFeatureExtractor(configs)
        self.stats_project = StatsProjection(configs.enc_in * 8, configs.d_model, configs.dropout)
        self.quality_project = StatsProjection(7, configs.d_model, configs.dropout)
        self.fusion = GatedBranchFusion(
            [self.backbone.out_dim, self.backbone.out_dim, configs.d_model],
            configs,
            context_dim=configs.d_model,
        )

    def _recent_branch(
        self,
        x_enc: torch.Tensor,
        x_mark_enc: torch.Tensor | None,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        recent_steps = max(1, x_enc.size(1) // 4)
        recent_x = x_enc[:, -recent_steps:, :]
        if recent_steps == x_enc.size(1):
            padded_x = recent_x
        else:
            pad = recent_x[:, :1, :].expand(-1, x_enc.size(1) - recent_steps, -1)
            padded_x = torch.cat([pad, recent_x], dim=1)
        recent_mask = None
        if x_mark_enc is not None:
            recent_mask = x_mark_enc[:, -recent_steps:, ...]
            if recent_steps != x_enc.size(1):
                pad_mask = recent_mask[:, :1, ...].expand(-1, x_enc.size(1) - recent_steps, *recent_mask.shape[2:])
                recent_mask = torch.cat([pad_mask, recent_mask], dim=1)
        return padded_x, recent_mask

    def _stats_branch(self, x_enc: torch.Tensor) -> torch.Tensor:
        seq_mean = x_enc.mean(dim=1)
        seq_std = x_enc.std(dim=1)
        seq_max = x_enc.amax(dim=1)
        seq_last = x_enc[:, -1, :]
        diff = x_enc[:, 1:, :] - x_enc[:, :-1, :]
        diff_mean = diff.mean(dim=1)
        diff_std = diff.std(dim=1)
        recent_steps = max(1, x_enc.size(1) // 4)
        recent_mean = x_enc[:, -recent_steps:, :].mean(dim=1)
        recent_std = x_enc[:, -recent_steps:, :].std(dim=1)
        stats = torch.cat(
            [seq_mean, seq_std, seq_max, seq_last, diff_mean, diff_std, recent_mean, recent_std],
            dim=-1,
        )
        return self.stats_project(stats)

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        base_feature = self.backbone.forward_features(x_enc, x_mark_enc)
        recent_x, recent_mask = self._recent_branch(x_enc, x_mark_enc)
        recent_feature = self.backbone.forward_features(recent_x, recent_mask)
        stats_feature = self._stats_branch(x_enc)
        quality_context = self.quality_project(numeric_quality_stats(x_enc, x_mark_enc))
        return self.fusion([base_feature, recent_feature, stats_feature], quality_context)


class SegRNNDiffFusionModel(FeatureClassifierBase):
    def __init__(self, configs):
        super().__init__()
        self.backbone = SegRNNFeatureExtractor(configs)
        self.context_project = StatsProjection(7, configs.d_model, configs.dropout)
        self.fusion = GatedBranchFusion(
            [self.backbone.out_dim, self.backbone.out_dim],
            configs,
            context_dim=configs.d_model,
        )

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        base_feature = self.backbone.forward_features(x_enc, x_mark_enc)
        diff_feature = self.backbone.forward_features(first_order_diff_sequence(x_enc), x_mark_enc)
        context = self.context_project(numeric_quality_stats(x_enc, x_mark_enc))
        return self.fusion([base_feature, diff_feature], context)


class SegRNNTrendResidualFusionModel(FeatureClassifierBase):
    def __init__(self, configs):
        super().__init__()
        self.backbone = SegRNNFeatureExtractor(configs)
        self.context_project = StatsProjection(7, configs.d_model, configs.dropout)
        self.fusion = GatedBranchFusion(
            [self.backbone.out_dim, self.backbone.out_dim, self.backbone.out_dim],
            configs,
            context_dim=configs.d_model,
        )

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        trend_x = moving_average_sequence(x_enc, kernel_size=11)
        residual_x = x_enc - trend_x
        base_feature = self.backbone.forward_features(x_enc, x_mark_enc)
        trend_feature = self.backbone.forward_features(trend_x, x_mark_enc)
        residual_feature = self.backbone.forward_features(residual_x, x_mark_enc)
        context = self.context_project(numeric_quality_stats(x_enc, x_mark_enc))
        return self.fusion([base_feature, trend_feature, residual_feature], context)


class SegRNNDiffTimeMixerGateModel(FeatureClassifierBase):
    def __init__(self, configs):
        super().__init__()
        self.left = SegRNNFeatureExtractor(configs)
        self.right = TimeMixerFeatureExtractor(configs)
        self.context_project = StatsProjection(7, configs.d_model, configs.dropout)
        self.fusion = GatedBranchFusion(
            [self.left.out_dim, self.right.out_dim],
            configs,
            context_dim=configs.d_model,
        )

    def forward(self, x_enc, x_mark_enc, x_dec, x_mark_dec, mask=None):
        diff_x = first_order_diff_sequence(x_enc)
        left_feature = self.left.forward_features(x_enc, x_mark_enc)
        right_feature = self.right.forward_features(diff_x, x_mark_enc)
        context = self.context_project(numeric_quality_stats(x_enc, x_mark_enc))
        return self.fusion([left_feature, right_feature], context)


LOCAL_TSLIB_MODEL_REGISTRY = {
    'SegRNNClsPool': SegRNNClsPoolModel,
    'SegRNNDeepHead': SegRNNDeepHeadModel,
    'SegRNNHybridHead': SegRNNHybridHeadModel,
    'TransformerClsPool': TransformerClsPoolModel,
    'TimeMixerClsPool': TimeMixerClsPoolModel,
    'SegRNNTimeMixerFusion': SegRNNTimeMixerFusionModel,
    'SegRNNTimeMixerGate': SegRNNTimeMixerGateModel,
    'SegRNNiTransformerFusion': SegRNNiTransformerFusionModel,
    'SegRNNiTransformerGate': SegRNNiTransformerGateModel,
    'SegRNNMambaFusion': SegRNNMambaFusionModel,
    'TimeMixeriTransformerFusion': TimeMixeriTransformerFusionModel,
    'SegRNNResidualAux': SegRNNResidualAuxModel,
    'SegRNNDualScale': SegRNNDualScaleModel,
    'SegRNNDualScaleV2': SegRNNDualScaleV2Model,
    'SegRNNDiffFusion': SegRNNDiffFusionModel,
    'SegRNNTrendResidualFusion': SegRNNTrendResidualFusionModel,
    'SegRNNDiffTimeMixerGate': SegRNNDiffTimeMixerGateModel,
    'SegRNNRichFusion': SegRNNRichFusionModel,
    'SegRNNNumericQualityAware': SegRNNNumericQualityAwareModel,
}
