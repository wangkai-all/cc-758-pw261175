"""Sequential runs, checkpoints and correction replay.

A ``Run`` is everything needed to replay and to audit one method under one
configuration:

* the resolved observations it consumed (so a later correction is visible);
* a per-match frame: the prediction made *before* the match, the goals, and
  the posterior mean/sd of every player *after* it;
* saved filter checkpoints at fixed intervals.

A correction never edits a frame or a checkpoint in place.  It stores a new
result revision, restores the newest checkpoint at or before the corrected
match, and replays the rest of the season into a *new* run revision.  The old
run revision and the board published from it remain on disk untouched.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .engine import (
    ApproxFilter,
    DiagState,
    EngineConfig,
    ModelSpec,
    Observation,
    WindowFilter,
    lambdas,
    outcome_probabilities,
)
from .model import Dataset, Match


CHECKPOINT_EVERY = 10


def run_fingerprint(ds: Dataset, method: str, config: EngineConfig) -> str:
    raw = json.dumps(
        {
            "dataset": ds.id,
            "method": method,
            "config": dataclasses.asdict(config),
            "matches": [
                [m.id, m.day, sorted(m.side_a), sorted(m.side_b), m.score_a, m.score_b]
                for m in sorted(ds.matches, key=lambda m: (m.day, m.id))
            ],
        },
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class Frame:
    index: int
    match_id: str
    day: int
    goals_a: int
    goals_b: int
    pre_mean: List[float]
    pre_sd: List[float]
    p_a: float
    p_draw: float
    p_b: float
    lam_a: float
    lam_b: float
    post_mean: List[float]
    post_sd: List[float]
    seconds: float = 0.0
    result_version: int = 0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Frame":
        return cls(**d)


@dataclass
class Run:
    dataset_id: str
    method: str
    config: dict
    fingerprint: str
    revision: int
    created_at: str
    result_versions: Dict[str, int]
    resolved: List[dict]
    frames: List[Frame]
    checkpoints: Dict[str, dict]
    parent_revision: Optional[int] = None
    replayed_from: Optional[int] = None
    note: str = ""
    history: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "method": self.method,
            "config": self.config,
            "fingerprint": self.fingerprint,
            "revision": self.revision,
            "created_at": self.created_at,
            "result_versions": self.result_versions,
            "resolved": self.resolved,
            "frames": [f.to_dict() for f in self.frames],
            "checkpoints": self.checkpoints,
            "parent_revision": self.parent_revision,
            "replayed_from": self.replayed_from,
            "note": self.note,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Run":
        return cls(
            dataset_id=d["dataset_id"],
            method=d["method"],
            config=d["config"],
            fingerprint=d["fingerprint"],
            revision=int(d["revision"]),
            created_at=d["created_at"],
            result_versions=d["result_versions"],
            resolved=d["resolved"],
            frames=[Frame.from_dict(f) for f in d["frames"]],
            checkpoints=d.get("checkpoints", {}),
            parent_revision=d.get("parent_revision"),
            replayed_from=d.get("replayed_from"),
            note=d.get("note", ""),
            history=d.get("history", []),
        )


GameFrame = Frame


def _snapshot(state: DiagState) -> Tuple[List[float], List[float]]:
    return (
        [round(v, 6) for v in state.skill_mean],
        [round(math.sqrt(max(v, 0.0)), 6) for v in state.skill_var],
    )


def _make_filter(spec: ModelSpec, config: EngineConfig, method: str):
    return (
        ApproxFilter(spec, config)
        if method == "approx"
        else WindowFilter(spec, config)
    )


def _serialize(filter_obj, method: str) -> dict:
    if method == "approx":
        return {"state": filter_obj.checkpoint()}
    return {"states": filter_obj.checkpoint_history()}


def _restore(filter_obj, payload: dict, method: str):
    if method == "approx":
        st = DiagState.from_dict(payload["state"])
        filter_obj.restore(st, st.processed)
    else:
        hist = {int(k): DiagState.from_dict(v) for k, v in payload["states"].items()}
        filter_obj.restore(hist, max(hist))
    return filter_obj


def run_sequence(
    ds: Dataset,
    spec: ModelSpec,
    config: EngineConfig,
    method: str,
    observations: Sequence[Observation],
    resolved: Sequence[Match],
    *,
    start: int = 0,
    filter_obj=None,
    result_versions: Optional[Dict[str, int]] = None,
    frames_prefix: Optional[List[Frame]] = None,
    checkpoints_prefix: Optional[Dict[str, dict]] = None,
) -> Tuple[List[Frame], Dict[str, dict]]:
    """Run or continue a sequential pass and return frames + checkpoints."""
    flt = filter_obj or _make_filter(spec, config, method)
    frames: List[Frame] = list(frames_prefix or [])
    if checkpoints_prefix is not None:
        # Mutate the previous run's checkpoint map in place so windows that
        # reach further back remain continuous after a replay.
        checkpoints: Dict[str, dict] = checkpoints_prefix
    else:
        checkpoints = {}
    if start == 0:
        checkpoints["0"] = _serialize(flt, method)
    versions = result_versions or {}
    for i in range(start, len(observations)):
        obs = observations[i]
        match = resolved[i]
        pre = flt.pre_match_state(obs)
        lam_a, lam_b = _lambdas(pre, obs, spec)
        probs = outcome_probabilities(lam_a, lam_b)
        pre_mean, pre_sd = _snapshot(pre)
        t0 = time.perf_counter()
        if method == "approx":
            state = flt.step(obs, observations)
        else:
            state, _ = flt.step(obs, observations)
        dt = time.perf_counter() - t0
        post_mean, post_sd = _snapshot(state)
        frames.append(
            Frame(
                index=i + 1,
                match_id=match.id,
                day=match.day,
                goals_a=match.score_a,
                goals_b=match.score_b,
                pre_mean=pre_mean,
                pre_sd=pre_sd,
                p_a=round(probs["a"], 5),
                p_draw=round(probs["draw"], 5),
                p_b=round(probs["b"], 5),
                lam_a=round(lam_a, 5),
                lam_b=round(lam_b, 5),
                post_mean=post_mean,
                post_sd=post_sd,
                seconds=round(dt, 6),
                result_version=versions.get(match.id, 0),
            )
        )
        if (i + 1) % CHECKPOINT_EVERY == 0 or i + 1 == len(observations):
            checkpoints[str(i + 1)] = _serialize(flt, method)
    return frames, checkpoints


def _lambdas(state: DiagState, obs: Observation, spec: ModelSpec) -> Tuple[float, float]:
    return lambdas(obs, state.skill_mean, state.combo_mean, spec.base)


class RunStore:
    """Persisted runs for one dataset, keyed by method + config."""

    def __init__(self, root: str, dataset_id: str):
        self.dir = os.path.join(root, "runs", dataset_id)
        os.makedirs(self.dir, exist_ok=True)

    def _paths(self, method: str, fingerprint: str) -> Tuple[str, str]:
        base = os.path.join(self.dir, f"{method}-{fingerprint}")
        return base + ".json", base + ".index.json"

    def list(self) -> List[dict]:
        out = []
        for name in sorted(os.listdir(self.dir)):
            if not name.endswith(".json") or name.endswith(".index.json"):
                continue
            try:
                with open(os.path.join(self.dir, name), encoding="utf-8") as fh:
                    head = json.load(fh)
                out.append(
                    {
                        "method": head["method"],
                        "fingerprint": head["fingerprint"],
                        "revision": head["revision"],
                        "created_at": head["created_at"],
                        "config": head["config"],
                    }
                )
            except Exception:
                continue
        return out

    def load(self, method: str, fingerprint: str) -> Optional[Run]:
        path, _ = self._paths(method, fingerprint)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return Run.from_dict(json.load(fh))

    def save(self, run: Run) -> None:
        path, _ = self._paths(run.method, run.fingerprint)
        os.makedirs(self.dir, exist_ok=True)
        # Unique temp name: two request threads may persist the same run at
        # the same moment; a shared ".tmp" would interleave their bytes.
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(run.to_dict(), fh, ensure_ascii=False)
        os.replace(tmp, path)


def build_run(
    ds: Dataset,
    spec: ModelSpec,
    config: EngineConfig,
    method: str,
    resolved: Sequence[Match],
    observations: Sequence[Observation],
    result_versions: Dict[str, int],
    revision: int = 1,
    parent: Optional[Run] = None,
) -> Run:
    fingerprints = {m.id: result_versions.get(m.id, 0) for m in resolved}
    frames, checkpoints = run_sequence(
        ds,
        spec,
        config,
        method,
        observations,
        resolved,
        result_versions=fingerprints,
    )
    from .model import now_iso

    return Run(
        dataset_id=ds.id,
        method=method,
        config=dataclasses.asdict(config),
        fingerprint=run_fingerprint(ds, method, config),
        revision=revision,
        created_at=now_iso(),
        result_versions=fingerprints,
        resolved=[m.to_dict() for m in resolved],
        frames=frames,
        checkpoints=checkpoints,
        parent_revision=parent.revision if parent else None,
    )


def replay_from(
    ds: Dataset,
    spec: ModelSpec,
    config: EngineConfig,
    method: str,
    resolved: Sequence[Match],
    observations: Sequence[Observation],
    previous: Run,
    corrected_index: int,
) -> Tuple[Run, dict]:
    """Restore the checkpoint before ``corrected_index`` and replay the rest.

    Returns the new run revision plus a small report (which checkpoint was
    used, how many matches were replayed).
    """
    if not previous.checkpoints:
        raise ValueError("previous run has no checkpoints")
    usable = sorted(int(k) for k in previous.checkpoints)
    cp = max((k for k in usable if k <= corrected_index), default=0)
    flt = _make_filter(spec, config, method)
    flt = _restore(flt, previous.checkpoints[str(cp)], method)

    prefix_frames = [f for f in previous.frames if f.index <= cp]
    prefix_checkpoints = {
        k: v for k, v in previous.checkpoints.items() if int(k) <= cp
    }
    # Take versions from the *resolved* matches: a correction has already
    # stamped the new revision number on the corrected row, while the previous
    # run still carries the old one.  Falling back to the previous run would
    # label a corrected frame with the superseded version.
    versions = {
        m.id: int(getattr(m, "result_version", 0)) or previous.result_versions.get(m.id, 0)
        for m in resolved
    }
    from .model import now_iso

    frames, checkpoints = run_sequence(
        ds,
        spec,
        config,
        method,
        observations,
        resolved,
        start=cp,
        filter_obj=flt,
        result_versions=versions,
        frames_prefix=prefix_frames,
        checkpoints_prefix=prefix_checkpoints,
    )
    new_run = Run(
        dataset_id=ds.id,
        method=method,
        config=dataclasses.asdict(config),
        fingerprint=previous.fingerprint,
        revision=previous.revision + 1,
        created_at=now_iso(),
        result_versions=versions,
        resolved=[m.to_dict() for m in resolved],
        frames=frames,
        checkpoints=checkpoints,
        parent_revision=previous.revision,
        replayed_from=cp,
        note=f"更正 {resolved[corrected_index].id} 后，从检查点 {cp} 重放",
        history=list(previous.history)
        + [
            {
                "revision": previous.revision,
                "checkpoint_used": cp,
                "replayed_from_match": resolved[cp].id if cp < len(resolved) else None,
                "corrected_match": resolved[corrected_index].id,
                "corrected_score": [
                    resolved[corrected_index].score_a,
                    resolved[corrected_index].score_b,
                ],
                "replayed_matches": len(observations) - cp,
            }
        ],
    )
    report = {
        "checkpoint_used": cp,
        "replayed_matches": len(observations) - cp,
        "total_matches": len(observations),
        "corrected_index": corrected_index,
    }
    return new_run, report


# --------------------------------------------------------------------------
# Derived views
# --------------------------------------------------------------------------


def player_trajectory(run: Run, player_index: int) -> List[dict]:
    out = []
    for f in run.frames:
        out.append(
            {
                "index": f.index,
                "day": f.day,
                "match_id": f.match_id,
                "mean": f.post_mean[player_index],
                "sd": f.post_sd[player_index],
            }
        )
    return out


def match_prediction(run: Run, match_id: str) -> Optional[dict]:
    for f in run.frames:
        if f.match_id == match_id:
            return f.to_dict()
    return None


def _players_in_frame(run: Run, frame: Frame) -> List[str]:
    rec = run.resolved[frame.index - 1]
    side_a = rec["side_a"] if isinstance(rec, dict) else rec.side_a
    side_b = rec["side_b"] if isinstance(rec, dict) else rec.side_b
    return list(side_a) + list(side_b)


def latest_rows(run: Run, ds: Dataset, spec: ModelSpec) -> List[dict]:
    last = run.frames[-1] if run.frames else None
    pmap = ds.player_map()
    counts = play_counts_from(run.resolved)
    rows = []
    for i, pid in enumerate(spec.player_ids):
        p = pmap[pid]
        mean = last.post_mean[i] if last else 0.0
        sd = last.post_sd[i] if last else 0.0
        plays = counts.get(pid, 0)
        rows.append(
            {
                "player_id": pid,
                "name": p.name,
                "club": p.club,
                "role": p.note,
                "mean": mean,
                "sd": sd,
                "lo90": round(mean - 1.645 * sd, 5),
                "hi90": round(mean + 1.645 * sd, 5),
            }
        )
    return rows


def play_counts(resolved: Sequence[Match]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for m in resolved:
        for p in m.side_a + m.side_b:
            counts[p] = counts.get(p, 0) + 1
    return counts


def play_counts_from(resolved: Sequence[object]) -> Dict[str, int]:
    """Works for both Match objects and their serialized dicts."""
    counts: Dict[str, int] = {}
    for m in resolved:
        if isinstance(m, dict):
            players = list(m["side_a"]) + list(m["side_b"])
        else:
            players = list(m.side_a) + list(m.side_b)
        for p in players:
            counts[p] = counts.get(p, 0) + 1
    return counts
