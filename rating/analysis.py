"""Evaluation against simulated truth, and exact small-window checks.

Questions answered with numbers rather than adjectives:

1. How close is each method to the generative truth, and do its 90% intervals
   cover the truth at the stated rate?  (``score_run``)
2. How fast does each method respond to a known form change?
   (``response_to_shock``, clocked in the player's own games)
3. Is the joint fit really solving the window?  (``gradient_check`` compares
   the analytic gradient with central finite differences.)
4. How large is the per-match diagonal approximation error on the same match
   with the same prior?  (``compare_one_step``)
5. Does more history keep changing the window's answer?
   (``window_truncation`` - a W / 2W / 4W / full-history stability curve.)
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from .engine import (
    ApproxFilter,
    EngineConfig,
    ModelSpec,
    Observation,
    diagonal_step,
    initial_state,
    joint_single_skill_fit,
    joint_window_fit,
)
from .linalg import zeros
from .model import Dataset
from .run import Frame, Run, play_counts_from


Z90 = 1.6448536269514722


def truth_at(ds: Dataset, pid: str, day: int) -> float:
    path = ds.truth["skill"][pid]
    return path[min(max(int(day), 0), len(path) - 1)]


# --------------------------------------------------------------------------
# Accuracy and calibration over the whole run
# --------------------------------------------------------------------------


@dataclass
class Scorecard:
    method: str
    n_frames: int
    rmse: float
    mae: float
    bias: float
    coverage90: float
    mean_sd: float
    settled_rmse: float      # players who had played >= 5 matches
    n_settled: int
    log_score: float

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _frame_skill_errors(
    ds: Dataset, spec: ModelSpec, frame: Frame
) -> List[Tuple[float, float, float]]:
    out = []
    for i, pid in enumerate(spec.player_ids):
        true = truth_at(ds, pid, frame.day)
        err = frame.post_mean[i] - true
        out.append((err, frame.post_sd[i], i))
    return out


def score_run(ds: Dataset, spec: ModelSpec, run: Run) -> Scorecard:
    play_counts = play_counts_from(run.resolved)
    errors: List[float] = []
    sd_all: List[float] = []
    covered = 0
    total = 0
    log_scores: List[float] = []
    settled_errors: List[float] = []
    for frame in run.frames:
        for err, sd, i in _frame_skill_errors(ds, spec, frame):
            if sd <= 0:
                continue
            errors.append(err)
            sd_all.append(sd)
            total += 1
            if abs(err) <= Z90 * sd:
                covered += 1

    # Only players with a real sample size, at the end of the season.
    if run.frames:
        last = run.frames[-1]
        for i, pid in enumerate(spec.player_ids):
            if play_counts.get(pid, 0) >= 5:
                err = last.post_mean[i] - truth_at(ds, pid, last.day)
                settled_errors.append(err)
    for frame in run.frames:
        goals = (frame.goals_a, frame.goals_b)
        probs = (frame.p_a, frame.p_draw, frame.p_b)
        # Brier-style multiclass score, lower is better.
        actual = 0 if goals[0] > goals[1] else (1 if goals[0] == goals[1] else 2)
        log_scores.append(-math.log(max(probs[actual], 1e-9)))

    n = max(1, len(errors))
    ns = max(1, len(settled_errors))
    return Scorecard(
        method=run.method,
        n_frames=len(run.frames),
        rmse=math.sqrt(sum(e * e for e in errors) / n),
        mae=sum(abs(e) for e in errors) / n,
        bias=sum(errors) / n,
        coverage90=covered / max(1, total),
        mean_sd=statistics.fmean(sd_all) if sd_all else 0.0,
        settled_rmse=math.sqrt(sum(e * e for e in settled_errors) / ns),
        n_settled=len(settled_errors),
        log_score=statistics.fmean(log_scores) if log_scores else 0.0,
    )


def _match_of(run: Run, index: int) -> dict:
    return run.resolved[index - 1]


# --------------------------------------------------------------------------
# Response speed after a known form shock
# --------------------------------------------------------------------------


def response_to_shock(
    ds: Dataset,
    spec: ModelSpec,
    run: Run,
    pid: str,
    shock_day: int,
    delta: float,
    games: int = 4,
) -> dict:
    """Response after a known form jump.

    Players do not all play on the same days, so the useful clock is *games
    played by that player*, not calendar days.  We report both: the number of
    own matches until the estimate has absorbed half the jump, and the
    calendar days that took.
    """
    i = spec.index_of[pid]
    counted = 0
    before = None
    history = []
    target_from = truth_at(ds, pid, shock_day - 2)
    target_to = truth_at(ds, pid, shock_day + 25)
    half = 0.5 * (delta if delta else (target_to - target_from))
    for frame in run.frames:
        rec = run.resolved[frame.index - 1]
        side_a = rec["side_a"] if isinstance(rec, dict) else rec.side_a
        side_b = rec["side_b"] if isinstance(rec, dict) else rec.side_b
        if pid in side_a or pid in side_b:
            counted += 1
            if frame.day < shock_day:
                before = frame.post_mean[i]
            history.append(
                {
                    "game": counted,
                    "day": frame.day,
                    "est": round(frame.post_mean[i], 4),
                    "sd": round(frame.post_sd[i], 4),
                }
            )
    games_before_shock = sum(1 for h in history if h["day"] < shock_day)
    if before is None or not any(h["day"] >= shock_day for h in history):
        return {"player_id": pid, "shock_day": shock_day, "available": False}
    # Anchor the "before" level on the last pre-shock estimate.
    base_est = before
    half_day = None
    half_game = None
    for h in history:
        if h["day"] < shock_day:
            continue
        absorbed = (h["est"] - base_est) / half if half else 0.0
        if half_day is None and absorbed >= 1.0:
            half_day = h["day"] - shock_day
            # Clock in this player's own games *since* the shock, not the
            # cumulative game number of the season.
            half_game = h["game"] - games_before_shock
    return {
        "player_id": pid,
        "shock_day": shock_day,
        "delta": delta,
        "estimate_before": round(base_est, 4),
        "estimate_after": round(history[-1]["est"], 4) if history else None,
        "truth_before": round(target_from, 4),
        "truth_after": round(target_to, 4),
        "half_absorption_game": half_game,
        "half_absorption_day": half_day,
        "games_before_shock": games_before_shock,
        "games_since_shock": sum(1 for h in history if h["day"] >= shock_day),
        "series": history,
        "available": True,
    }


# --------------------------------------------------------------------------
# Exact small-window checks
# --------------------------------------------------------------------------


def _prior_arrays(spec, prior, keys):
    from .engine import _prior_arrays as impl

    return impl(spec, prior, keys)


def _objective(spec, observations, prior, keys, vec):
    from .engine import _local_terms

    local = {k: i for i, k in enumerate(keys)}
    total = 0.0
    means, variances = _prior_arrays(spec, prior, keys)
    for i in range(len(vec)):
        d = vec[i] - means[i]
        total -= 0.5 * d * d / variances[i]
    for obs in observations:
        ll, _, _ = _local_terms(spec, obs, vec, local)
        total += ll
    return total


def gradient_check(
    spec: ModelSpec, observations: Sequence[Observation], config: EngineConfig
) -> dict:
    """Finite-difference check of the joint fit's gradient (central differences)."""
    prior = initial_state(spec, config)
    fit = joint_window_fit(spec, observations, prior, config)
    keys = fit.param_keys
    theta = fit.mean
    from .engine import _local_terms

    local = {k: i for i, k in enumerate(keys)}
    means, variances = _prior_arrays(spec, prior, keys)
    n = len(theta)

    def objective(vec):
        total = _objective(spec, observations, prior, keys, vec)
        from .engine import _local_terms as impl  # noqa: F401

        return total

    grad = [-(theta[i] - means[i]) / variances[i] for i in range(n)]
    for obs in observations:
        _, g, _ = _local_terms(spec, obs, theta, local)
        for i in range(n):
            grad[i] += g[i]

    h = 1e-6
    max_err = 0.0
    idxs = list(range(min(6, n)))
    for i in idxs:
        plus = list(theta)
        minus = list(theta)
        plus[i] += h
        minus[i] -= h
        num = (objective(plus) - objective(minus)) / (2 * h)
        max_err = max(max_err, abs(num - grad[i]))
    # Hessian symmetry & positive definiteness of the cached covariance.
    cov = fit.cov
    asym = max(
        (abs(cov[i][j] - cov[j][i]) for i in range(len(cov)) for j in range(len(cov))),
        default=0.0,
    )
    return {
        "n_params": n,
        "checked_params": len(idxs),
        "max_gradient_error": round(max_err, 8),
        "objective": round(objective(theta), 4),
        "grad_norm": round(fit.grad_norm, 8),
        "cov_asymmetry": round(asym, 12),
        "converged": fit.converged,
    }


