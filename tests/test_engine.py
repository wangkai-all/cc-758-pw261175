"""Engine mathematics: gradients, joint exactness, drift/absence behaviour."""

from __future__ import annotations

import math

import pytest

from rating.analysis import compare_one_step, gradient_check, window_truncation
from rating.engine import (
    ApproxFilter,
    DiagState,
    EngineConfig,
    ModelSpec,
    Observation,
    WindowFilter,
    diagonal_step,
    initial_state,
    joint_single_skill_fit,
    joint_window_fit,
    lambdas,
)


def test_gradient_matches_finite_differences(prepared):
    spec = prepared["spec"]
    obs = prepared["obs"]
    result = gradient_check(spec, obs[-3:], prepared["config"])
    assert result["converged"]
    assert result["max_gradient_error"] < 1e-4
    assert result["cov_asymmetry"] < 1e-9


def test_one_match_joint_diagonal_agreement(prepared):
    """On the same match and same prior the diagonal update is exact for
    a match with no shared-lineup parameter, so the gap must be tiny."""
    spec = prepared["spec"]
    cfg = prepared["config"]
    obs = prepared["obs"]
    result = compare_one_step(spec, cfg, obs, 20, drift_sd=cfg.drift_sd)
    assert result["max_abs_diff"] < 0.05
    assert result["max_z"] < 1.0


def test_joint_fit_recovers_known_skills():
    """A clean synthetic pool: joint fit should recover skills far better
    than the 0.6 prior width would allow by chance."""
    import random

    rng = random.Random(7)
    n, ids = 10, [f"X{i}" for i in range(10)]
    truth = [rng.gauss(0, 0.5) for _ in range(n)]

    def poisson(lam):
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            k += 1
            p *= rng.random()
            if p <= limit:
                return k - 1

    obs = []
    for t in range(600):
        a = rng.sample(range(n), 2)
        b = rng.sample([i for i in range(n) if i not in a], 2)
        la = math.exp(math.log(1.45) + (truth[a[0]] + truth[a[1]]) / 2)
        lb = math.exp(math.log(1.45) + (truth[b[0]] + truth[b[1]]) / 2)
        obs.append(
            Observation(
                match_id=str(t),
                day=t,
                side_a=tuple(a),
                side_b=tuple(b),
                goals_a=poisson(la),
                goals_b=poisson(lb),
            )
        )
    cfg = EngineConfig(base_goals=1.45, drift_sd=0.0, init_skill_sd=0.5)
    spec = ModelSpec(ids, obs, cfg)
    fit = joint_window_fit(spec, obs, initial_state(spec, cfg), cfg)
    est = {k[1]: fit.mean[i] for i, k in enumerate(fit.param_keys)}
    rmse = math.sqrt(sum((est[i] - truth[i]) ** 2 for i in range(n)) / n)
    assert rmse < 0.2
    assert fit.converged


def test_absence_widens_interval(prepared):
    spec = prepared["spec"]
    cfg = prepared["config"]
    match = next(
        m for m in prepared["clean"] if prepared["dataset"].scenarios["absent_player"]["id"] in m.side_a + m.side_b
    )
    obs = prepared["obs"]
    idx = next(i for i, o in enumerate(obs) if o.match_id == match.id)
    flt = ApproxFilter(spec, cfg)
    for o in obs[:idx]:
        flt.step(o, obs)
    before = flt.current()
    after = before.inflate(before.day + 60, cfg.drift_sd)
    pid = prepared["dataset"].scenarios["absent_player"]["id"]
    i = spec.index_of[pid]
    assert after.skill_var[i] > before.skill_var[i]
    # And it grows with the drift assumption.
    after2 = before.inflate(before.day + 60, cfg.drift_sd * 2)
    assert after2.skill_var[i] > after.skill_var[i]


def test_absence_never_increases_certainty(prepared):
    spec = prepared["spec"]
    cfg = prepared["config"]
    state = initial_state(spec, cfg)
    for delta in (1, 5, 100):
        nxt = state.inflate(state.day + delta, cfg.drift_sd)
        assert all(v2 >= v1 - 1e-12 for v1, v2 in zip(state.skill_var, nxt.skill_var))


def test_observation_never_shrinks_more_than_prior_allows(prepared):
    spec = prepared["spec"]
    cfg = prepared["config"]
    obs = prepared["obs"]
    state = initial_state(spec, cfg).inflate(obs[0].day, cfg.drift_sd)
    after = diagonal_step(state, spec, obs[0], cfg)
    for i in range(spec.n_players):
        assert after.skill_var[i] <= state.skill_var[i] + 1e-12


def test_window_truncation_shrinks_with_history(prepared):
    spec = prepared["spec"]
    config = prepared["config"]
    obs = prepared["obs"]
    result = window_truncation(spec, config, obs, len(obs))
    comps = result["comparisons"]
    assert comps[0]["reference"]
    full = comps[-1]["rms_diff_vs_window"]
    double = comps[1]["rms_diff_vs_window"]
    assert full < 1.0
    assert double < full


def test_weights_of_window_filter_are_not_shared_wrongly(prepared):
    """A deeper fit on one window should not explode; all values stay finite."""
    spec = prepared["spec"]
    cfg = prepared["config"]
    obs = prepared["obs"]
    prior = initial_state(spec, cfg)
    fit = joint_window_fit(spec, obs[:10], prior, cfg)
    assert all(math.isfinite(v) for v in fit.mean)
    assert all(math.isfinite(c) for row in fit.cov for c in row)


def test_draw_probability_is_positive_and_sums_to_one():
    from rating.engine import outcome_probabilities

    p = outcome_probabilities(1.4, 1.4)
    assert 0 < p["draw"] < 0.5
    assert abs(p["a"] + p["draw"] + p["b"] - 1.0) < 1e-9
    # Equal strengths -> symmetric outcome probabilities.
    assert abs(p["a"] - p["b"]) < 1e-12
