from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from market_forecast.config import ModelConfig


class GRUDirectionClassifier(nn.Module):
    def __init__(self, input_size: int, config: ModelConfig) -> None:
        super().__init__()
        self.gru = nn.GRU(
            input_size=input_size,
            hidden_size=config.hidden_size,
            num_layers=config.num_layers,
            dropout=config.dropout if config.num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.output = nn.Linear(config.hidden_size, config.output_size)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        sequence, _ = self.gru(values)
        return self.output(sequence[:, -1, :]).squeeze(-1)


@dataclass(frozen=True)
class TrainingResult:
    model: GRUDirectionClassifier
    best_epoch: int
    best_validation_loss: float
    epochs_trained: int
    positive_weight: float
    train_positive_ratio: float
    history: tuple[dict[str, float], ...]


def resolve_device(requested: str) -> torch.device:
    normalized = requested.lower()
    if normalized == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA_REQUESTED_BUT_NOT_AVAILABLE")
        return torch.device("cuda")
    if normalized == "auto" and torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def set_reproducible_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def train_gru(
    train_values: np.ndarray,
    train_targets: np.ndarray,
    validation_values: np.ndarray,
    validation_targets: np.ndarray,
    *,
    config: ModelConfig,
    seed: int,
    device: torch.device,
) -> TrainingResult:
    if not len(train_targets) or not len(validation_targets):
        raise ValueError("Training and validation sets must be non-empty")
    positives = int(train_targets.sum())
    negatives = int(len(train_targets) - positives)
    if positives == 0 or negatives == 0:
        raise ValueError("Training fold contains only one class")
    set_reproducible_seed(seed)
    positive_weight = negatives / positives
    model = GRUDirectionClassifier(train_values.shape[-1], config).to(device)
    loss_function = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(positive_weight, dtype=torch.float32, device=device))
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    train_dataset = TensorDataset(
        torch.from_numpy(train_values.astype(np.float32)),
        torch.from_numpy(train_targets.astype(np.float32)),
    )
    train_loader = DataLoader(
        train_dataset,
        batch_size=config.batch_size,
        shuffle=False,
        num_workers=0,
        drop_last=False,
    )
    validation_x = torch.from_numpy(validation_values.astype(np.float32)).to(device)
    validation_y = torch.from_numpy(validation_targets.astype(np.float32)).to(device)
    best_state: dict[str, Any] | None = None
    best_epoch = 0
    best_validation_loss = float("inf")
    stale_epochs = 0
    history: list[dict[str, float]] = []
    for epoch in range(1, config.max_epochs + 1):
        model.train()
        total_loss = 0.0
        total_count = 0
        for batch_x, batch_y in train_loader:
            batch_x = batch_x.to(device)
            batch_y = batch_y.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = model(batch_x)
            loss = loss_function(logits, batch_y)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=config.gradient_clip_norm)
            optimizer.step()
            total_loss += float(loss.detach().cpu()) * len(batch_y)
            total_count += len(batch_y)
        model.eval()
        with torch.no_grad():
            validation_loss = float(loss_function(model(validation_x), validation_y).detach().cpu())
        train_loss = total_loss / max(total_count, 1)
        history.append({"epoch": float(epoch), "train_loss": train_loss, "validation_loss": validation_loss})
        if validation_loss < best_validation_loss - 1e-8:
            best_validation_loss = validation_loss
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            stale_epochs = 0
        else:
            stale_epochs += 1
        if stale_epochs >= config.early_stopping_patience:
            break
    if best_state is None:
        raise RuntimeError("GRU_TRAINING_DID_NOT_PRODUCE_A_CHECKPOINT")
    model.load_state_dict(best_state)
    return TrainingResult(
        model=model,
        best_epoch=best_epoch,
        best_validation_loss=best_validation_loss,
        epochs_trained=len(history),
        positive_weight=float(positive_weight),
        train_positive_ratio=float(positives / len(train_targets)),
        history=tuple(history),
    )


def predict_probabilities(model: GRUDirectionClassifier, values: np.ndarray, device: torch.device) -> np.ndarray:
    model.eval()
    with torch.no_grad():
        logits = model(torch.from_numpy(values.astype(np.float32)).to(device))
        probabilities = torch.sigmoid(logits).detach().cpu().numpy()
    return probabilities.astype(float)