def compare_one_step(
    spec: ModelSpec,
    config: EngineConfig,
    observations: Sequence[Observation],
    index: int,
    drift_sd: float = 0.0,
) -> dict:
    """Diagonal one-match update vs an exact joint fit on that same match.

    Both start from the *same* prior state (the diagonal state just before
    the match), so the difference measures the diagonal approximation only.
    The fit is one match, so the joint model is exactly solved.
    """
    flt = ApproxFilter(spec, config)
    for obs in observations[:index]:
        flt.step(obs, observations)
    prior = flt.current().copy()
    if drift_sd:
        prior = prior.inflate(observations[index].day + 1, drift_sd)
    approx_after = diagonal_step(prior, spec, observations[index], config)
    joint = joint_single_skill_fit(spec, observations[index], prior, config)
    idx = {k: i for i, k in enumerate(joint.param_keys)}
    touched = sorted(set(observations[index].side_a) | set(observations[index].side_b))
    diffs = []
    zs = []
    for p in touched:
        key = ("s", p)
        if key not in idx:
            continue
        j = idx[key]
        diff = approx_after.skill_mean[p] - joint.mean[j]
        diffs.append(diff)
        jac = math.sqrt(max(joint.cov[j][j], 1e-12))
        zs.append(diff / jac)
    return {
        "match_id": observations[index].match_id,
        "n_players": len(touched),
        "max_abs_diff": round(max(abs(d) for d in diffs), 6) if diffs else 0.0,
        "rms_diff": round(math.sqrt(sum(d * d for d in diffs) / max(1, len(diffs))), 6),
        "max_z": round(max(abs(z) for z in zs), 4) if zs else 0.0,
        "approx_mean": {spec.player_ids[p]: round(approx_after.skill_mean[p], 5) for p in touched},
        "exact_mean": {
            spec.player_ids[p]: round(joint.mean[idx[("s", p)]], 5)
            for p in touched
            if ("s", p) in idx
        },
    }


