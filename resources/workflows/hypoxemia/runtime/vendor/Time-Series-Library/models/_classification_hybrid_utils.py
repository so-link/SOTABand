from __future__ import annotations

import torch
import torch.nn as nn


class DualBackboneFusionClassifier(nn.Module):
    def __init__(
        self,
        branch_dims: list[int],
        num_class: int,
        d_model: int,
        d_ff: int,
        dropout: float,
    ) -> None:
        super().__init__()
        if len(branch_dims) < 2:
            raise ValueError("DualBackboneFusionClassifier requires at least two branches")
        self.branch_projections = nn.ModuleList(
            [
                nn.Sequential(
                    nn.LayerNorm(branch_dim),
                    nn.Linear(branch_dim, d_model),
                    nn.GELU(),
                    nn.Dropout(dropout),
                )
                for branch_dim in branch_dims
            ]
        )
        self.branch_gates = nn.ModuleList([nn.Linear(d_model, 1) for _ in branch_dims])
        self.classifier = nn.Sequential(
            nn.LayerNorm(d_model * len(branch_dims)),
            nn.Linear(d_model * len(branch_dims), d_ff),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(d_ff, num_class),
        )

    def forward(self, branch_features: list[torch.Tensor]) -> torch.Tensor:
        if len(branch_features) != len(self.branch_projections):
            raise ValueError("branch feature count does not match configured branch count")
        projected = [proj(feature) for proj, feature in zip(self.branch_projections, branch_features)]
        gate_logits = torch.cat([gate(feature) for gate, feature in zip(self.branch_gates, projected)], dim=-1)
        gate_weights = torch.softmax(gate_logits, dim=-1)
        gated = [feature * gate_weights[:, idx : idx + 1] for idx, feature in enumerate(projected)]
        fused = torch.cat(gated, dim=-1)
        return self.classifier(fused)
