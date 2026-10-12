from __future__ import annotations

import torch


def _normalize_mask(mask: torch.Tensor | None, token_count: int, *, device: torch.device) -> torch.Tensor | None:
    if mask is None:
        return None
    if mask.dim() == 3 and mask.size(-1) == 1:
        mask = mask.squeeze(-1)
    if mask.dim() != 2:
        raise ValueError(f"expected mask with shape [batch, tokens], got {tuple(mask.shape)}")
    if mask.size(1) != token_count:
        raise ValueError(f"mask token count {mask.size(1)} != feature token count {token_count}")
    return mask.to(device=device, dtype=torch.float32)


def masked_stats_features(
    tokens: torch.Tensor,
    mask: torch.Tensor | None,
    scorer: torch.nn.Module | None = None,
) -> torch.Tensor:
    if tokens.dim() != 3:
        raise ValueError(f"expected tokens with shape [batch, tokens, dim], got {tuple(tokens.shape)}")

    batch_size, token_count, feature_dim = tokens.shape
    mask = _normalize_mask(mask, token_count, device=tokens.device)
    if mask is None:
        mask = torch.ones((batch_size, token_count), device=tokens.device, dtype=tokens.dtype)
    else:
        mask = mask.to(dtype=tokens.dtype)

    weights = mask.unsqueeze(-1)
    denom = mask.sum(dim=1, keepdim=True).clamp_min(1.0)
    mean_feature = (tokens * weights).sum(dim=1) / denom

    masked_max = tokens.masked_fill(mask.unsqueeze(-1) <= 0, float("-inf"))
    max_feature = masked_max.amax(dim=1)
    max_feature = torch.where(torch.isfinite(max_feature), max_feature, mean_feature)

    masked_min = tokens.masked_fill(mask.unsqueeze(-1) <= 0, float("inf"))
    min_feature = masked_min.amin(dim=1)
    min_feature = torch.where(torch.isfinite(min_feature), min_feature, mean_feature)

    if scorer is None:
        score_logits = torch.zeros((batch_size, token_count), device=tokens.device, dtype=tokens.dtype)
    else:
        score_logits = scorer(tokens).squeeze(-1)
    score_logits = score_logits.masked_fill(mask <= 0, -1e9)
    attn = torch.softmax(score_logits, dim=1)
    attn_feature = torch.bmm(attn.unsqueeze(1), tokens).squeeze(1)

    return torch.cat([mean_feature, max_feature, min_feature, attn_feature], dim=-1).reshape(batch_size, feature_dim * 4)