def _fit_on(
    spec: ModelSpec, observations, prior_mean, prior_var, keys, config
):
    """Joint MAP fit restricted to ``keys`` with an explicit Gaussian prior."""
    from .engine import _local_terms
    from .linalg import invert_spd, solve_spd

    local = {k: i for i, k in enumerate(keys)}
    n = len(keys)
    theta = [prior_mean[k] for k in keys]
    pvar = [prior_var[k] for k in keys]

    def objective(vec):
        total = 0.0
        for i in range(n):
            d = vec[i] - prior_mean[keys[i]]
            total -= 0.5 * d * d / pvar[i]
        for obs in observations:
            ll, _, _ = _local_terms(spec, obs, vec, local, include_combo=False)
            total += ll
        return total

    obj = objective(theta)
    for _ in range(config.damping_iters):
        grad = [-(theta[i] - prior_mean[keys[i]]) / pvar[i] for i in range(n)]
        info = zeros(n, n)
        for i in range(n):
            info[i][i] = 1.0 / pvar[i]
        for obs in observations:
            _, g, h = _local_terms(spec, obs, theta, local, include_combo=False)
            for i in range(n):
                grad[i] += g[i]
                for j in range(n):
                    info[i][j] += h[i][j]
        if max((abs(g) for g in grad), default=0.0) < 1e-10:
            break
        step = solve_spd(info, grad)
        scale = 6.0 / max(1.0, max(abs(s) for s in step))
        step = [s * scale for s in step]
        t = 1.0
        for _ in range(30):
            cand = [theta[i] + t * step[i] for i in range(n)]
            cobj = objective(cand)
            if cobj >= obj - 1e-12:
                theta, obj = cand, cobj
                break
            t *= 0.5
        else:
            break
    grad = [-(theta[i] - prior_mean[keys[i]]) / pvar[i] for i in range(n)]
    info = zeros(n, n)
    for i in range(n):
        info[i][i] = 1.0 / pvar[i]
    for obs in observations:
        _, g, h = _local_terms(spec, obs, theta, local, include_combo=False)
        for i in range(n):
            grad[i] += g[i]
            for j in range(n):
                info[i][j] += h[i][j]
    cov = invert_spd(info)
    return {k: theta[i] for i, k in enumerate(keys)}, {k: cov[i][i] for i, k in enumerate(keys)}


