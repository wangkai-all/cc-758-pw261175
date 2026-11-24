"""Two estimators for roster strength with time drift.

Model
-----
For a match between sides A and B:

    eta_A = b + mean_{i in A} s_i + g(lineup_A)
    eta_B = b + mean_{j in B} s_j + g(lineup_B)
    y_A ~ Poisson(exp(eta_A)),  y_B ~ Poisson(exp(eta_B))

``s_i`` is player i's time-varying contribution (one number: how much that
player lifts his side's scoring), ``g(lineup)`` is the combination effect of
the exact starting lineup, ``b`` is the dataset's scoring baseline.  Draws need no special casing: two
Poisson counts produce them naturally, and the model's draw probability is
part of every prediction.

Two ways to update after each match
-----------------------------------
* ``ApproxFilter`` - a per-match diagonal Laplace update.  Each parameter is
  updated against its own prior only.  Fast, but it ignores that a lineup's
  members share one likelihood: teammates' estimates move together, so the
  diagonal approximation reports too much certainty, especially for a
  perma-bound lineup.
* ``WindowFilter`` - a joint Newton/Laplace fit over the last ``window``
  matches.  Exact (to the Laplace approximation) inside the window: it keeps
  the full posterior covariance, so teammate correlation, combo/skill
  trade-offs and the extra width from a long absence are represented.

Between matches both filters run a random-walk time update: every skill
variance grows by ``drift_sd**2 * days``.  Variance never shrinks while a
player is absent; absence can only widen the interval.

Gauge
-----
``b = log(base_goals)`` is fixed from the dataset, so absolute levels are
identified (one free global offset per connected group would otherwise be
unidentifiable) and estimates from disconnected groups are placed on the same
scale.  That placement rests on the assumed baseline when groups never meet,
which the diagnostics flag.  Truth in the generator is centred so that the
population mean skill is zero, matching this baseline convention.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .linalg import invert_spd, solve_spd, zeros
from .model import Match


@dataclass
class EngineConfig:
    drift_sd: float = 0.040          # random-walk sd of a skill per day
    init_skill_sd: float = 0.45
    combo_sd: float = 0.25
    combo_min_count: int = 2
    window: int = 8
    base_goals: float = 1.45
    damping_iters: int = 50
    approx_iters: int = 5

    def fingerprint(self) -> str:
        return (
            f"d{self.drift_sd:.5f}-i{self.init_skill_sd:.3f}-"
            f"c{self.combo_sd:.3f}-m{self.combo_min_count}-w{self.window}-"
            f"b{self.base_goals:.5f}"
        )


def lineup_key(side: Iterable[str]) -> str:
    return "|".join(sorted(side))


@dataclass(frozen=True)
class Observation:
    match_id: str
    day: int
    side_a: Tuple[int, ...]
    side_b: Tuple[int, ...]
    goals_a: int
    goals_b: int
    combo_a: Optional[str] = None
    combo_b: Optional[str] = None

    @property
    def draw(self) -> bool:
        return self.goals_a == self.goals_b


class ModelSpec:
    """Player / active-lineup indexing shared by both filters."""

    def __init__(
        self,
        player_ids: Sequence[str],
        observations: Sequence[Observation],
        config: EngineConfig,
    ):
        self.player_ids = list(player_ids)
        self.index_of: Dict[str, int] = {pid: i for i, pid in enumerate(player_ids)}
        counts: Dict[str, int] = {}
        for obs in observations:
            for key in (obs.combo_a, obs.combo_b):
                if key:
                    counts[key] = counts.get(key, 0) + 1
        self.combo_counts = counts
        self.combos: List[str] = sorted(
            k for k, v in counts.items() if v >= config.combo_min_count
        )
        self.combo_index: Dict[str, int] = {k: i for i, k in enumerate(self.combos)}
        self.config = config
        self.base = math.log(max(config.base_goals, 0.05))

    @property
    def n_players(self) -> int:
        return len(self.player_ids)

    def active_combo(self, key: Optional[str]) -> bool:
        return bool(key) and key in self.combo_index

    def obs_for(self, match: Match) -> Observation:
        return Observation(
            match_id=match.id,
            day=match.day,
            side_a=tuple(self.index_of[p] for p in match.side_a),
            side_b=tuple(self.index_of[p] for p in match.side_b),
            goals_a=match.score_a,
            goals_b=match.score_b,
            combo_a=lineup_key(match.side_a),
            combo_b=lineup_key(match.side_b),
        )


# --------------------------------------------------------------------------
# Likelihood
# --------------------------------------------------------------------------


def lambdas(
    obs: Observation,
    skills: Sequence[float],
    combos: Dict[str, float],
    base: float,
) -> Tuple[float, float]:
    avg_a = sum(skills[i] for i in obs.side_a) / len(obs.side_a)
    avg_b = sum(skills[i] for i in obs.side_b) / len(obs.side_b)
    ga = combos.get(obs.combo_a, 0.0) if obs.combo_a else 0.0
    gb = combos.get(obs.combo_b, 0.0) if obs.combo_b else 0.0
    eta_a = base + avg_a + ga
    eta_b = base + avg_b + gb
    return math.exp(min(eta_a, 18.0)), math.exp(min(eta_b, 18.0))


def loglik(goals_a: int, goals_b: int, lam_a: float, lam_b: float) -> float:
    return (
        goals_a * math.log(lam_a)
        - lam_a
        + goals_b * math.log(lam_b)
        - lam_b
    )


def outcome_probabilities(
    lam_a: float, lam_b: float, max_goals: int = 12
) -> Dict[str, float]:
    """P(side A wins), P(draw), P(side B wins) under independent Poisson."""
    pa = [math.exp(-lam_a) * lam_a ** k / math.factorial(k) for k in range(max_goals + 1)]
    pb = [math.exp(-lam_b) * lam_b ** k / math.factorial(k) for k in range(max_goals + 1)]
    win_a = draw = win_b = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = pa[i] * pb[j]
            if i > j:
                win_a += p
            elif i == j:
                draw += p
            else:
                win_b += p
    total = win_a + draw + win_b
    return {"a": win_a / total, "draw": draw / total, "b": win_b / total}


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


@dataclass
class DiagState:
    day: int
    skill_mean: List[float]
    skill_var: List[float]
    combo_mean: Dict[str, float]
    combo_var: Dict[str, float]
    last_seen: Dict[str, int]
    plays: Dict[str, int]
    processed: int = 0

    def copy(self) -> "DiagState":
        return DiagState(
            day=self.day,
            skill_mean=list(self.skill_mean),
            skill_var=list(self.skill_var),
            combo_mean=dict(self.combo_mean),
            combo_var=dict(self.combo_var),
            last_seen=dict(self.last_seen),
            plays=dict(self.plays),
            processed=self.processed,
        )

    def inflate(self, day: int, drift_sd: float) -> "DiagState":
        """Random-walk time update; variance can only grow (or stay equal)."""
        out = self.copy()
        if day <= self.day:
            return out
        q2 = drift_sd * drift_sd * (day - self.day)
        out.day = day
        out.skill_var = [v + q2 for v in out.skill_var]
        return out

    def lambdas_for(self, match: Match, spec: "ModelSpec") -> Tuple[float, float]:
        obs = spec.obs_for(match)
        return lambdas(obs, self.skill_mean, self.combo_mean, spec.base)

    def to_dict(self) -> dict:
        return {
            "day": self.day,
            "skill_mean": self.skill_mean,
            "skill_var": self.skill_var,
            "combo_mean": self.combo_mean,
            "combo_var": self.combo_var,
            "last_seen": self.last_seen,
            "plays": self.plays,
            "processed": self.processed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DiagState":
        return cls(
            day=int(d["day"]),
            skill_mean=[float(x) for x in d["skill_mean"]],
            skill_var=[float(x) for x in d["skill_var"]],
            combo_mean={k: float(v) for k, v in d["combo_mean"].items()},
            combo_var={k: float(v) for k, v in d["combo_var"].items()},
            last_seen={k: int(v) for k, v in d["last_seen"].items()},
            plays={k: int(v) for k, v in d["plays"].items()},
            processed=int(d.get("processed", 0)),
        )


def initial_state(spec: ModelSpec, config: EngineConfig, day: int = 0) -> DiagState:
    n = spec.n_players
    return DiagState(
        day=day,
        skill_mean=[0.0] * n,
        skill_var=[config.init_skill_sd ** 2] * n,
        combo_mean={k: 0.0 for k in spec.combos},
        combo_var={k: config.combo_sd ** 2 for k in spec.combos},
        last_seen={pid: -10**6 for pid in spec.player_ids},
        plays={pid: 0 for pid in spec.player_ids},
        processed=0,
    )


def _register(state: DiagState, spec: ModelSpec, obs: Observation) -> None:
    for idx in set(obs.side_a) | set(obs.side_b):
        pid = spec.player_ids[idx]
        state.last_seen[pid] = obs.day
        state.plays[pid] = state.plays.get(pid, 0) + 1


# --------------------------------------------------------------------------
# Diagonal per-match filter
# --------------------------------------------------------------------------


def diagonal_step(
    state: DiagState, spec: ModelSpec, obs: Observation, config: EngineConfig
) -> DiagState:
    """One match, per-parameter Laplace update (ignores cross-parameter info)."""
    out = state.inflate(obs.day, config.drift_sd)
    prior_mean = list(out.skill_mean)
    prior_var = list(out.skill_var)
    combo_prior_mean = {k: out.combo_mean.get(k, 0.0) for k in spec.combos}
    combo_prior_var = {k: out.combo_var.get(k, config.combo_sd ** 2) for k in spec.combos}
    skills_touched = sorted(set(obs.side_a) | set(obs.side_b))
    combos_touched = [
        k for k in (obs.combo_a, obs.combo_b) if k and spec.active_combo(k)
    ]
    combos = {k: out.combo_mean.get(k, 0.0) for k in spec.combos}

    for _ in range(config.approx_iters):
        lam_a, lam_b = lambdas(obs, out.skill_mean, combos, spec.base)
        r_a, r_b = obs.goals_a - lam_a, obs.goals_b - lam_b
        for idx in skills_touched:
            in_a = idx in obs.side_a
            k = len(obs.side_a) if in_a else len(obs.side_b)
            lam, resid = (lam_a, r_a) if in_a else (lam_b, r_b)
            grad = resid / k
            info = lam / (k * k)
            prec = 1.0 / prior_var[idx]
            out.skill_mean[idx] += (
                grad - prec * (out.skill_mean[idx] - prior_mean[idx])
            ) / (prec + info)
        for key in combos_touched:
            if key == obs.combo_a:
                grad, info = r_a, lam_a
            else:
                grad, info = r_b, lam_b
            prec = 1.0 / combo_prior_var[key]
            out.combo_mean[key] += (
                grad - prec * (out.combo_mean[key] - combo_prior_mean[key])
            ) / (prec + info)
        combos = {k: out.combo_mean.get(k, 0.0) for k in spec.combos}

    # Final information at the converged means gives the marginal variance.
    lam_a, lam_b = lambdas(obs, out.skill_mean, combos, spec.base)
    for idx in skills_touched:
        in_a = idx in obs.side_a
        k = len(obs.side_a) if in_a else len(obs.side_b)
        lam = lam_a if in_a else lam_b
        out.skill_var[idx] = 1.0 / (1.0 / prior_var[idx] + lam / (k * k))
    for key in combos_touched:
        lam = lam_a if key == obs.combo_a else lam_b
        out.combo_var[key] = 1.0 / (1.0 / combo_prior_var[key] + lam)

    _register(out, spec, obs)
    out.processed += 1
    return out


class ApproxFilter:
    def __init__(self, spec: ModelSpec, config: EngineConfig):
        self.spec = spec
        self.config = config
        self.initial = initial_state(spec, config)
        self.state = self.initial
        self.processed = 0

    def current(self) -> DiagState:
        return self.state

    def step(self, obs: Observation, all_obs: Sequence[Observation]) -> DiagState:
        self.state = diagonal_step(self.state, self.spec, obs, self.config)
        self.processed += 1
        return self.state

    def pre_match_state(self, obs: Observation) -> DiagState:
        return self.current().inflate(obs.day, self.config.drift_sd)

    def restore(self, state: DiagState, processed: int) -> None:
        self.state = state
        self.processed = processed

    def checkpoint(self) -> dict:
        return self.state.to_dict()


# --------------------------------------------------------------------------
# Joint window fit
# --------------------------------------------------------------------------


@dataclass
class JointFit:
    param_keys: List[Tuple[str, object]]
    mean: List[float]
    cov: List[List[float]]
    loglik: float
    grad_norm: float
    iterations: int
    converged: bool
    window_ids: List[str] = field(default_factory=list)

    def index(self, key: Tuple[str, object]) -> int:
        return self.param_keys.index(key)

    def marginal(self, key: Tuple[str, object]) -> Tuple[float, float]:
        i = self.index(key)
        return self.mean[i], self.cov[i][i]


def _window_param_keys(
    spec: ModelSpec, observations: Sequence[Observation]
) -> List[Tuple[str, object]]:
    players = set()
    combos = set()
    for obs in observations:
        players.update(obs.side_a)
        players.update(obs.side_b)
        for key in (obs.combo_a, obs.combo_b):
            if key and spec.active_combo(key):
                combos.add(key)
    keys: List[Tuple[str, object]] = [("s", p) for p in sorted(players)]
    keys += [("c", k) for k in sorted(combos)]
    return keys


def _prior_arrays(
    spec: ModelSpec, prior: DiagState, keys: Sequence[Tuple[str, object]]
) -> Tuple[List[float], List[float]]:
    means: List[float] = []
    variances: List[float] = []
    for kind, key in keys:
        if kind == "s":
            means.append(prior.skill_mean[int(key)])
            variances.append(max(prior.skill_var[int(key)], 1e-8))
        else:
            key = str(key)
            means.append(prior.combo_mean.get(key, 0.0))
            variances.append(max(prior.combo_var.get(key, spec.config.combo_sd ** 2), 1e-8))
    return means, variances


def _local_terms(
    spec: ModelSpec,
    obs: Observation,
    theta: Sequence[float],
    local: Dict[Tuple[str, object], int],
    include_combo: bool = True,
) -> Tuple[float, List[float], List[List[float]]]:
    n = len(theta)
    grad = [0.0] * n
    info = zeros(n, n)
    combo_values = {
        key: theta[local[("c", key)]] if (include_combo and ("c", key) in local) else 0.0
        for key in spec.combos
    }
    skills = [0.0] * spec.n_players
    for idx in range(spec.n_players):
        key = ("s", idx)
        if key in local:
            skills[idx] = theta[local[key]]
    lam_a, lam_b = lambdas(obs, skills, combo_values, spec.base)
    ll = loglik(obs.goals_a, obs.goals_b, lam_a, lam_b)
    r_a, r_b = obs.goals_a - lam_a, obs.goals_b - lam_b

    ua: Dict[int, float] = {}
    ub: Dict[int, float] = {}
    for idx in obs.side_a:
        k = local.get(("s", idx))
        if k is not None:
            ua[k] = ua.get(k, 0.0) + 1.0 / len(obs.side_a)
    for idx in obs.side_b:
        k = local.get(("s", idx))
        if k is not None:
            ub[k] = ub.get(k, 0.0) + 1.0 / len(obs.side_b)
    if include_combo and obs.combo_a and ("c", obs.combo_a) in local:
        k = local[("c", obs.combo_a)]
        ua[k] = ua.get(k, 0.0) + 1.0
    if include_combo and obs.combo_b and ("c", obs.combo_b) in local:
        k = local[("c", obs.combo_b)]
        ub[k] = ub.get(k, 0.0) + 1.0

    for resid, vec in ((r_a, ua), (r_b, ub)):
        for k, v in vec.items():
            grad[k] += resid * v
    for weight, vec in ((lam_a, ua), (lam_b, ub)):
        items = list(vec.items())
        for i, vi in items:
            row = info[i]
            w = weight * vi
            for j, vj in items:
                row[j] += w * vj
    return ll, grad, info


def joint_window_fit(
    spec: ModelSpec,
    observations: Sequence[Observation],
    prior: DiagState,
    config: EngineConfig,
) -> JointFit:
    """MAP fit of all players and lineups appearing in the window."""
    keys = _window_param_keys(spec, observations)
    if not keys:
        return JointFit([], [], [], 0.0, 0.0, 0, True)
    local = {k: i for i, k in enumerate(keys)}
    prior_mean, prior_var = _prior_arrays(spec, prior, keys)
    theta = list(prior_mean)
    n = len(theta)
    window_ids = [o.match_id for o in observations]

    cache: Dict[Tuple[int, ...], List[Tuple[float, List[float], List[List[float]]]]] = {}

    def terms(vec: Sequence[float]):
        key = tuple(round(v, 12) for v in vec)
        hit = cache.get(key)
        if hit is None:
            hit = [_local_terms(spec, obs, vec, local) for obs in observations]
            cache[key] = hit
        return hit

    def full_objective(vec: Sequence[float]) -> float:
        total = 0.0
        for i in range(n):
            d = vec[i] - prior_mean[i]
            total -= 0.5 * d * d / prior_var[i]
        for ll, _, _ in terms(vec):
            total += ll
        return total

    def grad_info(vec: Sequence[float]):
        grad = [-(vec[i] - prior_mean[i]) / prior_var[i] for i in range(n)]
        info = zeros(n, n)
        for i in range(n):
            info[i][i] = 1.0 / prior_var[i]
        for ll, g, h in terms(vec):
            for i in range(n):
                grad[i] += g[i]
                hi = info[i]
                for j in range(n):
                    hi[j] += h[i][j]
        return grad, info

    obj = full_objective(theta)
    grad_norm = math.inf
    converged = False
    it = 0
    for it in range(1, config.damping_iters + 1):
        grad, info = grad_info(theta)
        grad_norm = max((abs(g) for g in grad), default=0.0)
        if grad_norm < 1e-7:
            converged = True
            break
        step = solve_spd(info, grad)
        max_step = max((abs(s) for s in step), default=0.0)
        if max_step > 6.0:
            scale = 6.0 / max_step
            step = [s * scale for s in step]
        t = 1.0
        accepted = False
        for _ in range(30):
            cand = [theta[i] + t * step[i] for i in range(n)]
            cobj = full_objective(cand)
            if cobj >= obj - 1e-12:
                theta = cand
                obj = cobj
                accepted = True
                break
            t *= 0.5
        if not accepted:
            break
        if max_step < 1e-9:
            converged = True
            break

    grad, info = grad_info(theta)
    grad_norm = max((abs(g) for g in grad), default=0.0)
    cov = invert_spd(info)
    ll_total = sum(t[0] for t in terms(theta))
    return JointFit(
        param_keys=keys,
        mean=theta,
        cov=cov,
        loglik=ll_total,
        grad_norm=grad_norm,
        iterations=it,
        converged=converged or grad_norm < 1e-5,
        window_ids=window_ids,
    )


class WindowFilter:
    """Sliding-window joint fit with a diagonal prior carried from W back."""

    def __init__(self, spec: ModelSpec, config: EngineConfig):
        self.spec = spec
        self.config = config
        self.initial = initial_state(spec, config)
        # states[k] = state after k matches; the last window+1 are kept.
        self.states: Dict[int, DiagState] = {0: self.initial}
        self.processed = 0
        self.last_fit: Optional[JointFit] = None

    def current(self) -> DiagState:
        return self.states[self.processed]

    def _trim(self) -> None:
        cutoff = self.processed - self.config.window
        for k in [k for k in self.states if k < cutoff]:
            del self.states[k]

    def step(
        self, obs: Observation, all_obs: Sequence[Observation]
    ) -> Tuple[DiagState, JointFit]:
        t = self.processed
        start = max(0, t - self.config.window + 1)
        # Prior is the state before the window's first match: no observation
        # is counted twice.  Inflate to the window's midpoint day.
        template = self.states[start - 1] if start > 0 else self.initial
        window = list(all_obs[start : t + 1])
        mid_day = (window[0].day + window[-1].day) / 2.0
        prior = template.inflate(int(round(mid_day)), self.config.drift_sd)
        fit = joint_window_fit(self.spec, window, prior, self.config)
        out = prior.copy()
        for i, (kind, key) in enumerate(fit.param_keys):
            if kind == "s":
                out.skill_mean[int(key)] = fit.mean[i]
                out.skill_var[int(key)] = fit.cov[i][i]
            else:
                key = str(key)
                out.combo_mean[key] = fit.mean[i]
                out.combo_var[key] = fit.cov[i][i]
        out.day = obs.day
        out.processed = t + 1
        _register(out, self.spec, obs)
        self.states[t + 1] = out
        self.processed = t + 1
        self.last_fit = fit
        self._trim()
        return out, fit

    def pre_match_state(self, obs: Observation) -> DiagState:
        t = self.processed
        template = self.states.get(t, self.initial)
        return template.inflate(obs.day, self.config.drift_sd)

    def restore(self, history: Dict[int, DiagState], processed: int) -> None:
        self.states = {int(k): v for k, v in history.items()}
        self.processed = processed
        if processed not in self.states:
            raise ValueError("checkpoint is missing its current state")

    def checkpoint(self) -> dict:
        return {
            "processed": self.processed,
            "states": {str(k): v.to_dict() for k, v in self.states.items()},
        }

    def checkpoint_history(self) -> Dict[str, dict]:
        return {str(k): v.to_dict() for k, v in self.states.items()}


# --------------------------------------------------------------------------
# Batch fit (static reference, used by validation & tests)
# --------------------------------------------------------------------------


def joint_single_skill_fit(
    spec: ModelSpec,
    obs: Observation,
    prior: DiagState,
    config: EngineConfig,
) -> JointFit:
    """Exact one-match joint fit with skills only, no combination term.

    This is the like-for-like exact counterpart of the diagonal one-match
    update: same model, same prior, no shared-lineup parameter to trade off.
    """
    keys = [("s", p) for p in sorted(set(obs.side_a) | set(obs.side_b))]
    local = {k: i for i, k in enumerate(keys)}
    prior_mean, prior_var = _prior_arrays(spec, prior, keys)
    theta = list(prior_mean)
    n = len(theta)

    def terms(vec):
        ll, g, h = _local_terms(spec, obs, vec, local, include_combo=False)
        return ll, g, h

    def objective(vec):
        total = 0.0
        for i in range(n):
            d = vec[i] - prior_mean[i]
            total -= 0.5 * d * d / prior_var[i]
        return total + terms(vec)[0]

    obj = objective(theta)
    converged = False
    it = 0
    for it in range(1, config.damping_iters + 1):
        ll, g, h = terms(theta)
        grad = [-(theta[i] - prior_mean[i]) / prior_var[i] + g[i] for i in range(n)]
        info = [[h[i][j] + (1.0 / prior_var[i] if i == j else 0.0) for j in range(n)] for i in range(n)]
        if max((abs(v) for v in grad), default=0.0) < 1e-9:
            converged = True
            break
        step = solve_spd(info, [v for v in grad])
        step = [s for s in step]
        scale = 6.0 / max(1.0, max(abs(s) for s in step))
        step = [s * scale for s in step]
        t = 1.0
        accepted = False
        for _ in range(30):
            cand = [theta[i] + t * step[i] for i in range(n)]
            cobj = objective(cand)
            if cobj >= obj - 1e-12:
                theta, obj, accepted = cand, cobj, True
                break
            t *= 0.5
        if not accepted:
            break
    ll, g, h = terms(theta)
    info = [[h[i][j] + (1.0 / prior_var[i] if i == j else 0.0) for j in range(n)] for i in range(n)]
    return JointFit(
        param_keys=keys,
        mean=theta,
        cov=invert_spd(info),
        loglik=ll,
        grad_norm=max(
            (
                abs(-(theta[i] - prior_mean[i]) / prior_var[i] + g[i])
                for i in range(n)
            ),
            default=0.0,
        ),
        iterations=it,
        converged=converged,
    )


def lineup_aggregates(
    spec: ModelSpec,
    fit: JointFit,
    state: DiagState,
) -> Dict[str, Tuple[float, float]]:
    """Combined lift of a lineup: mean(member skill) + combo effect.

    The combination term and the member levels are not separately identified
    for a perma-bound lineup; their sum is.  The variance uses the full
    posterior covariance, so it is honest about the sharing.
    """
    out: Dict[str, Tuple[float, float]] = {}
    index = {k: i for i, k in enumerate(fit.param_keys)}
    for combo_key in spec.combos:
        members = combo_key.split("|")
        idxs = [spec.index_of[p] for p in members if p in spec.index_of]
        if not idxs:
            continue
        weights: List[Tuple[int, float]] = []
        mean = 0.0
        for idx in idxs:
            k = ("s", idx)
            if k in index:
                weights.append((index[k], 1.0 / len(idxs)))
                mean += fit.mean[index[k]] / len(idxs)
            else:
                mean += state.skill_mean[idx] / len(idxs)
        ck = ("c", combo_key)
        if ck in index:
            weights.append((index[ck], 1.0))
            mean += fit.mean[index[ck]]
        else:
            mean += state.combo_mean.get(combo_key, 0.0)
        var = 0.0
        for i, wi in weights:
            for j, wj in weights:
                var += wi * wj * fit.cov[i][j]
        out[combo_key] = (mean, max(var, 0.0))
    return out


def fit_batch(
    spec: ModelSpec,
    observations: Sequence[Observation],
    prior: DiagState,
    config: EngineConfig,
) -> JointFit:
    return joint_window_fit(spec, observations, prior, config)
