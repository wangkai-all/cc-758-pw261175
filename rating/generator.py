"""Deterministic generator of fictional team matches with known true skill.

Latent skills follow a Gaussian random walk on a day grid.  Match goals are
drawn from a Poisson model whose log-mean is a log base rate plus the mean
latent skill of each side; an exact-lineup combination term is part of the
generative truth, so "team combination performance" is not a fiction invented
by the estimator.  There is no home-field term: the data and the estimator both
treat the two sides symmetrically.

The generator also plants the evaluation scenarios requested by the task:

* an isolated group whose members never meet anyone outside it;
* a perma-bound trio whose members always play together;
* a draw-heavy subset (low-scoring, evenly matched fixtures);
* a duplicated import batch (exact re-imports plus a duplicate row).

Everything is seeded from the dataset seed; no external data is used.
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Dict, List

from .model import Dataset, Match, Player, now_iso


FIRST = [
    "林", "周", "陈", "赵", "沈", "顾", "陆", "许", "韩", "叶",
    "苏", "程", "唐", "魏", "夏", "秦", "方", "白", "江", "邵",
    "罗", "何", "高", "殷", "钟", "余", "谢", "严", "温", "阮",
    "傅", "石", "孟", "段", "钱", "万", "翁", "葛", "鲁", "韦",
]
GIVEN = [
    "澈", "远", "舟", "野", "迟", "川", "禾", "砚", "渡", "岑",
    "澜", "祈", "寒", "序", "岩", "朔", "牧", "屿", "璟", "钧",
    "昀", "桐", "柏", "霁", "檀", "衡", "骁", "翎", "陌", "临",
    "叙", "玄", "禹", "峤", "岚", "泓", "湛", "栩", "珩", "熙",
]
FIRST = list(dict.fromkeys(FIRST))


def _name(rng: random.Random, used: set) -> str:
    for _ in range(200):
        n = rng.choice(FIRST) + rng.choice(GIVEN)
        if n not in used:
            used.add(n)
            return n
    raise RuntimeError("name space exhausted")


class Truth:
    """Container for generated latent truth."""

    def __init__(self) -> None:
        self.skill: Dict[str, List[float]] = {}
        self.combo: Dict[str, float] = {}
        self.players: List[Player] = []
        self.role_of: Dict[str, str] = {}

    def to_dict(self) -> dict:
        return {
            "skill": {pid: [round(v, 6) for v in path] for pid, path in self.skill.items()},
            "combo": {k: round(v, 6) for k, v in self.combo.items()},
            "role_of": dict(self.role_of),
        }

    def skill_on(self, pid: str, day: int) -> float:
        path = self.skill[pid]
        return path[int(round(day))] if 0 <= day < len(path) else path[-1]


def _poisson(rng: random.Random, lam: float) -> int:
    # Knuth for small means; normal approximation above 30.
    if lam <= 0.0:
        return 0
    if lam > 30.0:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    limit = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def generate_dataset(
    *,
    seed: int = 75826,
    days: int = 180,
    n_main: int = 26,
    drift_scale: float = 0.010,
    base_goals: float = 1.45,
    skill_scale: float = 0.62,
) -> Dataset:
    rng = random.Random(seed)
    truth = Truth()
    used_names: set = set()

    roles = []
    for i in range(n_main):
        club = f"俱乐部{chr(65 + i % 6)}{i // 6 + 1}"
        pid = f"P{i + 1:03d}"
        role = "core" if i < 18 else "rotation"
        roles.append((pid, club, role))

    # Isolated group: 4 players, never mixed with the main pool.
    iso = []
    for i in range(4):
        pid = f"Q{i + 1:03d}"
        iso.append((pid, "孤岛组", "isolated"))
    # Perma-bound trio: always fielded together, never separately.
    bound = []
    for i in range(3):
        pid = f"B{i + 1:03d}"
        bound.append((pid, "绑定三人组", "bound"))

    # Controlled mid-season form shocks: known to the evaluator, not to the
    # estimator.  They make "response speed" measurable rather than a guess.
    shocks = {
        "P005": (100, 0.85),
        "P009": (120, -0.70),
        "P001": (60, -0.55),
        "P007": (95, 0.80),
        "P019": (90, -0.75),
        "P013": (110, 0.70),
    }

    everyone = roles + iso + bound
    for pid, club, role in everyone:
        truth.players.append(Player(id=pid, name=_name(rng, used_names), club=club, note=role))
        truth.role_of[pid] = role
        start = rng.gauss(0.0, skill_scale)
        path = [0.0] * days
        for d in range(days):
            if d == 0:
                path[d] = start
            else:
                # A few players have a mid-season form break (bigger step).
                step_sd = drift_scale * (3.0 if rng.random() < 0.004 else 1.0)
                path[d] = path[d - 1] + rng.gauss(0.0, step_sd)
            if pid in shocks and d == shocks[pid][0]:
                path[d] += shocks[pid][1]
        truth.skill[pid] = path

    players_main = [p for p, _, _ in roles]
    players_iso = [p for p, _, _ in iso]
    bound_ids = sorted([p for p, _, _ in bound])

    # Team-combination terms for exact lineups that recur in the schedule.
    # Fixing a lineup's true combination before sampling avoids judging the
    # estimator against a moving target.
    lineups: List[List[str]] = [sorted(bound_ids)]
    for _ in range(10):
        size = rng.choice([2, 2, 3])
        # Draw from all players: a shared main-pool lineup is what makes the
        # combination term useful, not a pool-label artifact.
        lineups.append(sorted(rng.sample(players_main, size)))
    # Sparse, deliberate combination effects: most lineups are exactly
    # additive, while a few carry a real synergy or clash.  This keeps the
    # truth identifiable and makes the "team combination" output mean
    # something rather than fitting noise.
    synergy = {
        "|".join(sorted(bound_ids)): 0.35,
        "|".join(sorted(players_iso)): 0.25,
    }
    for combo in lineups:
        key = "|".join(combo)
        truth.combo[key] = synergy.get(key, 0.0)

    # One main-pool player is long-term absent: no match between day 66 and
    # day 128.  His interval must widen over that gap, not shrink.
    absent = "P018"
    absent_from, absent_until = 66, 128

    # Standardise starting skills: the model's baseline is the side-average
    # skill, so a non-zero population mean would be absorbed into the
    # baseline and make truth-vs-estimate comparisons unfair.
    mean_start = statistics.fmean(p[0] for p in truth.skill.values())
    for pid in truth.skill:
        truth.skill[pid] = [v - mean_start for v in truth.skill[pid]]

    matches: List[Match] = []
    mid = 0
    next_day = 1
    duplicate_pending: List[Match] = []
    seen_fixtures: set = set()
    while next_day < days:
        n_games = 1 if rng.random() < 0.18 else (2 if rng.random() < 0.84 else 3)
        if n_games == 0:
            next_day += 1
            continue
        for _ in range(n_games):
            mode = rng.random()
            if mode < 0.12:
                pool = players_iso
                size = 2
                tag = "isolated"
            elif mode < 0.24:
                pool = list(players_main)
                size = 3
                tag = "bound"
            else:
                pool = players_main
                size = rng.choice([2, 2, 3, 3, 4, 5])
                tag = "main"
            if tag == "bound":
                a = bound_ids
                # Bound trio plays against a mixed side, never split.
                b = sorted(rng.sample(pool, size))
                side_a, side_b = a, b
            else:
                chosen = rng.sample(pool, size * 2)
                side_a, side_b = sorted(chosen[:size]), sorted(chosen[size:])

            if absent in side_a + side_b and absent_from <= next_day <= absent_until:
                continue

            def lam(side: List[str], day: int = next_day) -> float:
                avg = sum(truth.skill_on(p, day) for p in side) / len(side)
                combo = truth.combo.get("|".join(sorted(side)), 0.0)
                return math.exp(math.log(base_goals) + avg + combo)

            # A team cannot play the identical fixture twice on one day; such
            # a row would be indistinguishable from a re-import.
            fixture = (next_day, tuple(sorted(side_a)), tuple(sorted(side_b)))
            if fixture in seen_fixtures:
                continue
            seen_fixtures.add(fixture)

            # Draws are a real outcome of equal lambdas, not a forced tag.
            ga = _poisson(rng, lam(side_a))
            gb = _poisson(rng, lam(side_b))
            m = Match(
                id=f"M{mid + 1:04d}",
                day=next_day,
                side_a=tuple(side_a),
                side_b=tuple(side_b),
                score_a=ga,
                score_b=gb,
                batch="import-1",
                tags=(tag,),
            )
            mid += 1
            matches.append(m)
            # Re-import a few weeks later for the duplicate scenario.
            if rng.random() < 0.07 or (
                "bound" in m.tags and len(duplicate_pending) == 0
            ):
                duplicate_pending.append(m)
        next_day += rng.choice([1, 1, 1, 1, 2, 2, 3, 4])

    # Append an exact duplicate batch (same fingerprint, later batch id).
    dup_batch = "import-2"
    dup_matches = []
    for m in duplicate_pending[:6]:
        d = Match(
            id=f"D{len(dup_matches) + 1:03d}",
            day=m.day,
            side_a=m.side_a,
            side_b=m.side_b,
            score_a=m.score_a,
            score_b=m.score_b,
            batch=dup_batch,
            tags=m.tags + ("duplicate-batch",),
        )
        dup_matches.append(d)
    # One deliberately conflicting row: same fixture and day, different score.
    # Real re-imports can disagree after a manual edit upstream; the importer
    # must keep both and ask, never silently pick one.
    if len(matches) >= 3:
        main_only = [m for m in matches if "main" in m.tags]
        target = main_only[len(main_only) // 3] if main_only else matches[2]
        conflict = Match(
            id="C001",
            day=target.day,
            side_a=target.side_a,
            side_b=target.side_b,
            score_a=target.score_a + 1,
            score_b=target.score_b,
            batch="import-3",
            tags=("conflict",),
        )
        matches.append(conflict)

    # One row inside the duplicate batch is itself duplicated (double import).
    if dup_matches:
        d0 = dup_matches[0]
        dup_matches.append(
            Match(
                id="D900",
                day=d0.day,
                side_a=d0.side_a,
                side_b=d0.side_b,
                score_a=d0.score_a,
                score_b=d0.score_b,
                batch="import-2b",
                tags=d0.tags + ("duplicate-batch", "double-import"),
            )
        )
    matches.extend(dup_matches)

    # Freeze truth at the days that were actually played for the metadata.
    scenario = {
        "isolated_group": sorted(players_iso),
        "absent_player": {"id": absent, "from": absent_from, "until": absent_until},
        "shocks": {
            pid: {"day": day, "delta": delta}
            for pid, (day, delta) in shocks.items()
            if day < days
        },
        "bound_group": sorted([p for p, _, _ in bound]),
        "bound_combo_key": "|".join(sorted([p for p, _, _ in bound])),
        "draw_share": round(
            sum(1 for m in matches if m.is_draw) / max(1, len(matches)), 4
        ),
        "duplicate_batch": dup_batch,
        "duplicate_rows": [m.id for m in dup_matches],
    }

    ds = Dataset(
        id=f"rwr-{seed}",
        title="星河轮换联赛（虚构）",
        description=(
            "虚构的多人混编联赛：主池 26 人轮换、4 人孤岛组从不与主池交手、"
            "3 人绑定组总是同队出场；比分由泊松模型按真实能力路径生成。"
        ),
        players=truth.players,
        matches=matches,
        base_goals=base_goals,
        skill_scale=skill_scale,
        drift_true=drift_scale,
        seed=seed,
        truth=truth.to_dict(),
        truth_meta={
            "days": days,
            "drift_sd_per_day": drift_scale,
            "skill_scale": skill_scale,
            "draw_share": scenario["draw_share"],
            "n_matches": len(matches),
            "n_players": len(truth.players),
        },
        scenarios=scenario,
        generated_at=now_iso(),
    )
    return ds
