from __future__ import annotations

import numpy as np
import torch

from market_forecast.config import ModelConfig
from market_forecast.gru_model import predict_probabilities, train_gru


def test_same_seed_reproduces_gru_probabilities() -> None:
    rng = np.random.default_rng(123)
    train_x = rng.normal(size=(32, 8, 4)).astype(np.float32)
    train_y = np.array([0, 1] * 16, dtype=np.int64)
    validation_x = rng.normal(size=(12, 8, 4)).astype(np.float32)
    validation_y = np.array([0, 1] * 6, dtype=np.int64)
    config = ModelConfig(
        hidden_size=4,
        num_layers=1,
        dropout=0.0,
        batch_size=8,
        max_epochs=2,
        early_stopping_patience=2,
    )
    device = torch.device("cpu")

    first = train_gru(train_x, train_y, validation_x, validation_y, config=config, seed=42, device=device)
    first_probabilities = predict_probabilities(first.model, validation_x, device)
    second = train_gru(train_x, train_y, validation_x, validation_y, config=config, seed=42, device=device)
    second_probabilities = predict_probabilities(second.model, validation_x, device)

    assert np.allclose(first_probabilities, second_probabilities, rtol=0, atol=1e-7)
