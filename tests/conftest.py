"""Shared fixtures: one generated season, reused across tests."""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rating.engine import EngineConfig, ModelSpec  # noqa: E402
from rating.generator import generate_dataset  # noqa: E402
from rating.model import dedupe_matches  # noqa: E402


@pytest.fixture(scope="session")
def dataset():
    return generate_dataset(seed=75826)


@pytest.fixture(scope="session")
def prepared(dataset):
    clean, duplicates, conflicts = dedupe_matches(dataset.matches)
    clean.sort(key=lambda m: (m.day, m.id))
    config = EngineConfig(base_goals=dataset.base_goals)
    player_ids = [p.id for p in dataset.players]
    empty = ModelSpec(player_ids, [], config)
    obs = [empty.obs_for(m) for m in clean]
    spec = ModelSpec(player_ids, obs, config)
    obs = [spec.obs_for(m) for m in clean]
    return {
        "dataset": dataset,
        "clean": clean,
        "duplicates": duplicates,
        "conflicts": conflicts,
        "config": config,
        "spec": spec,
        "obs": obs,
        "versions": {m.id: 0 for m in clean},
    }