def window_truncation(
    spec: ModelSpec,
    config: EngineConfig,
    observations: Sequence[Observation],
    end: int,
) -> dict:
    """Truncation sensitivity: W matches vs 2W, 4W and the full history.

    All fits share the same prior scale and use skills only, and all end at
    the same match.  If the window were long enough, its estimate would stop
    moving as more history is added; each successive difference is therefore
    a truncation-error reading, not a drift reading, because every fit is
    anchored on the same recent era.
    """
    if end <= 0:
        return {"available": False, "end": end}
    everyone = sorted(
        {p for o in observations[:end] for p in list(o.side_a) + list(o.side_b)}
    )
    prior_mean = {("s", p): 0.0 for p in everyone}
    prior_var = {("s", p): config.init_skill_sd ** 2 for p in everyone}
    keys = [("s", p) for p in everyone]

    def fit_last(count: int):
        start = max(0, end - count)
        est, _ = _fit_on(
            spec, observations[start:end], prior_mean, prior_var, keys, config
        )
        return est, end - start

    widths = [config.window, config.window * 2, config.window * 4, end]
    results = []
    ref, n_ref = fit_last(config.window)
    results.append({"history_matches": n_ref, "reference": True})
    for w in widths[1:]:
        if w <= config.window:
            continue
        est, n = fit_last(w)
        diffs = [ref[k] - est[k] for k in keys]
        results.append(
            {
                "history_matches": n,
                "rms_diff_vs_window": round(
                    math.sqrt(sum(d * d for d in diffs) / len(diffs)), 5
                ),
                "max_abs_diff_vs_window": round(max(abs(d) for d in diffs), 5),
                "reference": False,
            }
        )
    return {
        "available": True,
        "end": end,
        "window": config.window,
        "comparisons": results,
        "note": (
            "所有拟合都结束于同一场比赛，只看历史长度。窗口越长估计越稳；"
            "若 2W→4W 的差已很小，说明 W 足够。"
        ),
    }


def member_indistinguishability(
    ds: Dataset, spec: ModelSpec, run: Run, members: Sequence[str]
) -> dict:
    idxs = [spec.index_of[p] for p in members]
    last = run.frames[-1]
    means = [last.post_mean[i] for i in idxs]
    sds = [last.post_sd[i] for i in idxs]
    true = [truth_at(ds, spec.player_ids[i], last.day) for i in idxs]
    return {
        "members": list(members),
        "est_spread": round(max(means) - min(means), 5),
        "est_sd_mean": round(statistics.fmean(sds), 4),
        "true_spread": round(max(true) - min(true), 4),
        "identified": (max(means) - min(means)) > 0.05,
        "note": "同队成员的个人水平不可分；给出的应是阵容合计。",
    }
